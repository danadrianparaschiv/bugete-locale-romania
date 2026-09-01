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
    fallback_model: str | None = None  # full-page transcription; None -> repair_model
    premium_model: str | None = None  # second attempt only after cheap validation failure
    reasoning: str | None = None  # compat: reasoning_effort — thinking-ul se facturează


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
        "OpenAI — capabilitate de vârf, alternativă serioasă pentru reparare",
        reasoning="low"),
    "openai:gpt-5-mini": Preset(
        "openai", "gpt-5-mini", "gpt-5-mini",
        "OPENAI_API_KEY", "https://api.openai.com/v1",
        "OpenAI economic — clasa Haiku, viziune solidă la preț mic",
        reasoning="low"),
    "google:gemini-3.1-pro": Preset(
        "google", "gemini-3.1-pro-preview", "gemini-3.6-flash",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "Google — printre cele mai bune la OCR de documente scanate"),
    "google:gemini-3.6-flash": Preset(
        "google", "gemini-3.6-flash", "gemini-3.6-flash",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "Google economic — OCR foarte bun la o fracțiune din preț",
        reasoning="low"),
    "mistral:mistral-medium-3": Preset(
        "mistral", "mistral-medium-latest", "mistral-small-latest",
        "MISTRAL_API_KEY", "https://api.mistral.ai/v1",
        "Mistral — furnizor european, rezidență a datelor în UE"),
    "qwen:qwen3-vl-30b": Preset(
        "qwen", "qwen/qwen3-vl-30b-a3b-thinking", "qwen/qwen3-vl-30b-a3b-thinking",
        "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
        "Qwen (via OpenRouter) — greutăți deschise, fără dependență de furnizor"),
    # combinația sugerată de evaluarea pe corpus (docs/eval-modele.md):
    # reparare ieftină pe Gemini Flash, transcriere de pagină pe Sonnet
    "mixt:flash+sonnet": Preset(
        "mixt", "gemini-3.6-flash", "gemini-3.6-flash",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "mixt — Gemini Flash întâi; Sonnet 5 numai pentru escaladări cu randament mare",
        premium_model="claude-sonnet-5", reasoning="low"),
}

# model -> (api_key_env, base_url) pentru rutarea per apel; modelele claude-*
# merg mereu prin SDK-ul nativ Anthropic și nu apar aici
MODEL_ROUTES: dict[str, tuple[str, str]] = {
    m: (p.api_key_env, p.base_url)
    for p in PRESETS.values() if p.base_url
    for m in (p.repair_model, p.cell_model, p.fallback_model, p.premium_model)
    if m and not m.startswith("claude-")
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
    config.llm.fallback_model = p.fallback_model
    config.llm.premium_model = p.premium_model
    config.llm.reasoning_effort = p.reasoning
    config.llm.api_key_env = p.api_key_env
    config.llm.base_url = p.base_url
    return p
