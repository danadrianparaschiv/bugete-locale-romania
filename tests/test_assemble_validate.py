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
from bgconvertor.validate import revalidate, validate


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


def test_assemble_applies_functional_context_from_scanned_page_header(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": (
            "BUGETUL LOCAL DETALIAT LA CHELTUIELI "
            "Capitolul 5102 Autoritati publice Paragraful 0103 "
            "Autoritati executive"
        ),
        "layout": "scan_simple_table",
        "lines": [
            _line("10", "Cheltuieli de personal", total_2024="100"),
            _line("20", "Bunuri si servicii", total_2024="20"),
        ],
    })
    store.put("extract", 2, {
        "text": (
            "Capitolul 5402 Alte servicii publice generale "
            "Subcapitolul 540210 Servicii publice comunitare"
        ),
        "layout": "scan_simple_table",
        "lines": [_line("10", "Cheltuieli de personal", total_2024="50")],
    })

    doc = assemble(store, [1, 2], registry)[0]
    assert [line.func_code for line in doc.lines] == [
        "51.02.01.03", "51.02.01.03", "54.02.10",
    ]
    assert all(line.kind == "expense_economic" for line in doc.lines)


def test_header_functional_context_scopes_legitimate_repeated_codes(tmp_path, registry):
    store = _mk_store(tmp_path)
    for page, header, amount in (
        (1, "Capitolul 5402 Subcapitolul 540205", "10"),
        (2, "Capitolul 5402 Subcapitolul 540210", "20"),
    ):
        store.put("extract", page, {
            "text": "BUGETUL LOCAL DETALIAT " + header,
            "layout": "scan_simple_table",
            "lines": [_line("10", "Cheltuieli de personal", total_2024=amount)],
        })
    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [1, 2], registry),
        pages_expected=2,
        pages_selected=[1, 2],
        pages_processed=[1, 2],
    )
    validate(result, registry)

    lines = result.documents[0].lines
    assert [line.func_code for line in lines] == ["54.02.05", "54.02.10"]
    assert not [
        issue for issue in result.all_issues()
        if issue.check == "V7_hygiene" and "duplicate" in issue.message
    ]


def test_printed_section_heading_scopes_repeated_summary_rows(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT LA CHELTUIELI",
        "layout": "scan_simple_table",
        "lines": [
            _line("5002", "Partea I SERVICII PUBLICE GENERALE", total_2024="100"),
            _line(
                "4902",
                "CHELTUIELILE SECTIUNII DE FUNCTIONARE (cod 50.02+59.02)",
                total_2024="80",
            ),
            _line("5002", "Partea I SERVICII PUBLICE GENERALE", total_2024="80"),
        ],
    })
    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [1], registry),
        pages_expected=1,
        pages_selected=[1],
        pages_processed=[1],
    )
    validate(result, registry)

    repeated = [line for line in result.documents[0].lines if line.code == "50.02"]
    assert [line.section for line in repeated] == [None, "FUNCTIONARE"]
    assert not [
        issue for issue in result.all_issues()
        if issue.check == "V7_hygiene" and "duplicate" in issue.message
    ]


def test_repeated_summary_repairs_one_cell_only_after_two_columns_agree(tmp_path):
    store = _mk_store(tmp_path)
    first = _line(
        None,
        "CHELTUIELI TOTAL, din care:",
        buget_2023="568077",
        executie_2023="275990.59",
        total_2024="463579",
    )
    second = _line(
        None,
        "TOTAL CHELTUIELI footer OCR",
        executie_2023="275990.59",
        total_2024="463579",
    )
    second["cell_issues"] = [{"column": "buget_2023", "raw": "568.0,7.00"}]
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT LA CHELTUIELI",
        "layout": "scan_comparative_budget",
        "lines": [first],
    })
    store.put("extract", 2, {
        "text": None,
        "layout": "digital_detail",
        "lines": [second],
    })

    line = assemble(store, [1, 2])[-1].lines[-1]

    assert line.values["buget_2023"] == 568077
    assert line.value_sources["buget_2023"] == "cross_page_repeat"
    assert not [issue for issue in line.issues if issue.column == "buget_2023"]


