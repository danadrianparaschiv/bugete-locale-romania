"""Claude API client with the §6 guardrails built in.

Every call goes through:
  1. the response cache (identical calls are never re-paid; the cache files
     double as replay cassettes for tests and offline development),
  2. the budget check (BudgetExceeded aborts LLM passes, never the pipeline),
  3. the ledger (JSONL record per call with tokens and cost).

Structured outputs only: callers pass a pydantic model and get a validated
instance back — no free-text parsing anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..config import RunConfig
from .ledger import Ledger

log = logging.getLogger("bgc.llm")

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, config: RunConfig, ledger: Ledger, cache_dir: Path):
        self.config = config
        self.ledger = ledger
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._compat: dict[str, object] = {}  # base_url -> OpenAI-compat client

    def _api(self):
        if self._client is None:
            import anthropic

            # long streamed transcriptions can exceed the default 10-min
            # timeout mid-read; give the read window real headroom
            self._client = anthropic.Anthropic(timeout=1500.0)
        return self._client

    def _route(self, model: str) -> tuple[str, str] | None:
        """(api_key_env, base_url) for a compat model; None -> native Anthropic."""
        if model.startswith("claude-"):
            return None
        from .presets import MODEL_ROUTES

        route = MODEL_ROUTES.get(model)
        if route is None and self.config.llm.base_url:
            route = (self.config.llm.api_key_env, self.config.llm.base_url)
        if route is None:
            raise RuntimeError(
                f"nu știu pe ce endpoint să rutez modelul {model!r} — "
                "adăugați-l într-un preset (llm/presets.py) sau setați "
                "BGC_LLM__BASE_URL/BGC_LLM__API_KEY_ENV"
            )
        return route

    def _api_compat(self, model: str):
        """OpenAI-compatible client for non-Anthropic models, cached per endpoint.

        Bounded hard (timeout + one SDK retry): a hung request must never
        stall a run for hours — the pool-level deadline is the backstop."""
        key_env, base_url = self._route(model)
        if base_url not in self._compat:
            import os

            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "furnizorii non-Anthropic necesită pachetul openai: "
                    "`uv add openai` sau `uv sync --extra vendors`"
                ) from None
            key = os.environ.get(key_env)
            if not key:
                raise RuntimeError(
                    f"lipsește {key_env} din mediu/.env (necesară pentru {model})"
                )
            self._compat[base_url] = OpenAI(
                api_key=key, base_url=base_url, timeout=600.0, max_retries=1,
            )
        return self._compat[base_url]

    def _cache_key(self, purpose, prompt, output_model, model, image_bytes: bytes) -> Path:
        key = hashlib.sha256(
            b"|".join([
                model.encode(),
                self.config.llm.prompt_version.encode(),
                prompt.encode(),
                image_bytes,
                json.dumps(output_model.model_json_schema(), sort_keys=True).encode(),
            ])
        ).hexdigest()[:32]
        return self.cache_dir / f"{key}.json"

    def cache_lookup(self, purpose, prompt, output_model, model=None, image=None, page=None):
        model = model or self.config.llm.repair_model
        image_bytes = _png_bytes(image) if image is not None else b""
        cache_file = self._cache_key(purpose, prompt, output_model, model, image_bytes)
        if not cache_file.exists():
            return None
        record = json.loads(cache_file.read_text())
        self.ledger.record(
            purpose, model, record["input_tokens"], record["output_tokens"],
            page=page, cached=True,
        )
        return output_model.model_validate(record["output"])

    def cache_store(self, purpose, prompt, output_model, parsed,
                    input_tokens: int, output_tokens: int, model=None, image=None) -> None:
        model = model or self.config.llm.repair_model
        image_bytes = _png_bytes(image) if image is not None else b""
        cache_file = self._cache_key(purpose, prompt, output_model, model, image_bytes)
        cache_file.write_text(json.dumps({
            "purpose": purpose,
            "model": model,
            "prompt_version": self.config.llm.prompt_version,
            "prompt": prompt,
            "has_image": image is not None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "output": parsed.model_dump(mode="json"),
        }, ensure_ascii=False, indent=2))

    def structured(
        self,
        purpose: str,
        prompt: str,
        output_model: type[T],
        model: str | None = None,
        image=None,  # PIL image or None
        page: int | None = None,
        max_tokens: int = 4096,
    ) -> T:
        model = model or self.config.llm.repair_model
        cached = self.cache_lookup(purpose, prompt, output_model, model=model, image=image, page=page)
        if cached is not None:
            return cached
        image_bytes = _png_bytes(image) if image is not None else b""

        self.ledger.check_budget()

        content: list[dict] = []
        if image is not None:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(image_bytes).decode(),
                },
            })
        content.append({"type": "text", "text": prompt})

        t0 = time.time()
        parsed: T | None = None
        in_tok = out_tok = 0
        stop: str | None = None
        for attempt in (1, 2):
            got_response = False
            try:
                if not model.startswith("claude-"):
                    payload: list[dict] = []
                    if image is not None:
                        payload.append({
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,"
                                          + base64.standard_b64encode(image_bytes).decode()},
                        })
                    payload.append({"type": "text", "text": prompt})
                    response = self._api_compat(model).chat.completions.create(
                        model=model,
                        max_completion_tokens=max_tokens,
                        messages=[{"role": "user", "content": payload}],
                        response_format={"type": "json_schema", "json_schema": {
                            "name": output_model.__name__,
                            "strict": True,
                            "schema": _strict_schema(output_model, require_all=True),
                        }},
                    )
                    choice = response.choices[0]
                    in_tok = response.usage.prompt_tokens
                    out_tok = response.usage.completion_tokens
                    stop = ("max_tokens" if choice.finish_reason == "length"
                            else choice.finish_reason)
                    got_response = True
                    text = choice.message.content or ""
                    parsed = _lenient_validate(output_model, text) if text else None
                elif max_tokens > 16000:
                    # the SDK refuses long non-streaming requests — stream and
                    # validate the final JSON against the schema ourselves
                    with self._api().messages.stream(
                        model=model,
                        max_tokens=max_tokens,
                        messages=[{"role": "user", "content": content}],
                        output_config={"format": {
                            "type": "json_schema",
                            "schema": _strict_schema(output_model),
                        }},
                    ) as stream:
                        response = stream.get_final_message()
                    in_tok = response.usage.input_tokens
                    out_tok = response.usage.output_tokens
                    stop = response.stop_reason
                    got_response = True
                    text = "".join(
                        b.text for b in response.content if getattr(b, "type", "") == "text"
                    )
                    parsed = _lenient_validate(output_model, text) if text else None
                else:
                    response = self._api().messages.parse(
                        model=model,
                        max_tokens=max_tokens,
                        messages=[{"role": "user", "content": content}],
                        output_format=output_model,
                    )
                    parsed = response.parsed_output
                    in_tok = response.usage.input_tokens
                    out_tok = response.usage.output_tokens
                    stop = response.stop_reason
                    got_response = True
            except Exception as exc:
                # truncated JSON etc. — treat like an empty output and retry bigger
                log.warning("%s call p%s parse error (attempt %d): %r",
                            purpose, page, attempt, exc)
                parsed = None
                stop = None
            if got_response:
                cost = self.ledger.record(
                    purpose, model, in_tok, out_tok,
                    page=page, duration_ms=int((time.time() - t0) * 1000),
                )
            if parsed is not None:
                break
            log.warning("%s call p%s returned no structured output (attempt %d, "
                        "stop_reason=%s)", purpose, page, attempt, stop)
            # adaptive thinking spends from max_tokens: a max_tokens stop means
            # the cap was too small for thinking + JSON — retry with room
            if stop == "max_tokens" or not got_response:
                max_tokens = min(64000, max_tokens * 4)
        if parsed is None:
            raise RuntimeError(f"no structured output after retry ({purpose}, p{page})")
        log.debug("%s call: %s p%s $%.4f", purpose, model, page, cost)

        self.cache_store(
            purpose, prompt, output_model, parsed,
            in_tok, out_tok, model=model, image=image,
        )
        return parsed


def _lenient_validate(output_model, text: str):
    """Validate model output, tolerating prose/markdown wrapping.

    A schema miss on an already-paid call is wasted money: before giving
    up, strip ``` fences and try the largest {...} / [...] slice. Raises
    only when no candidate validates (caller treats it as a parse error)."""
    text = text.strip()
    try:
        return output_model.model_validate_json(text)
    except Exception:
        pass
    candidates = []
    if "```" in text:
        for chunk in text.split("```")[1::2]:
            candidates.append(chunk.removeprefix("json").strip())
    for opener, closer in ("{}", "[]"):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            return output_model.model_validate_json(cand)
        except Exception:
            continue
    # let the caller's retry logic see the original failure
    return output_model.model_validate_json(text)


def _strict_schema(output_model, require_all: bool = False) -> dict:
    """Pydantic JSON schema with additionalProperties:false on every object
    (the API's json_schema output format requires it explicitly).

    require_all: OpenAI-style strict mode additionally demands that every
    object lists ALL its properties as required."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
                if require_all and node.get("properties"):
                    node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    schema = output_model.model_json_schema()
    walk(schema)
    return schema


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
