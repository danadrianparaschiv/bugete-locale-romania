from pathlib import Path

import pytest

from bgconvertor.config import RunConfig
from bgconvertor.corpus import build_result, export, export_rows

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ab_ready():
    pdf = PROJECT_ROOT / "budget_file_ab.pdf"
    if not (pdf.exists() and (PROJECT_ROOT / "runs/budget_file_ab/extract").is_dir()):
        pytest.skip("ab extraction not present")
    return pdf


def test_ab_stays_fully_clean(ab_ready, monkeypatch):
    """The digital reference file must validate 100% clean — always.

    This pins the assembly/validation behavior: any tiebreak or classifier
    change that misfiles even one Alba Iulia line fails here.
    """
    monkeypatch.chdir(PROJECT_ROOT)
    result = build_result(RunConfig(), ab_ready)
    stats = result.stats()
    assert stats["pct_clean"] == 100.0, stats


def test_export_rows_shape_and_verified(ab_ready, monkeypatch, tmp_path):
    monkeypatch.chdir(PROJECT_ROOT)
    rows = list(export_rows(RunConfig(), ab_ready))
    assert len(rows) > 5000  # 2046 lines x several columns
    r = rows[0]
    assert set(r) == {
        "municipality", "document", "budget", "suffix", "section", "kind",
        "code", "func_code", "name", "column", "value", "source", "verified", "page",
    }
    assert r["municipality"] == "ab"
    assert all(row["verified"] for row in rows)  # ab is 100% clean

    out = tmp_path / "corpus.csv"
    stats = export(RunConfig(), [ab_ready], out)
    assert stats["rows"] == len(rows)
    assert out.exists()
