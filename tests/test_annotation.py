import hashlib
import json
from decimal import Decimal
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from bgconvertor import annotation as ann
from bgconvertor.annotation_server import AnnotationRequestHandler
from bgconvertor.config import RunConfig


def _entry(
    *,
    city: str,
    siruta: str,
    path: str,
    source_format: str,
    source_hash: str,
    units: int,
    strict_rate: float,
):
    return {
        "county_code": "01",
        "county_name": "Alba",
        "capital_siruta": siruta,
        "capital_name": city,
        "path": path,
        "source_format": source_format,
        "conversion": {
            "status": "converted",
            "quality": {
                "pct_lines_strictly_verified": strict_rate,
                "scope": {"pages_expected": units},
            },
            "artifacts": {"source_sha256": source_hash},
        },
    }


def _write_workspace(tmp_path: Path, *, page_count: int = 1) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    source = root / "data/2024/01-alba/1017-alba/budget_file.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not-a-rendered-pdf-test-source")
    workspace_path = root / "runs/annotations/2024"
    pages = [
        ann.SourcePage(
            number=page,
            label=f"Pagina {page}",
            source_type="pdf_page",
            machine=ann.MachineSuggestion(
                suggested_kind="budget_table",
                reason="fixture",
                numeric_cells=2,
            ),
            review=ann.PageReview(),
        )
        for page in range(1, page_count + 1)
    ]
    document = ann.AnnotationDocument(
        id="01-alba-1017-alba",
        year=2024,
        municipality="Alba",
        siruta="1017",
        county_code="01",
        county_name="Alba",
        source_path=str(source.relative_to(root)),
        source_format="pdf",
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_hash_verified=True,
        source_units=page_count,
        observed_strict_line_rate=50,
        benchmark_scope="full",
        pages=pages,
    )
    ann._save_document(workspace_path, document)
    workspace = ann.AnnotationWorkspace(
        year=2024,
        repository_root=str(root),
        data_root=str(root / "data"),
        runs_dir=str(root / "runs"),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        documents=[ann.WorkspaceDocument(
            id=document.id,
            municipality=document.municipality,
            siruta=document.siruta,
            county_code=document.county_code,
            county_name=document.county_name,
            source_format=document.source_format,
            source_path=document.source_path,
            source_units=document.source_units,
            benchmark_scope=document.benchmark_scope,
            observed_strict_line_rate=document.observed_strict_line_rate,
        )],
    )
    ann._save_workspace(workspace_path, workspace)
    return workspace_path


