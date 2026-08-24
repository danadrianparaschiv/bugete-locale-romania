import threading

import pytest

from bgconvertor.llm.ledger import MODEL_PRICES, BudgetExceeded, Ledger, estimate_cost
from bgconvertor.llm.presets import PRESETS


def _ledger(tmp_path, cap=1.00):
    return Ledger(path=tmp_path / "l.jsonl", max_cost_usd=cap, max_calls=100)


def test_reserve_blocks_concurrent_overshoot(tmp_path):
    led = _ledger(tmp_path, cap=1.00)
    # două rezervări mari concurente: a doua trebuie refuzată, nu suprapusă
    led.reserve("claude-sonnet-5", 10_000, 40_000)  # ~ $0.63
    with pytest.raises(BudgetExceeded):
        led.reserve("claude-sonnet-5", 10_000, 40_000)


def test_release_frees_reservation(tmp_path):
    led = _ledger(tmp_path, cap=1.00)
    r = led.reserve("claude-sonnet-5", 10_000, 40_000)
    led.release(r)
    led.reserve("claude-sonnet-5", 10_000, 40_000)  # trece după eliberare


def test_reserve_thread_safety(tmp_path):
    led = _ledger(tmp_path, cap=0.10)
    granted = []
    def worker():
        try:
            granted.append(led.reserve("claude-haiku-4-5", 5_000, 15_000))  # ~$0.08
        except BudgetExceeded:
            pass
    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(granted) == 1  # doar una încape sub $0.10


def test_hidden_thinking_recorded(tmp_path):
    led = _ledger(tmp_path, cap=5.00)
    led.record("repair", "gemini-3.6-flash", 100, 175, visible_output_tokens=3)
    import json
    rec = json.loads((tmp_path / "l.jsonl").read_text().splitlines()[0])
    assert rec["output_tokens"] == 175 and rec["visible_output_tokens"] == 3


def test_every_preset_model_is_priced_and_flash_has_low_reasoning():
    for p in PRESETS.values():
        for m in (p.repair_model, p.cell_model, p.fallback_model):
            if m:
                assert m in MODEL_PRICES, m
    assert PRESETS["google:gemini-3.6-flash"].reasoning == "low"
    assert estimate_cost("gemini-3.6-flash", 1_000_000, 0) == MODEL_PRICES["gemini-3.6-flash"][0]


def test_billed_output_tokens_math():
    from bgconvertor.llm.client import _billed_output_tokens
    assert _billed_output_tokens(22, 3, 175) == 153       # thinking ascuns
    assert _billed_output_tokens(22, 3, None) == 3        # fără total raportat
    assert _billed_output_tokens(22, 30, 40) == 30        # total inconsistent
