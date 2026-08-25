"""Batch mode: the cache short-circuit answers without any API construction."""

from pydantic import BaseModel

from bgconvertor.config import RunConfig
from bgconvertor.llm.batch import batch_structured
from bgconvertor.llm.client import LLMClient
from bgconvertor.llm.ledger import Ledger


class Out(BaseModel):
    ok: bool


def test_batch_all_cached_needs_no_api(tmp_path):
    config = RunConfig()
    ledger = Ledger(path=tmp_path / "l.jsonl", max_cost_usd=1.0, max_calls=10)
    client = LLMClient(config, ledger, tmp_path / "cache")
    client.cache_store("repair", "prompt-x", Out, Out(ok=True), 100, 10)

    results = batch_structured(client, [{
        "key": "a", "purpose": "repair", "prompt": "prompt-x",
        "image": None, "output_model": Out,
    }])
    assert results["a"].ok is True
    assert client._client is None  # no SDK constructed
    assert ledger.total_calls == 1 and ledger.total_cost_usd == 0.0


def test_batch_budget_planner_blocks_before_api_construction(tmp_path):
    config = RunConfig()
    ledger = Ledger(path=tmp_path / "l.jsonl", max_cost_usd=0.001, max_calls=10)
    client = LLMClient(config, ledger, tmp_path / "cache")

    results = batch_structured(client, [{
        "key": "too-large",
        "purpose": "repair",
        "prompt": "read rows",
        "image": None,
        "output_model": Out,
        "max_tokens": 12000,
    }])

    assert isinstance(results["too-large"], Exception)
    assert client._client is None
    assert ledger.total_cost_usd == 0.0
