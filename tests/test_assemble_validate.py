"""Unit tests for assembly and validation on synthetic payloads, plus an
integration test over the real digital PDF (skipped when absent)."""

import json
from pathlib import Path

import pytest

from bgconvertor.assemble import assemble
from bgconvertor.config import RunConfig
from bgconvertor.export import _sheet_columns
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
