"""Predefined model presets, keyed "vendor:model".

A preset picks the (repair_model, cell_model) pair plus how to reach the
vendor. Anthropic uses the native SDK; every other vendor is reached
through its OpenAI-compatible endpoint, so one client code path serves all.

The arithmetic acceptance gate makes this safe: a weaker model repairs
fewer groups but can never corrupt data — repairs only apply when the
re-read makes the sums hold.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PRESET = "anthropic:claude-sonnet-5"


@dataclass(frozen=True)
class Preset:
    vendor: str
    repair_model: str
    cell_model: str
    api_key_env: str
    base_url: str | None  # None -> native Anthropic SDK
    description: str  # shown by `bgconvertor models` (user-facing, Romanian)


def _anthropic(repair: str, cell: str, desc: str) -> Preset:
    return Preset("anthropic", repair, cell, "ANTHROPIC_API_KEY", None, desc)


PRESETS: dict[str, Preset] = {
    # ---- Anthropic ladder (default vendor) ----
    "anthropic:claude-fable-5": _anthropic(
        "claude-fable-5", "claude-sonnet-5",
        "maxim — scanările cele mai grele, cost maxim per apel"),
    "anthropic:claude-opus-5": _anthropic(
        "claude-opus-5", "claude-sonnet-5",
        "premium — transcriere vizibil mai bună pe scanări dificile"),
    "anthropic:claude-sonnet-5": _anthropic(
        "claude-sonnet-5", "claude-haiku-4-5",
        "echilibrat (implicit) — perechea validată pe întregul corpus"),
    "anthropic:claude-opus-4-5": _anthropic(
        "claude-opus-4-5", "claude-haiku-4-5",
        "valoare — capabilitate aproape premium la preț mediu"),
    "anthropic:claude-sonnet-4-5": _anthropic(
        "claude-sonnet-4-5", "claude-haiku-4-5",
        "economic — generația anterioară, suficient pe scanări bune"),
    "anthropic:claude-haiku-4-5": _anthropic(
        "claude-haiku-4-5", "claude-haiku-4-5",
        "buget — cel mai ieftin; rată de reparare mai mică, zero risc de date"),
    # ---- Alți furnizori (endpoint compatibil OpenAI) ----
    "openai:gpt-5.1": Preset(
        "openai", "gpt-5.1", "gpt-5-mini",
        "OPENAI_API_KEY", "https://api.openai.com/v1",
        "OpenAI — capabilitate de vârf, alternativă serioasă pentru reparare"),
    "openai:gpt-5-mini": Preset(
        "openai", "gpt-5-mini", "gpt-5-mini",
        "OPENAI_API_KEY", "https://api.openai.com/v1",
        "OpenAI economic — clasa Haiku, viziune solidă la preț mic"),
    "google:gemini-3.1-pro": Preset(
        "google", "gemini-3.1-pro-preview", "gemini-3.6-flash",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "Google — printre cele mai bune la OCR de documente scanate"),
    "google:gemini-3.6-flash": Preset(
        "google", "gemini-3.6-flash", "gemini-3.6-flash",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "Google economic — OCR foarte bun la o fracțiune din preț"),
    "mistral:mistral-medium-3": Preset(
        "mistral", "mistral-medium-latest", "mistral-small-latest",
        "MISTRAL_API_KEY", "https://api.mistral.ai/v1",
        "Mistral — furnizor european, rezidență a datelor în UE"),
    "qwen:qwen3-vl": Preset(
        "qwen", "qwen3-vl-plus", "qwen3-vl-plus",
        "DASHSCOPE_API_KEY",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "Qwen — greutăți deschise, opțiune fără dependență de furnizor"),
}


def resolve(key: str) -> Preset:
    try:
        return PRESETS[key]
    except KeyError:
        options = "\n  ".join(sorted(PRESETS))
        raise ValueError(
            f"preset necunoscut {key!r}; opțiuni:\n  {options}"
        ) from None


def apply(config, key: str) -> Preset:
    """Set the LLM config fields a preset governs; returns the preset."""
    p = resolve(key)
    config.llm.preset = key
    config.llm.vendor = p.vendor
    config.llm.repair_model = p.repair_model
    config.llm.cell_model = p.cell_model
    config.llm.classify_model = p.cell_model
    config.llm.api_key_env = p.api_key_env
    config.llm.base_url = p.base_url
    return p
