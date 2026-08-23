"""Run configuration.

A single RunConfig object flows through every stage. It is serialized into
each run directory and hashed into every cache key, so any stored artifact
can answer "what settings produced you?" and a settings change invalidates
exactly the stages it affects.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root(start: Path | None = None) -> Path:
    """The repo root, found by markers — so the CLI works from any cwd
    (e.g. running `bgconvertor convert budget_file.pdf` inside a city
    folder of the data/ tree)."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "pyproject.toml").exists() or (cand / ".git").exists() or (
            cand / "reference" / "nomenclator"
        ).is_dir():
            return cand
    return p


class LLMConfig(BaseModel):
    preset: str | None = None  # "vendor:model" key from llm/presets.py
    vendor: str = "anthropic"  # anthropic | openai | google | mistral | qwen
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str | None = None  # OpenAI-compatible endpoint for non-Anthropic
    repair_model: str = "claude-sonnet-5"
    cell_model: str = "claude-haiku-4-5"  # transcription-only cell recovery
    fallback_model: str | None = None  # full-page transcription; None -> repair_model
    call_deadline_s: int = 1800  # hard per-call wait bound in worker pools
    batch: bool = False  # Batch API (-50%) for unattended repair/fallback runs
    classify_model: str = "claude-haiku-4-5"
    mode: str = "off"  # off | repair | full — development default is off
    max_cost_usd: float = 1.00  # hard budget per run; raising it is a conscious act
    max_calls: int = 2000  # the dollar budget is the primary governor
    concurrency: int = 4  # parallel LLM calls (network-bound; thread pool)
    prompt_version: str = "v2"  # bumped whenever a prompt file changes


class RunConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BGC_", env_nested_delimiter="__")

    runs_dir: Path = Field(default_factory=lambda: project_root() / "runs")
    reference_dir: Path = Field(default_factory=lambda: project_root() / "reference/nomenclator")

    # rendering
    render_scale: float = 2.0  # pypdfium2 scale factor (~144 dpi)

    # OCR / docling (used from Phase 2; part of config now so hashes are stable)
    ocr_langs: list[str] = ["ro", "en"]
    ocr_engine: str = "rapidocr"
    tableformer_mode: str = "accurate"
    # Copier PDFs: extract from the embedded text layer instead of re-OCR.
    # Measured on Bacau: 3x faster but -8pp validated cleanliness (the copier
    # text is corrupted) — enable only for files with a GOOD text layer.
    prefer_native_text: bool = False
    # Whiten saturated (stamp-ink) pixels before OCR. Off by default: flipping
    # it does NOT invalidate cached OCR (the ocr stage keys on nothing), so
    # enable it only together with a deliberate re-OCR of the target file.
    stamp_filter: bool = False
    docling_cell_matching: bool = True

    llm: LLMConfig = LLMConfig()

    fail_fast: bool = False
    debug_artifacts: bool = False

    # Bumped whenever extraction-mapping code changes semantics; invalidates
    # the cheap mapping stage without touching cached OCR.
    extract_version: str = "29"

    # Which config fields each stage's cache key depends on. A field change
    # invalidates only the stages that list it.
    STAGE_FIELDS: dict[str, list[str]] = {
        "profile": [],
        "orient": [],
        "ocr": ["render_scale", "ocr_langs", "ocr_engine", "tableformer_mode", "docling_cell_matching"],
        "classify": ["render_scale", "llm.classify_model", "llm.prompt_version", "llm.mode"],
        # extract derives from ocr output, so it inherits ocr's fields too
        "extract": [
            "extract_version", "render_scale", "ocr_langs", "ocr_engine",
            "tableformer_mode", "docling_cell_matching",
        ],
        "llm": ["render_scale", "llm.repair_model", "llm.prompt_version"],
        "llm_extract": [
            "render_scale", "llm.repair_model", "llm.fallback_model",
            "llm.prompt_version",
        ],
    }

    def _field_value(self, dotted: str) -> Any:
        obj: Any = self
        for part in dotted.split("."):
            obj = getattr(obj, part)
        return obj

    def stage_hash(self, stage: str) -> str:
        """Stable short hash of the config fields a stage depends on."""
        fields = self.STAGE_FIELDS.get(stage, [])
        payload = {f: self._field_value(f) for f in fields}
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def dump(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))