def test_mid_page_header_context_starts_at_detail_total(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": (
            "DIN CARE: Capitolul 5102 Autoritati publice "
            "Paragraful 0103 Autoritati executive"
        ),
        "layout": "scan_simple_table",
        "lines": [
            _line("70", "CHELTUIELI DE CAPITAL", total_2024="200"),
            _line(None, "TOTAL CHELTUIELI", total_2024="100"),
            _line("70", "CHELTUIELI DE CAPITAL", total_2024="20"),
        ],
    })
    lines = assemble(store, [1], registry)[0].lines
    assert lines[0].func_code is None
    assert lines[-1].func_code == "51.02.01.03"


def test_embedded_header_context_applies_only_to_following_row(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT LA CHELTUIELI",
        "layout": "scan_simple_table",
        "lines": [
            _line(
                "710130",
                "Alte active fixe Capitolul 5402 Alte servicii publice generale",
                func="51.02.01.03",
                code="71.01.30",
                total_2024="10",
            ),
            _line("20", "Bunuri si servicii", total_2024="5"),
        ],
    })
    lines = assemble(store, [1], registry)[0].lines
    assert lines[0].func_code == "51.02.01.03"
    assert lines[1].func_code == "54.02"


def test_annex_rows_are_reported_outside_budget_quality_scope(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT",
        "layout": "scan_simple_table",
        "lines": [_line("000102", "TOTAL VENITURI", total_2024="100")],
    })
    store.put("extract", 2, {
        "text": "LISTA OBIECTIVELOR DE INVESTITII",
        "layout": "investment_list",
        "lines": [
            _line("12", "Reabilitare scoala", total_2024="50"),
            _line("12", "Modernizare parc", total_2024="75"),
        ],
    })
    result = ConversionResult(
        pdf="doc.pdf",
        documents=assemble(store, [1, 2], registry),
        pages_expected=2,
        pages_selected=[1, 2],
        pages_processed=[1, 2],
    )
    validate(result, registry)
    stats = result.stats()

    assert stats["quality_schema_version"] == 3
    assert stats["lines"] == 1
    assert stats["numeric_cells"] == 1
    assert stats["pct_lines_strictly_verified"] == 100.0
    assert stats["annex_lines"] == 2
    assert stats["annex_numeric_cells"] == 2
    assert not result.all_issues()


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


def test_revalidate_replaces_stale_findings_after_repair(tmp_path, registry):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT DE TEST",
        "lines": [
            _line("000102", "TOTAL VENITURI", total="99.00",
                  trim1="25.00", trim2="25.00", trim3="25.00", trim4="25.00"),
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1]))
    validate(result, registry)
    line = result.documents[0].lines[0]
    assert any(issue.check == "V3_row_checksum" for issue in line.issues)

    line.values["total"] = line.values["trim1"] * 4
    revalidate(result, registry)

    assert not any(issue.check == "V3_row_checksum" for issue in line.issues)


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


def test_official_prose_total_is_not_invalidated_by_an_explicitly_partial_summary(
    tmp_path, registry
):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL — sinteză oficială",
        "layout": "official_prose_summary",
        "lines": [
            {**_line("4902", "TOTAL CHELTUIELI", section="TOTAL", total_2024="100"),
             "source": "official_prose"},
            {**_line("6502", "Învățământ", section="TOTAL", total_2024="80"),
             "source": "official_prose"},
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1], registry))
    validate(result, registry)

    total = next(line for line in result.documents[0].lines if line.code == "49.02")
    assert not any(issue.check == "V4_hierarchy" for issue in total.issues)


def test_native_excel_partial_hierarchy_does_not_invalidate_source_cell(
    tmp_path, registry
):
    store = _mk_store(tmp_path)
    store.put("extract", 1, {
        "text": "BUGETUL LOCAL DETALIAT DE TEST",
        "lines": [
            _line("0302", "Impozit pe venit", total="10.00"),
            _line(
                "030218",
                "Impozitul pe veniturile din transferul proprietatilor",
                total="7.00",
            ),
        ],
    })
    result = ConversionResult(pdf="d", documents=assemble(store, [1]))
    for line in result.documents[0].lines:
        line.source = "native_excel"
        line.value_sources = {column: "native_excel" for column in line.values}

    validate(result, registry)

    assert not any(issue.check == "V4_hierarchy" for issue in result.all_issues())
    assert result.documents[0].lines[0].strictly_verified


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

    assert stats["numeric_cells"] == 0
    assert stats["numeric_cells_strictly_verified"] == 0
    assert stats["pct_lines_strictly_verified"] == 0.0
    assert stats["annex_numeric_cells"] == 62
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