def test_initialize_inventory_covers_pdf_pages_and_workbook_sheets(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    data = root / "data/2024"
    data.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    pdf = data / "01-alba/1017-alba/budget_file.pdf"
    workbook = data / "02-arad/9262-arad/buget_orig.xlsx"
    pdf.parent.mkdir(parents=True)
    workbook.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf-source-fixture")
    workbook.write_bytes(b"xlsx-source-fixture")
    manifest = {
        "entries": [
            _entry(
                city="Alba", siruta="1017", path=str(pdf.relative_to(data)),
                source_format="pdf", source_hash=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                units=2, strict_rate=60,
            ),
            _entry(
                city="Arad", siruta="9262", path=str(workbook.relative_to(data)),
                source_format="xlsx",
                source_hash=hashlib.sha256(workbook.read_bytes()).hexdigest(),
                units=2, strict_rate=90,
            ),
        ]
    }
    (data / "manifest.json").write_text(json.dumps(manifest))

    class FakeReader:
        pages = [object(), object()]

    monkeypatch.setattr("pypdf.PdfReader", lambda _path: FakeReader())
    monkeypatch.setattr("bgconvertor.nomenclator.load_registry_for_year", lambda *_: object())
    monkeypatch.setattr(
        ann,
        "_workbook_payloads",
        lambda *_: {
            1: {"layout": "native_excel_standard", "lines": [{"values": {"total_2024": "1"}}]},
            2: {"layout": "native_excel_metadata", "lines": []},
        },
    )
    monkeypatch.setattr(
        "bgconvertor.native_workbook.read_sheets",
        lambda _path: [("Buget", [["1"]]), ("Note", [["text"]])],
    )
    config = RunConfig(runs_dir=root / "runs", reference_dir=root / "reference")
    workspace_path = root / "runs/annotations/2024"
    workspace = ann.initialize_workspace(
        year=2024,
        data_root=root / "data",
        workspace_path=workspace_path,
        config=config,
    )

    summary = ann.workspace_summary(workspace_path)
    assert summary["source_units"] == 4
    assert len(workspace.documents) == 2
    assert workspace.documents[0].municipality == "Alba"
    assert workspace.documents[0].benchmark_scope == "full"
    assert workspace.documents[1].benchmark_scope == "inventory"
    pdf_document = ann.load_document(workspace_path, workspace.documents[0].id)
    assert all(page.review.page_kind == "unreviewed" for page in pdf_document.pages)
    assert pdf_document.pages[0].machine.numeric_cells == 0


def test_annotation_number_normalization_is_explicit_about_unit_and_notation():
    assert ann.normalize_printed_value(
        "1.234,50", source_unit="mii_lei", notation="romanian"
    ) == "1234.5"
    assert ann.normalize_printed_value(
        "1234.50", source_unit="lei", notation="canonical"
    ) == "1.2345"
    with pytest.raises(ValueError, match="unitatea"):
        ann.normalize_printed_value("1", source_unit="unknown", notation="romanian")
    with pytest.raises(ValueError, match="ambiguă"):
        ann.normalize_printed_value("12.34", source_unit="mii_lei", notation="romanian")


def test_machine_output_stays_hidden_until_classification_and_freeze(tmp_path):
    workspace = _write_workspace(tmp_path)
    document_id = ann.load_workspace(workspace).documents[0].id
    initial = ann.page_payload(workspace, document_id, 1)
    assert "machine_suggestion" not in initial["page"]
    assert "comparison" not in initial["page"]

    review = ann.save_review(
        workspace,
        document_id,
        1,
        {
            "expected_revision": 0,
            "page_kind": "budget_table",
            "source_unit": "mii_lei",
            "number_notation": "romanian",
            "exhaustive": True,
            "columns": ["total_2024"],
            "rows": [{
                "id": "r1",
                "raw_code": "000102",
                "name": "TOTAL VENITURI",
                "values": {"total_2024": {"printed": "1.000,00"}},
            }],
            "reviewer": "annotator-1",
        },
    )
    assert review.status == "draft"
    assert review.rows[0].values["total_2024"].normalized_mii_lei == "1000"
    classified = ann.page_payload(workspace, document_id, 1)
    assert classified["page"]["machine_suggestion"]["suggested_kind"] == "budget_table"
    assert "comparison" not in classified["page"]

    frozen = ann.save_review(
        workspace,
        document_id,
        1,
        {
            "expected_revision": 1,
            "page_kind": "budget_table",
            "source_unit": "mii_lei",
            "number_notation": "romanian",
            "exhaustive": True,
            "columns": ["total_2024"],
            "rows": [{
                "id": "r1",
                "raw_code": "000102",
                "name": "TOTAL VENITURI",
                "values": {"total_2024": {"printed": "1.000,00"}},
            }],
            "reviewer": "annotator-1",
        },
        freeze=True,
    )
    assert frozen.status == "frozen"
    audit = ann.audit_workspace(workspace)
    assert audit["complete_page_inventory"] is True
    assert audit["recall_measurement_ready"] is True


def test_frozen_truth_requires_complete_identity_reviewer_and_numeric_scope(tmp_path):
    workspace = _write_workspace(tmp_path)
    document_id = ann.load_workspace(workspace).documents[0].id
    with pytest.raises(ValueError, match="aliasul"):
        ann.save_review(
            workspace,
            document_id,
            1,
            {
                "expected_revision": 0,
                "page_kind": "budget_table",
                "source_unit": "mii_lei",
                "number_notation": "romanian",
                "exhaustive": True,
                "columns": [],
                "rows": [],
                "no_numeric_cells": True,
            },
            freeze=True,
        )


def test_second_review_requires_a_distinct_reviewer(tmp_path):
    workspace = _write_workspace(tmp_path)
    document_id = ann.load_workspace(workspace).documents[0].id
    frozen = ann.save_review(
        workspace,
        document_id,
        1,
        {
            "expected_revision": 0,
            "page_kind": "budget_table",
            "source_unit": "mii_lei",
            "number_notation": "romanian",
            "exhaustive": True,
            "columns": ["total_2024"],
            "rows": [{
                "id": "r1", "name": "TOTAL",
                "values": {"total_2024": {"printed": "1", "certain": False}},
            }],
            "reviewer": "reviewer-a",
        },
        freeze=True,
    )
    audit = ann.audit_workspace(workspace)
    assert any(
        problem["problem"] == "uncertain_cells_need_second_review"
        for problem in audit["problems"]
    )
    with pytest.raises(ValueError, match="diferit"):
        ann.complete_second_review(
            workspace, document_id, 1,
            expected_revision=frozen.revision, reviewer="reviewer-a",
        )
    reviewed = ann.complete_second_review(
        workspace, document_id, 1,
        expected_revision=frozen.revision, reviewer="reviewer-b",
    )
    assert reviewed.second_reviewer == "reviewer-b"
    assert reviewed.second_reviewed_at
    assert ann.audit_workspace(workspace)["recall_measurement_ready"] is True


def test_freeze_allows_same_code_for_distinct_printed_rows(tmp_path):
    workspace = _write_workspace(tmp_path)
    document_id = ann.load_workspace(workspace).documents[0].id
    review = ann.save_review(
        workspace,
        document_id,
        1,
        {
            "expected_revision": 0,
            "page_kind": "budget_table",
            "exhaustive": True,
            "source_unit": "mii_lei",
            "number_notation": "romanian",
            "columns": ["total_2024"],
            "reviewer": "reviewer-a",
            "rows": [
                {
                    "id": "parent",
                    "raw_code": "51.02/58.02",
                    "name": "Proiecte cu finanțare externă",
                    "values": {"total_2024": {"printed": "100,00"}},
                },
                {
                    "id": "detail",
                    "raw_code": "51.02/58.02",
                    "name": "Proiect FSE",
                    "values": {"total_2024": {"printed": "100,00"}},
                },
            ],
        },
        freeze=True,
    )

    assert review.status == "frozen"
    assert len(review.rows) == 2


def test_freeze_rejects_an_exact_repeated_printed_cell(tmp_path):
    workspace = _write_workspace(tmp_path)
    document_id = ann.load_workspace(workspace).documents[0].id
    duplicate = {
        "raw_code": "51.02/58.02",
        "name": "Proiect FSE",
        "values": {"total_2024": {"printed": "100,00"}},
    }
    with pytest.raises(ValueError, match="celulă duplicată"):
        ann.save_review(
            workspace,
            document_id,
            1,
            {
                "expected_revision": 0,
                "page_kind": "budget_table",
                "exhaustive": True,
                "source_unit": "mii_lei",
                "number_notation": "romanian",
                "columns": ["total_2024"],
                "reviewer": "reviewer-a",
                "rows": [
                    {"id": "first", **duplicate},
                    {"id": "second", **duplicate},
                ],
            },
            freeze=True,
        )


def test_page_score_counts_missing_and_extra_cells_one_to_one():
    review = ann.PageReview(
        page_kind="budget_table",
        status="frozen",
        exhaustive=True,
        source_unit="mii_lei",
        reviewer="a",
        rows=[ann.GroundTruthRow(
            id="r1",
            raw_code="000102",
            economic_code="00.01.02",
            name="TOTAL VENITURI",
            values={
                "total_2024": ann.AnnotationValue(printed="100", normalized_mii_lei="100"),
                "trim1": ann.AnnotationValue(printed="25", normalized_mii_lei="25"),
            },
        )],
    )
    facts = [
        ann.PredictionFact(
            document="BUGET LOCAL", budget="local", page=1, section="TOTAL",
            kind="revenue", raw_code="000102", economic_code="00.01.02",
            name="TOTAL VENITURI", column="total_2024", value_mii_lei="100", source="ocr",
        ),
        ann.PredictionFact(
            document="BUGET LOCAL", budget="local", page=1, section="TOTAL",
            kind="revenue", raw_code="0402", economic_code="04.02",
            name="Cote", column="trim1", value_mii_lei="7", source="ocr",
        ),
    ]
    score = ann.score_page(review, facts, 1)
    assert score["matched"] == 1
    assert score["expected"] == 2
    assert score["predicted"] == 2
    assert score["recall_pct"] == 50
    assert score["precision_pct"] == 50
    assert len(score["misses"]) == 1
    assert len(score["extras"]) == 1


def test_page_score_uses_exact_code_without_requiring_ocr_perfect_name():
    review = ann.PageReview(
        page_kind="budget_table",
        status="frozen",
        exhaustive=True,
        source_unit="mii_lei",
        reviewer="a",
        rows=[ann.GroundTruthRow(
            id="r1",
            raw_code="04.02.01",
            name="Cote defalcate din impozitul pe venit",
            values={
                "total_2024": ann.AnnotationValue(
                    printed="100", normalized_mii_lei="100"
                ),
            },
        )],
    )
    facts = [ann.PredictionFact(
        document="BUGET LOCAL",
        budget="local",
        page=1,
        kind="revenue",
        raw_code="04.02.01",
        name="Cote defalcate Imp.pe Venit, adr. AJFP",
        column="total_2024",
        value_mii_lei="100",
        source="ocr",
    )]

    score = ann.score_page(review, facts, 1)

    assert score["matched"] == 1
    assert score["recall_pct"] == 100


def test_uncoded_annotation_rows_match_common_budget_abbreviations():
    assert ann._text_match(
        "Drepturile asistenților personali cu handicap grav buget local",
        "drepturile asist. pers. cu hand.grav - BL",
    )
    assert ann._text_match(
        "Salarii unități de îngrijire la domiciliu buget local",
        "salarii Unitati de Ingr.la Domiciliu a pers.in varsta-BL",
    )
    assert not ann._text_match(
        "Drepturile asistenților personali cu handicap grav TVA",
        "drepturile asist. pers. cu hand.grav - BL",
    )
    assert ann._text_match("Străzi-PMC", "Strazi - PMC")
    assert ann._text_match(
        "Întreținere spații verzi-PMC",
        "Intretinere gradini publice, parcuri, zone verzi - P.M.C.",
    )


def test_annotation_http_policy_requires_loopback_host_and_token():
    handler = object.__new__(AnnotationRequestHandler)
    headers = Message()
    headers["Host"] = "127.0.0.1:8765"
    headers["X-Annotation-Token"] = "secret-token"
    handler.headers = headers
    handler.server = SimpleNamespace(token="secret-token")
    assert handler._host_is_loopback() is True
    assert handler._authorized({}) is True
    assert handler._authorized({"token": ["wrong"]}) is True  # header wins

    del headers["X-Annotation-Token"]
    assert handler._authorized({"token": ["wrong"]}) is False
    assert handler._authorized({"token": ["secret-token"]}) is True
    headers.replace_header("Host", "annotation.example.com")
    assert handler._host_is_loopback() is False


def test_prediction_facts_exclude_annex_and_preserve_classification():
    from bgconvertor.model import BudgetDocument, BudgetLine, ConversionResult

    result = ConversionResult(
        pdf="fixture.pdf",
        documents=[BudgetDocument(
            title="BUGET LOCAL", budget="local", suffix="02", pages=[1],
            context_id="cui:1", institution="Școala A",
            lines=[
                BudgetLine(
                    raw_code="6502.2001", code="20.01", func_code="65.02",
                    name="Bunuri", kind="expense_economic", page=1,
                    institution="Școala B", form="Formular 11",
                    subdocument="Secțiune proprie",
                    values={"total_2024": Decimal("12.5")},
                ),
                BudgetLine(
                    raw_code="1", code="1", name="Anexă", kind="annex", page=1,
                    values={"total_2024": Decimal("99")},
                ),
            ],
        )],
    )
    facts = ann.prediction_facts(result)
    assert len(facts) == 1
    assert facts[0].functional_code == "65.02"
    assert facts[0].economic_code == "20.01"
    assert facts[0].institution == "Școala B"
    assert facts[0].form == "Formular 11"
    assert facts[0].subdocument == "Secțiune proprie"


def test_detected_prediction_pages_include_structural_x_only_budget_table(
    tmp_path, monkeypatch
):
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"fixture")
    config = RunConfig(runs_dir=tmp_path / "runs")
    monkeypatch.setattr(ann, "store_key", lambda _source: "fixture")
    extract = config.runs_dir / "fixture/extract/p0002.json"
    extract.parent.mkdir(parents=True)
    extract.write_text(json.dumps({
        "payload": {
            "lines": [{"name": "Rând", "values": {}, "x_markers": ["total_2024"]}],
            "mapping_context": {"budget_table": True},
        }
    }))

    pages = ann._detected_prediction_pages(
        config,
        source,
        2,
        [ann.PredictionFact(
            document="Buget", budget="local", page=1, kind="revenue",
            name="Venit", column="total_2024", value_mii_lei="1", source="ocr",
        )],
    )

    assert pages == {1, 2}


