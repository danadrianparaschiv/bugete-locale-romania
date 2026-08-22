from pathlib import Path

import pytest

from bgconvertor.config import RunConfig
from bgconvertor.orchestrator import parse_pages, run_stage
from bgconvertor.runstore import RunStore


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    config = RunConfig(runs_dir=tmp_path / "runs")
    return RunStore(config, pdf)


def test_put_get_roundtrip(store: RunStore):
    store.put("extract", 3, {"rows": [1, 2]})
    assert store.get("extract", 3) == {"rows": [1, 2]}
    assert store.get("extract", 4) is None


def test_config_change_invalidates_only_dependent_stage(store: RunStore):
    store.put("profile", 1, {"a": 1})
    store.put("ocr", 1, {"b": 2})
    store.put("extract", 1, {"c": 3})
    store.config.ocr_engine = "tesseract"  # ocr + derived extract depend on this
    assert store.get("profile", 1) == {"a": 1}
    assert store.get("ocr", 1) is None
    assert store.get("extract", 1) is None


def test_failure_recorded_and_cleared(store: RunStore):
    try:
        raise ValueError("boom")
    except ValueError as exc:
        store.record_failure("extract", 7, exc)
    assert store.failures("extract")[0]["page"] == 7
    store.put("extract", 7, {"ok": True})
    assert store.failures("extract") == []


def test_different_pdf_same_stem_refused(tmp_path: Path):
    config = RunConfig(runs_dir=tmp_path / "runs")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 one")
    RunStore(config, pdf)
    pdf.write_bytes(b"%PDF-1.4 two - different content, same name")
    with pytest.raises(RuntimeError, match="different PDF"):
        RunStore(config, pdf)


def test_run_stage_fail_soft_and_resume(store: RunStore):
    calls: list[int] = []

    def fn(page: int):
        calls.append(page)
        if page == 2:
            raise RuntimeError("page 2 broken")
        return {"page": page}

    s1 = run_stage(store, "extract", [1, 2, 3], fn, show_progress=False)
    assert (s1.ok, s1.failed, s1.cached) == ([1, 3], [2], [])
    # Resume: only the failed page is retried.
    calls.clear()
    s2 = run_stage(store, "extract", [1, 2, 3], fn, show_progress=False)
    assert calls == [2]
    assert (s2.cached, s2.failed) == ([1, 3], [2])


def test_run_stage_fail_fast(store: RunStore):
    store.config.fail_fast = True
    with pytest.raises(RuntimeError, match="broken"):
        run_stage(
            store, "extract", [1, 2], lambda p: (_ for _ in ()).throw(RuntimeError("broken")),
            show_progress=False,
        )


def test_parse_pages():
    assert parse_pages(None, 5) == [1, 2, 3, 4, 5]
    assert parse_pages("1-3", 10) == [1, 2, 3]
    assert parse_pages("9,31,2-4", 40) == [2, 3, 4, 9, 31]
    assert parse_pages("38-", 40) == [38, 39, 40]
    assert parse_pages("1-99", 5) == [1, 2, 3, 4, 5]
    with pytest.raises(ValueError):
        parse_pages("77", 5)


def test_store_key_corpus_tree_vs_flat(tmp_path, monkeypatch):
    from bgconvertor.runstore import store_key

    monkeypatch.chdir(tmp_path)
    flat = tmp_path / "budget_file_ab.pdf"
    assert store_key(flat) == "budget_file_ab"
    tree = tmp_path / "data/2026/01-alba/1017-alba-iulia/budget_file.pdf"
    assert store_key(tree) == "2026-01-alba-1017-alba-iulia"
    named = tmp_path / "data/2026/01-alba/1017-alba-iulia/rectificare.pdf"
    assert store_key(named) == "2026-01-alba-1017-alba-iulia-rectificare"
