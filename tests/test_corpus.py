import json
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
        "document", "context_id", "institution", "budget", "suffix", "section", "kind",
        "code", "code_source", "func_code", "functional_code", "economic_code",
        "name", "column", "value", "unit", "source", "verified",
        "verification_status", "validation_evidence", "validation_issues", "page",
    }
    assert r["municipality"] == "Alba Iulia"  # resolved via SIRUTA manifest
    assert all(row["verified"] for row in rows)  # ab is 100% clean
    assert all(row["verification_status"] == "strictly_verified" for row in rows)
    assert all(row["unit"] == "mii lei" for row in rows)
    assert all("recall_measured" in json.loads(row["validation_evidence"]) for row in rows)

    out = tmp_path / "corpus.csv"
    stats = export(RunConfig(), [ab_ready], out)
    assert stats["rows"] == len(rows)
    assert out.exists()


def test_info_issue_is_not_exported_as_verified(monkeypatch, tmp_path):
    line = BudgetLine(
        code="04.02", name="Cote", kind="revenue", page=1,
        values={"total": Decimal("1")},
        source="mixed",
        value_sources={"total": "llm:gemini-3.6-flash"},
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
    assert rows[0]["source"] == "llm:gemini-3.6-flash"
    evidence = json.loads(rows[0]["validation_evidence"])
    assert evidence["status"] == "flagged"
    assert evidence["cell_source"] == "llm:gemini-3.6-flash"
    assert evidence["findings"][0]["check"] == "V7_hygiene"


def test_export_rows_has_explicit_functional_and_economic_codes(monkeypatch, tmp_path):
    lines = [
        BudgetLine(
            code="04.02", name="Cote", kind="revenue", page=1,
            values={"total": Decimal("10")},
        ),
        BudgetLine(
            code="65.02", name="Învățământ", kind="expense_functional", page=1,
            values={"total": Decimal("8")},
        ),
        BudgetLine(
            code="10.01", func_code="65.02", name="Salarii",
            kind="expense_economic", page=1,
            values={"total": Decimal("5")},
        ),
    ]
    result = ConversionResult(
        pdf="x.pdf",
        documents=[BudgetDocument(
            title="Buget", budget="local", suffix="02", pages=[1], lines=lines
        )],
    )
    monkeypatch.setattr("bgconvertor.corpus.build_result", lambda config, pdf: result)

    by_kind = {
        row["kind"]: row
        for row in export_rows(RunConfig(), tmp_path / "x.pdf")
    }
    assert by_kind["revenue"]["functional_code"] is None
    assert by_kind["revenue"]["economic_code"] == "04.02"
    assert by_kind["expense_functional"]["functional_code"] == "65.02"
    assert by_kind["expense_functional"]["economic_code"] is None
    assert by_kind["expense_economic"]["functional_code"] == "65.02"
    assert by_kind["expense_economic"]["economic_code"] == "10.01"
