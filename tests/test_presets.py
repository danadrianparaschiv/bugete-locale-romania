import pytest

from bgconvertor.config import RunConfig
from bgconvertor.llm.ledger import MODEL_PRICES
from bgconvertor.llm.presets import DEFAULT_PRESET, PRESETS, apply, resolve


def test_all_presets_are_vendor_keyed_and_priced():
    for key, p in PRESETS.items():
        vendor, _, model = key.partition(":")
        assert vendor == p.vendor and model
        # the ledger must be able to price every model a preset can select
        assert p.repair_model in MODEL_PRICES, p.repair_model
        assert p.cell_model in MODEL_PRICES, p.cell_model
        if p.fallback_model:
            assert p.fallback_model in MODEL_PRICES, p.fallback_model
        if p.premium_model:
            assert p.premium_model in MODEL_PRICES, p.premium_model
        # non-Anthropic vendors go through the OpenAI-compatible endpoint
        assert (p.base_url is None) == (p.vendor == "anthropic")


def test_default_preset_matches_config_defaults():
    p = PRESETS[DEFAULT_PRESET]
    cfg = RunConfig()
    assert p.repair_model == cfg.llm.repair_model
    assert p.cell_model == cfg.llm.cell_model
    assert cfg.llm.vendor == "anthropic"


def test_apply_sets_llm_fields():
    cfg = RunConfig()
    p = apply(cfg, "google:gemini-3.6-flash")
    assert cfg.llm.vendor == "google"
    assert cfg.llm.repair_model == p.repair_model == "gemini-3.6-flash"
    assert cfg.llm.api_key_env == "GEMINI_API_KEY"
    assert cfg.llm.base_url and "openai" in cfg.llm.base_url


def test_unknown_preset_lists_options():
    with pytest.raises(ValueError, match="anthropic:claude-sonnet-5"):
        resolve("acme:supermodel")


def test_mixed_preset_sets_fallback_and_routes():
    cfg = RunConfig()
    apply(cfg, "mixt:flash+sonnet")
    assert cfg.llm.fallback_model is None  # page fallback inherits the cheap repair model
    assert cfg.llm.repair_model == "gemini-3.6-flash"
    assert cfg.llm.premium_model == "claude-sonnet-5"
    # compat models must be routable; claude models never appear in routes
    from bgconvertor.llm.presets import MODEL_ROUTES
    assert "gemini-3.6-flash" in MODEL_ROUTES
    assert not any(m.startswith("claude-") for m in MODEL_ROUTES)
    # applying a non-mixed preset clears the fallback slot
    apply(cfg, "anthropic:claude-sonnet-5")
    assert cfg.llm.fallback_model is None
    assert cfg.llm.premium_model is None


def test_fallback_model_changes_llm_extract_hash():
    a, b, c = RunConfig(), RunConfig(), RunConfig()
    b.llm.fallback_model = "claude-sonnet-5"
    c.llm.premium_model = "claude-sonnet-5"
    assert a.stage_hash("llm_extract") != b.stage_hash("llm_extract")
    assert a.stage_hash("llm_extract") != c.stage_hash("llm_extract")
    assert a.stage_hash("llm") == b.stage_hash("llm")
