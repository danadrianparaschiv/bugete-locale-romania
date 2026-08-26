"""Unit tests for assembly and validation on synthetic payloads, plus an
integration test over the real digital PDF (skipped when absent)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bgconvertor.assemble import assemble
from bgconvertor.config import RunConfig
from bgconvertor.export import _sheet_columns
from bgconvertor.export import export as export_xlsx
from bgconvertor.extract.scanned import map_payload
from bgconvertor.model import ConversionResult
from bgconvertor.nomenclator import load_registry
from bgconvertor.runstore import RunStore
from bgconvertor.validate import validate


def _mk_store(tmp_path: Path) -> RunStore:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF fake")
    return RunStore(RunConfig(runs_dir=tmp_path / "runs"), pdf)


def _line(raw_code, name, section=None, func=None, code=None, **values):
    from bgconvertor.parsing import normalize_indicator_code

    return {
        "raw_code": raw_code,
        "code": code or normalize_indicator_code(raw_code),
        "func_code": func,
        "name": name,
        "row_no": None,
        "section": section,
        "year": None,
        "values": {k: str(v) for k, v in values.items()},
    }


def test_rectification_export_labels_distinguish_initial_and_rectified():
    line = SimpleNamespace(
        values={"buget_2026": 1, "influente": 2, "total_2026": 3},
        x_markers=[],
    )
    columns = dict(_sheet_columns([line]))
    assert columns["buget_2026"] == "Buget 2026 (initial)"
    assert columns["influente"] == "Influente"
    assert columns["total_2026"] == "Buget 2026 rectificat"


@pytest.fixture
def registry(reference_dir):
    return load_registry(reference_dir)


def test_assemble_documents_sections_regions(tmp_path):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT LA VENITURI",
        "lines": [
            _line(None, "====== SECTIUNEA TOTAL ======", section="SECTIUNEA TOTAL"),
            _line("000102", "TOTAL VENITURI", total="100.00"),
            _line("0302", "Impozit pe venit", total="100.00"),
        ],
    })
    store.put("extract", 2, {
        "text": None,
        "lines": [
            _line("5002", "TOTAL CHELTUIELI", total="100.00"),
            _line("6502", "CAP. Invatamant", total="100.00"),
            _line("6502.10", "TITLUL I", func="65.02", code="10", total="100.00"),
        ],
    })
    docs = assemble(store, [1, 2])
    assert len(docs) == 1
    doc = docs[0]
    assert doc.budget == "local"
    kinds = [ln.kind for ln in doc.lines]
    assert kinds == ["heading", "revenue", "revenue", "expense_functional",
                     "expense_functional", "expense_economic"]
    # section carried across pages
    assert doc.lines[-1].section == "TOTAL"


def test_assemble_stitches_a_row_split_across_pages(tmp_path):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT",
        "layout": "scan_simple_table",
        "lines": [_line("6502", "Invatamant")],
    })
    store.put("extract", 2, {
        "text": None,
        "layout": "scan_simple_table",
        "lines": [_line(None, "", total_2025="123.00")],
    })
    doc = assemble(store, [1, 2])[0]
    assert len(doc.lines) == 1
    assert doc.lines[0].raw_code == "6502"
    assert doc.lines[0].values == {"total_2025": 123}


def test_assemble_stitches_name_above_code_across_pages(tmp_path):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT",
        "layout": "scan_simple_table",
        "lines": [_line(None, "Invatamant prescolar")],
    })
    store.put("extract", 2, {
        "text": None,
        "layout": "scan_simple_table",
        "lines": [_line("650203", "", total_2025="45")],
    })
    doc = assemble(store, [1, 2])[0]
    assert len(doc.lines) == 1
    assert doc.lines[0].name == "Invatamant prescolar"
    assert doc.lines[0].raw_code == "650203"


def test_assemble_repairs_truncated_func_prefix(tmp_path):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT",
        "lines": [
            _line("5002", "TOTAL CHELTUIELI", total="10.00"),
            # the PDF prints 5002.580103 truncated to 02.580103
            _line("02.5801", "Programe FEDR", func="02", code="58.01", total="10.00"),
        ],
    })
    doc = assemble(store, [1])[0]
    assert doc.lines[-1].func_code == "50.02"


def test_assemble_source_digit_completion_is_name_gated(tmp_path, registry):
    """Bare codes are completed to <NN>.<suffix> only when the printed name
    matches the official one (PMB drops the source digit document-wide).
    Investment-list ordinals and form-internal row codes (Bacău '00',
    Arad '96') look identical and must stay untouched — completing them
    fabricates duplicate identities across the whole document."""
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT LA VENITURI SI CHELTUIELI",
        "lines": [
            _line("000102", "TOTAL VENITURI", total="10.00"),
            # PMB style: source digit dropped, official names printed
            _line("03", "Impozit pe venit", total="10.00"),
            _line("30.05.30", "Alte venituri din concesiuni si inchirieri", total="1.00"),
            # Arad form rows: '96' is the form's internal section code
            _line("96", "VENITURILE SECTIUNII DE FUNCTIONARE", total="10.00"),
        ],
    })
    store.put("extract", 2, {
        "text": None,
        "layout": "investment_list",
        "lines": [
            # Bacău: 'Nr. crt.' ordinal of an investment item, not a code
            _line("12", "Amenajare spatiu comunitar, str. Progresului", total="20.00"),
        ],
    })
    doc = assemble(store, [1, 2], registry)[0]
    by_raw = {ln.raw_code: ln for ln in doc.lines}
    assert by_raw["03"].code == "03.02"          # name matches -> completed
    assert by_raw["30.05.30"].code == "30.02.05.30"
    assert by_raw["96"].code == "96"             # name mismatch -> untouched
    assert by_raw["12"].code == "12"             # out-of-scope annex -> untouched


def test_validate_v1_unknown_code(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT DE TEST",
        "lines": [
            _line("000102", "TOTAL VENITURI", total="5.00"),
            _line("039299", "Cod inventat", total="5.00"),
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1]))
    validate(result, registry)
    issues = [i for i in result.all_issues() if i.check == "V1_code"]
    assert len(issues) == 1 and issues[0].code == "03.92.99"


def test_validate_v3_row_checksum(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT DE TEST",
        "lines": [
            _line("000102", "TOTAL VENITURI", total="100.00",
                  trim1="30.00", trim2="30.00", trim3="30.00", trim4="9.00"),
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1]))
    validate(result, registry)
    checks = [i.check for i in result.all_issues()]
    assert "V3_row_checksum" in checks


def test_validate_v4_hierarchy_breach_and_est_convention(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT DE TEST",
        "lines": [
            _line("000102", "TOTAL VENITURI", total="1.00"),
            _line("0302", "Impozit pe venit", total="10.00", est2027="99.00"),
            _line("030218", "Impozitul pe veniturile din transferul proprietatilor",
                  total="7.00"),  # breach: child sums 7 != 10; est children absent -> ok
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1]))
    validate(result, registry)
    v4 = [i for i in result.all_issues() if i.check == "V4_hierarchy"]
    assert len(v4) == 1
    assert v4[0].column == "total"


def test_validate_v5_identity(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT DE TEST",
        "lines": [
            _line(None, "====== SECTIUNEA TOTAL ======", section="SECTIUNEA TOTAL"),
            _line("000102", "TOTAL VENITURI", total="100.00"),
            _line("000202", "VENITURI CURENTE", total="60.00"),   # 00.02
            _line("000302", "VENITURI FISCALE", total="40.00"),   # 00.03
            _line("001202", "VENITURI NEFISCALE", total="30.00"), # 00.12 -> 40+30 != 60
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1]))
    validate(result, registry)
    v5 = [i for i in result.all_issues() if i.check == "V5_identity"]
    assert any(i.code == "00.02" for i in v5)


def test_institution_grid_survives_assembly_and_validation(tmp_path, registry):
    source_path = (
        Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p301.json"
    )
    source = json.loads(source_path.read_text())
    payload = map_payload({"tables_raw": [source["grid"]], "text": source["text"]})
    store = _mk_store(tmp_path)
    store.put("extract", 301, payload)

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [301], registry),
        pages_expected=333,
        pages_selected=[301],
        pages_processed=[301],
    )
    validate(result, registry)
    stats = result.stats()

    assert sum(len(line.values) for doc in result.documents for line in doc.lines) == 51
    assert stats["numeric_cells"] == 51
    assert stats["numeric_cells_strictly_verified"] == 51
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["issues"] == {"error": 0, "warning": 0, "info": 0}
    assert all(
        line.kind == "heading"
        for doc in result.documents for line in doc.lines
        if line.raw_code == "96"
    )


def test_cluj_individual_forms_scope_legitimate_and_real_duplicates(tmp_path, registry):
    """Repeated codes are legitimate across institutions, but not twice in one form."""
    fixture_path = (
        Path(__file__).parent / "fixtures" / "assembly"
        / "cluj_individual_boundaries.json"
    )
    fixture = json.loads(fixture_path.read_text())
    store = _mk_store(tmp_path)
    for page in fixture["pages"]:
        store.put("extract", page["page"], page["payload"])

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [140, 144, 145], registry),
        pages_expected=145,
        pages_selected=[140, 144, 145],
        pages_processed=[140, 144, 145],
    )
    validate(result, registry)

    assert len(result.documents) == 2
    first, second = result.documents
    assert first.context_id == "cui:17973191"
    assert second.context_id == "cui:5303102"
    assert first.institution == "Colegiul Unitarian Janos Zsigmond"
    assert second.pages == [144, 145]
    economic = [
        line for doc in result.documents for line in doc.lines
        if line.code == "20"
    ]
    assert all(line.kind == "expense_economic" for line in economic)
    assert all(line.func_code == "54.02" for line in economic)

    duplicates = [
        issue for issue in result.all_issues()
        if issue.check == "V7_hygiene" and "duplicate" in issue.message
    ]
    assert len(duplicates) == 1
    assert duplicates[0].page == 145
    assert duplicates[0].message == "duplicate of p144 with different values"


def test_collapsed_annual_grid_survives_assembly_and_validation(tmp_path, registry):
    source_path = (
        Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p009.json"
    )
    source = json.loads(source_path.read_text())
    payload = map_payload({"tables_raw": [source["grid"]], "text": source["text"]})
    store = _mk_store(tmp_path)
    store.put("extract", 9, payload)

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [9], registry),
        pages_expected=236,
        pages_selected=[9],
        pages_processed=[9],
    )
    validate(result, registry)
    stats = result.stats()

    assert stats["numeric_cells"] == 216
    assert stats["numeric_cells_strictly_verified"] == 216
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["issues"] == {"error": 0, "warning": 0, "info": 0}

    line = next(
        line
        for doc in result.documents
        for line in doc.lines
        if line.raw_code == "74020502"
    )
    assert str(line.values["total_2026"]) == "2742"
    assert line.name.startswith("Colectarea")


def test_collapsed_detail_grid_survives_assembly_and_validation(tmp_path, registry):
    source_path = (
        Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p041.json"
    )
    source = json.loads(source_path.read_text())
    payload = map_payload({"tables_raw": [source["grid"]], "text": source["text"]})
    store = _mk_store(tmp_path)
    store.put("extract", 41, payload)

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [41], registry),
        pages_expected=236,
        pages_selected=[41],
        pages_processed=[41],
    )
    validate(result, registry)
    stats = result.stats()

    assert stats["numeric_cells"] == 245
    assert stats["numeric_cells_strictly_verified"] == 245
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["issues"] == {"error": 0, "warning": 0, "info": 0}

    line = next(
        line
        for doc in result.documents
        for line in doc.lines
        if line.raw_code == "200103"
    )
    assert str(line.values["total_2026"]) == "52.00"
    assert line.func_code == "68.02.05.02"


def test_expense_chapter_grid_survives_assembly_and_validation(tmp_path, registry):
    source_path = (
        Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p151.json"
    )
    source = json.loads(source_path.read_text())
    payload = map_payload({"tables_raw": [source["grid"]], "text": source["text"]})
    store = _mk_store(tmp_path)
    store.put("extract", 151, payload)

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [151], registry),
        pages_expected=333,
        pages_selected=[151],
        pages_processed=[151],
    )
    validate(result, registry)
    stats = result.stats()

    assert stats["numeric_cells"] == 80
    assert stats["numeric_cells_strictly_verified"] == 80
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["issues"] == {"error": 0, "warning": 0, "info": 0}

    line = next(
        line
        for doc in result.documents
        for line in doc.lines
        if line.raw_code == "56.48"
    )
    assert str(line.values["buget_2026"]) == "93474.00"
    assert line.section == "SECTIUNEA DE DEZVOLTARE"


def test_revenue_detail_grid_survives_assembly_validation_and_export(tmp_path, registry):
    source_path = (
        Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p031.json"
    )
    source = json.loads(source_path.read_text())
    payload = map_payload({"tables_raw": [source["grid"]], "text": source["text"]})
    store = _mk_store(tmp_path)
    store.put("extract", 31, payload)

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [31], registry),
        pages_expected=333,
        pages_selected=[31],
        pages_processed=[31],
    )
    validate(result, registry)
    stats = result.stats()
    lines = [line for doc in result.documents for line in doc.lines]

    assert payload["layout"] == "scan_revenue_detail"
    assert stats["numeric_cells"] == 73
    assert stats["numeric_cells_strictly_verified"] == 73
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["issues"] == {"error": 0, "warning": 0, "info": 0}
    assert sum(len(line.x_markers) for line in lines) == 15
    assert [line.row_no for line in lines] == list(range(499, 521))
    assert all(line.kind == "revenue" for line in lines)
    assert dict(_sheet_columns(lines)) == {
        "buget_2026": "Buget 2026",
        "est2027": "Estimare 2027",
        "est2028": "Estimare 2028",
        "est2029": "Estimare 2029",
    }

    workbook_path = export_xlsx(result, tmp_path / "arad-p31.xlsx")
    from openpyxl import load_workbook

    workbook = load_workbook(workbook_path)
    data_sheet = workbook["BL Date"]
    assert data_sheet["G1"].value == "Buget 2026"
    assert data_sheet["C2"].alignment.wrap_text is True
    assert data_sheet.row_dimensions[2].height > 15


def test_investment_grid_survives_assembly_validation_and_export(tmp_path, registry):
    source_path = (
        Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p171.json"
    )
    source = json.loads(source_path.read_text())
    payload = map_payload({"tables_raw": [source["grid"]], "text": source["text"]})
    store = _mk_store(tmp_path)
    store.put("extract", 171, payload)

    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [171], registry),
        pages_expected=236,
        pages_selected=[171],
        pages_processed=[171],
    )
    validate(result, registry)
    stats = result.stats()

    assert stats["numeric_cells"] == 62
    assert stats["numeric_cells_strictly_verified"] == 62
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["issues"] == {"error": 0, "warning": 0, "info": 0}

    lines = [line for doc in result.documents for line in doc.lines]
    assert all(line.kind == "annex" for line in lines if line.values)
    columns = dict(_sheet_columns(lines))
    assert columns["buget_local_pct"] == "% Buget local"
    assert columns["credite_externe_pct"] == "% Credite externe"
    assert columns["credite_interne_pct"] == "% Credite interne"
    assert columns["buget_fen_pct"] == "% Buget FEN"


def test_integration_ab_pdf_fully_clean(ab_pdf, reference_dir):
    """The real digital file must stay 100% clean — the Phase 1 quality bar."""
    import pdfplumber

    from bgconvertor.extract import digital

    config = RunConfig(runs_dir=Path("runs"))
    store = RunStore(config, ab_pdf)
    missing = [p for p in range(1, 71) if store.get("extract", p) is None]
    if missing:
        with pdfplumber.open(ab_pdf) as plumber:
            for p in missing:
                store.put("extract", p, digital.extract_page(plumber.pages[p - 1]))

    result = ConversionResult(pdf=ab_pdf.name, documents=assemble(store, list(range(1, 71))))
    validate(result, load_registry(reference_dir))
    stats = result.stats()
    assert stats["documents"] == 2
    assert stats["lines"] > 2000
    assert stats["pct_clean"] == 100.0, [i.message for i in result.all_issues()][:10]