def test_deterministic_budget_detection_is_not_hidden_by_llm_payload(
    tmp_path, monkeypatch
):
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"fixture")
    config = RunConfig(runs_dir=tmp_path / "runs")
    monkeypatch.setattr(ann, "store_key", lambda _source: "fixture")
    root = config.runs_dir / "fixture"
    for stage, payload in (
        ("extract", {"mapping_context": {"budget_table": True}}),
        ("llm_extract", {"layout": "llm_fallback", "lines": []}),
    ):
        target = root / stage / "p0001.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"payload": payload}))

    assert ann._detected_prediction_pages(config, source, 1, []) == {1}


def test_fact_matching_uses_line_level_form_and_subdocument():
    row = ann.GroundTruthRow(
        id="r1",
        name="Bunuri",
        institution="Școala A",
        form="Formular 11",
        subdocument="Buget propriu",
        values={"total_2024": ann.AnnotationValue(printed="10")},
    )
    fact = ann.PredictionFact(
        document="Buget local",
        institution="Școala A",
        form="Formular 11",
        subdocument="Buget propriu",
        budget="local",
        page=1,
        kind="heading",
        name="Bunuri",
        column="total_2024",
        value_mii_lei="10",
        source="ocr",
    )

    assert ann._fact_matches(row, fact)


