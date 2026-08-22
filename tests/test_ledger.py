import pytest

from bgconvertor.llm.ledger import BudgetExceeded, Ledger, estimate_cost


def test_estimate_cost():
    # Sonnet 5: $3/M in, $15/M out
    assert estimate_cost("claude-sonnet-5", 1_000_000, 0) == pytest.approx(3.0)
    assert estimate_cost("claude-sonnet-5", 0, 1_000_000) == pytest.approx(15.0)
    assert estimate_cost("claude-haiku-4-5", 2000, 1000, batch=True) == pytest.approx(
        (2000 * 1 + 1000 * 5) / 1e6 * 0.5
    )
    with pytest.raises(ValueError, match="no price entry"):
        estimate_cost("unknown-model", 1, 1)


def test_ledger_records_and_persists(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path=path, max_cost_usd=10.0, max_calls=100)
    led.record("repair", "claude-sonnet-5", 2000, 500, page=9)
    assert led.total_calls == 1
    assert led.total_cost_usd > 0

    # A new Ledger over the same file resumes the running totals.
    led2 = Ledger(path=path, max_cost_usd=10.0, max_calls=100)
    assert led2.total_calls == 1
    assert led2.total_cost_usd == pytest.approx(led.total_cost_usd)


def test_budget_enforced(tmp_path):
    led = Ledger(path=tmp_path / "l.jsonl", max_cost_usd=0.001, max_calls=100)
    led.record("repair", "claude-sonnet-5", 100_000, 10_000)
    with pytest.raises(BudgetExceeded, match="budget reached"):
        led.check_budget()


def test_call_limit_enforced(tmp_path):
    led = Ledger(path=tmp_path / "l.jsonl", max_cost_usd=99.0, max_calls=1)
    led.record("classify", "claude-haiku-4-5", 10, 10)
    with pytest.raises(BudgetExceeded, match="call limit"):
        led.check_budget()


def test_cached_call_costs_nothing(tmp_path):
    led = Ledger(path=tmp_path / "l.jsonl", max_cost_usd=1.0, max_calls=10)
    cost = led.record("repair", "claude-sonnet-5", 5000, 5000, cached=True)
    assert cost == 0.0
    assert led.total_cost_usd == 0.0
