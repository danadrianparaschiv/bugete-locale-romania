"""LLMClient cache/cassette behavior — no network involved."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from bgconvertor.config import RunConfig
from bgconvertor.llm.client import LLMClient
from bgconvertor.llm.ledger import BudgetExceeded, Ledger


class Verdict(BaseModel):
    ok: bool
    detail: str = ""


def _client(tmp_path, max_cost=1.0):
    config = RunConfig()
    ledger = Ledger(path=tmp_path / "ledger.jsonl", max_cost_usd=max_cost, max_calls=10)
    return LLMClient(config, ledger, tmp_path / "cache"), ledger, config


def _cache_key(config, model, prompt, output_model):
    return hashlib.sha256(
        b"|".join([
            model.encode(),
            config.llm.prompt_version.encode(),
            prompt.encode(),
            b"",
            json.dumps(output_model.model_json_schema(), sort_keys=True).encode(),
        ])
    ).hexdigest()[:32]


def test_cache_replay_without_api(tmp_path):
    """A cached response replays offline: no API client is ever constructed."""
    client, ledger, config = _client(tmp_path)
    key = _cache_key(config, config.llm.repair_model, "citeste celula", Verdict)
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "cache" / f"{key}.json").write_text(json.dumps({
        "purpose": "repair", "model": config.llm.repair_model,
        "prompt_version": config.llm.prompt_version, "prompt": "citeste celula",
        "has_image": False, "input_tokens": 1000, "output_tokens": 50,
        "output": {"ok": True, "detail": "58.295"},
    }))

    result = client.structured("repair", "citeste celula", Verdict)
    assert result.ok and result.detail == "58.295"
    assert client._client is None  # the anthropic SDK was never touched
    assert ledger.total_calls == 1
    assert ledger.total_cost_usd == 0.0  # cached call costs nothing


def test_budget_blocks_uncached_call(tmp_path):
    """With the budget exhausted, an uncached call raises before any API use."""
    client, ledger, config = _client(tmp_path, max_cost=0.001)
    ledger.record("repair", config.llm.repair_model, 100_000, 10_000)  # spend it
    with pytest.raises(BudgetExceeded):
        client.structured("repair", "alt prompt necachat", Verdict)
    assert client._client is None


def test_prompt_version_invalidates_cache(tmp_path):
    client, ledger, config = _client(tmp_path)
    key_v = _cache_key(config, config.llm.repair_model, "p", Verdict)
    config.llm.prompt_version = "v999"
    key_v999 = _cache_key(config, config.llm.repair_model, "p", Verdict)
    assert key_v != key_v999


def test_larger_retry_must_reserve_again_before_second_api_call(tmp_path):
    client, ledger, _config = _client(tmp_path, max_cost=0.02)

    class Messages:
        def __init__(self):
            self.calls = 0

        def parse(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                parsed_output=None,
                usage=SimpleNamespace(input_tokens=100, output_tokens=500),
                stop_reason="max_tokens",
            )

    messages = Messages()
    client._client = SimpleNamespace(messages=messages)

    with pytest.raises(BudgetExceeded, match="would be exceeded"):
        client.structured("repair", "read one cell", Verdict, max_tokens=512)

    assert messages.calls == 1
    assert ledger.total_calls == 1
    assert ledger.run_cost_usd <= ledger.max_cost_usd


def test_failed_second_attempt_releases_its_reservation(tmp_path):
    client, ledger, _config = _client(tmp_path, max_cost=1.0)

    class Messages:
        def __init__(self):
            self.calls = 0

        def parse(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                parsed_output=None,
                usage=SimpleNamespace(input_tokens=10, output_tokens=10),
                stop_reason="end_turn",
            )

    messages = Messages()
    client._client = SimpleNamespace(messages=messages)

    with pytest.raises(RuntimeError, match="no structured output"):
        client.structured("repair", "read one cell", Verdict, max_tokens=512)

    assert messages.calls == 2
    assert ledger.remaining_cost_usd == pytest.approx(
        ledger.max_cost_usd - ledger.run_cost_usd
    )
    assert ledger.remaining_calls == ledger.max_calls - 2