def test_prediction_result_prefers_exact_exported_candidate(tmp_path, monkeypatch):
    from bgconvertor.model import BudgetDocument, BudgetLine, ConversionResult
    from bgconvertor.runstore import RunStore

    workspace = _write_workspace(tmp_path)
    root = Path(ann.load_workspace(workspace).repository_root)
    source = root / "data/2024/01-alba/1017-alba/budget_file.pdf"
    config = RunConfig(runs_dir=root / "runs", reference_dir=root / "reference")
    monkeypatch.setattr(
        "bgconvertor.nomenclator.load_registry_for_year", lambda *_: object()
    )
    exported = ConversionResult(
        pdf=source.name,
        documents=[BudgetDocument(
            title="BUGET LOCAL", budget="local", suffix="02", pages=[1],
            lines=[BudgetLine(
                raw_code="0402", code="04.02", name="Cote", kind="revenue", page=1,
                values={"total_2024": Decimal("777")},
                value_sources={"total_2024": "llm_targeted"},
            )],
        )],
        pages_expected=1,
        pages_selected=[1],
        pages_processed=[1],
    )
    RunStore(config, source).put_final_candidate(exported)

    result = ann._prediction_result(config, source, 2024, 1)

    assert result.documents[0].lines[0].values["total_2024"] == Decimal("777")
    metadata = ann._candidate_metadata(config, source, 1)
    assert metadata["final_candidate"]["artifact_sha256"]
