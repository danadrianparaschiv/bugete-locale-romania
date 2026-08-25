from decimal import Decimal
from pathlib import Path

import pytest

from bgconvertor.config import RunConfig
from bgconvertor.corpus import build_result, export, export_rows
from bgconvertor.model import BudgetDocument, BudgetLine, ConversionResult, Issue

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ab_ready():
    pdf = PROJECT_ROOT / "data/2026/01-alba/1017-alba-iulia/budget_file.pdf"
    if not (pdf.exists() and (PROJECT_ROOT / "runs/2026-01-alba-1017-alba-iulia/extract").is_dir()):
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
        "municipality", "siruta", "county_code", "county", "year",
        "document", "budget", "suffix", "section", "kind",
        "code", "func_code", "name", "column", "value", "source", "verified",
        "verification_status", "validation_issues", "page",
    }
    assert r["municipality"] == "Alba Iulia"  # resolved via SIRUTA manifest
    assert all(row["verified"] for row in rows)  # ab is 100% clean
    assert all(row["verification_status"] == "strictly_verified" for row in rows)

    out = tmp_path / "corpus.csv"
    stats = export(RunConfig(), [ab_ready], out)
    assert stats["rows"] == len(rows)
    assert out.exists()


def test_info_issue_is_not_exported_as_verified(monkeypatch, tmp_path):
    line = BudgetLine(
        code="04.02", name="Cote", kind="revenue", page=1,
        values={"total": Decimal("1")},
        issues=[Issue(check="V7_hygiene", severity="info", message="review")],
    )
    result = ConversionResult(
        pdf="x.pdf",
        documents=[BudgetDocument(
            title="Buget", budget="local", suffix="02", pages=[1], lines=[line]
        )],
    )
    monkeypatch.setattr("bgconvertor.corpus.build_result", lambda config, pdf: result)
    rows = list(export_rows(RunConfig(), tmp_path / "x.pdf"))
    assert rows[0]["verified"] is False
    assert rows[0]["verification_status"] == "flagged"
    assert rows[0]["validation_issues"] == "V7_hygiene:info"
