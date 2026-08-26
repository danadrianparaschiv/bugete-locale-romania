"""Mapper tests on synthetic text grids (no OCR involved)."""

from bgconvertor.extract.scanned import map_payload, map_table


def _grid(rows):
    return rows


HEADER = [
    ["DENUMIREA INDICATORILOR", "Cod Indicator", "Buget 2026", "Estimari", "Estimari", "Estimari"],
    ["DENUMIREA INDICATORILOR", "Cod Indicator", "PREVEDERI ANUALE TOTAL", "2027", "2028", "2029"],
]


def test_simple_table_mapping():
    t = _grid(HEADER + [
        ["Ordine publica (cod 61.02.03.04)", "610203", "21303", "21303", "21303", "21303"],
        ["Invatamant", "6502", "48152.87", "30297", "30878", "31260"],
        ["Invatamant prescolar", "650203", "4418,37", "723", "742", "761"],
    ])
    lines = map_table(t)
    assert len(lines) == 3
    assert lines[0]["raw_code"] == "610203"
    assert lines[0]["code"] == "61.02.03"
    assert lines[0]["values"]["total_2026"] == "21303"
    assert lines[0]["values"]["est2029"] == "21303"
    # OCR-misread decimal dot normalized
    assert lines[1]["values"]["total_2026"] == "48152.87"
    assert lines[2]["values"]["total_2026"] == "4418.37"


def test_section_context_row_and_continuation():
    t = _grid(HEADER + [
        ["68020502 Asistenta sociala in caz de invaliditate", "", "", "", "", ""],
        ["Sume aferente persoanelor cu handicap", "5940", "4,00", "0,00", "0,00", "0,00"],
        ["neincadrate", "", "", "", "", ""],
    ])
    lines = map_table(t)
    data = [ln for ln in lines if ln["raw_code"]]
    assert len(data) == 1
    ln = data[0]
    assert ln["section"] and "invaliditate" in ln["section"]
    assert ln["name"].endswith("neincadrate")  # continuation row merged
    assert ln["values"]["total_2026"] == "4.00"


def test_unparseable_cell_becomes_issue_not_zero():
    t = _grid(HEADER + [
        ["Sanatate", "6602", "66+9", "12790", "12790", "12790"],
    ])
    ln = map_table(t)[0]
    assert "total_2026" not in ln["values"]
    assert ln["cell_issues"][0]["column"] == "total_2026"
    assert ln["values"]["est2027"] == "12790"


def test_pseudo_codes_do_not_normalize():
    t = _grid(HEADER + [
        ["CHELTUIELI DE CAPITAL", "D", "8,00", "0,00", "0,00", "0,00"],
    ])
    ln = map_table(t)[0]
    assert ln["raw_code"] == "D"
    assert ln["code"] is None


def test_budget_and_forecast_years_are_taken_from_the_document():
    grid = [
        ["DENUMIREA", "Cod indicator", "Buget 2025", "Estimari", "Estimari", "Estimari"],
        ["DENUMIREA", "Cod indicator", "PREVEDERI ANUALE", "2026", "2027", "2028"],
        ["Invatamant", "6502", "100", "101", "102", "103"],
    ]
    line = map_payload({"tables_raw": [grid]}, budget_year=2025)["lines"][0]
    assert line["values"] == {
        "total_2025": "100",
        "est2026": "101",
        "est2027": "102",
        "est2028": "103",
    }


def test_economic_codes_are_not_mistaken_for_transposed_year_rows():
    grid = [
        ["Cod", "Denumire indicator", "Propuneri Buget 2025", "2026", "Estimari 2027", "2028"],
        ["A", "B", "1", "2", "3", "4"],
        ["4902", "TOTAL CHELTUIELI", "3181", "1614", "1667", "1718"],
        ["2001", "Bunuri si servicii", "1224", "1300", "1350", "1400"],
        ["2002", "Reparatii curente", "100", "110", "120", "130"],
        ["2003", "Hrana", "625", "630", "640", "650"],
        ["2005", "Obiecte de inventar", "105", "110", "115", "120"],
    ]
    payload = map_payload({"tables_raw": [grid]}, budget_year=2025)
    assert payload["layout"] == "scan_simple_table"
    indexed = {line["raw_code"]: line for line in payload["lines"]}
    assert indexed["2001"]["values"] == {
        "total_2025": "1224",
        "est2026": "1300",
        "est2027": "1350",
        "est2028": "1400",
    }


def test_merged_code_name_header_keeps_data_columns_separate():
    grid = [
        ["", "Cod Denumire indicator", "Propuneri", "Estimari", "Estimari", "Estimari"],
        ["", "", "Buget 2025", "2026", "2027", "2028"],
        ["A", "B", "1", "2", "3", "4"],
        ["4902", "TOTAL CHELTUIELI", "520,85", "539,58", "556,06", "574,14"],
        ["5702", "Ajutoare sociale", "42,29", "43,19", "44,40", "45,78"],
        ["570201", "Ajutoare sociale in numerar", "37,29", "38,00", "39,00", "40,00"],
    ]
    payload = map_payload({"tables_raw": [grid]}, budget_year=2025)
    assert len(payload["lines"]) == 3
    assert payload["lines"][0]["raw_code"] == "4902"
    assert payload["lines"][0]["name"] == "TOTAL CHELTUIELI"
    assert payload["lines"][0]["values"]["total_2025"] == "520.85"


def test_header_schema_is_propagated_to_a_continuation_page():
    first = map_payload({"tables_raw": [[
        ["Denumire", "Cod", "Buget 2025", "Estimare 2026", "Estimare 2027"],
        ["Invatamant", "6502", "100", "101", "102"],
        ["Sanatate", "6602", "50", "51", "52"],
    ]]}, budget_year=2025)
    continuation = map_payload({"tables_raw": [[
        ["Cultura", "6702", "10", "11", "12"],
        ["Asistenta", "6802", "20", "21", "22"],
        ["Locuinte", "7002", "30", "31", "32"],
        ["Mediu", "7402", "40", "41", "42"],
        ["Energie", "8102", "50", "51", "52"],
        ["Transport", "8402", "60", "61", "62"],
    ]]}, budget_year=2025, context=first["mapping_context"])
    line = continuation["lines"][0]
    assert line["raw_code"] == "6702"
    assert line["values"] == {
        "total_2025": "10", "est2026": "11", "est2027": "12"
    }


def test_cluj_annual_total_keeps_row_number_and_budget_code_separate():
    grid = [
        ["Denumirea indicatorului bugetar", "", "Cod rand Indicator bugetar", "Total (T1+T2+T3+T4)"],
        ["Deplasari interne", "254", "20.06.01", "522.000"],
        ["Deplasari externe", "255", "20.06.02", "40.000"],
        ["Publicatii", "256", "20.11", "105.000"],
        ["Pregatire", "257", "20.13", "231.000"],
        ["Protectia muncii", "258", "20.14", "5.000"],
    ]
    payload = map_payload({"tables_raw": [grid]}, budget_year=2026)
    assert payload["layout"] == "scan_annual_total"
    assert payload["lines"][0]["row_no"] == 254
    assert payload["lines"][0]["raw_code"] == "20.06.01"
    assert payload["lines"][0]["values"] == {"total_2026": "522000"}


def test_cluj_annual_total_inherits_schema_when_continuation_header_is_damaged():
    first = map_payload({"tables_raw": [[
        ["Denumirea indicatorului", "", "Cod rand Indicator", "Total (T1+T2+T3+T4)"],
        ["Deplasari", "254", "20.06", "522.000"],
        ["Publicatii", "255", "20.11", "105.000"],
        ["Pregatire", "256", "20.13", "231.000"],
    ]]}, budget_year=2026)
    continuation = map_payload({"tables_raw": [[
        ["Demamirea indicaturului", "Cod rund", "Indicator", "Tutal"],
        ["Protectia muncii", "257", "20.14", "5.000"],
    ]]}, budget_year=2026, context=first["mapping_context"])
    assert continuation["layout"] == "scan_annual_total"
    assert continuation["lines"][0]["raw_code"] == "20.14"
    assert continuation["lines"][0]["row_no"] == 257
    assert continuation["lines"][0]["values"] == {"total_2026": "5000"}


def test_cluj_annual_total_inherits_schema_when_header_is_absent():
    first = map_payload({"tables_raw": [[
        ["Denumire", "", "Cod rand", "Total"],
        ["Deplasari", "254", "20.06", "522.000"],
        ["Publicatii", "255", "20.11", "105.000"],
        ["Pregatire", "256", "20.13", "231.000"],
    ]]}, budget_year=2026)
    continuation = map_payload({"tables_raw": [[
        ["Protectia muncii", "257", "20.14", "5.000"],
        ["Chirie", "258", "20.30", "8.000"],
    ]]}, budget_year=2026, context=first["mapping_context"])
    assert [line["row_no"] for line in continuation["lines"]] == [257, 258]
    assert continuation["lines"][0]["values"] == {"total_2026": "5000"}


def test_cluj_single_row_annual_page_is_detected_from_its_header():
    payload = map_payload({"tables_raw": [[
        ["Denumirea indicatorului", "Cod rand", "Indicator bugetar", "Total (T1+T2+T3+T4)"],
        ["Finantare complementara", "158", "65.02A.04.02.02", "641.250"],
    ]]}, budget_year=2026)
    assert payload["layout"] == "scan_annual_total"
    assert payload["lines"][0]["values"] == {"total_2026": "641250"}


def test_cluj_wide_investment_program_is_out_of_budget_nomenclator_scope():
    payload = map_payload({"tables_raw": [[
        ["Denumire subcapitol", "Nominalizarea pe obiective de investitii", "Total obiectiv", "Total 2026", "2027", "2028", "2029"],
        ["65C", "Reabilitare scoala", "100", "50", "20", "20", "10"],
    ]]}, budget_year=2026)
    assert payload["layout"] == "investment_list"


def test_cluj_consolidated_summary_maps_all_seven_value_columns():
    grid = [
        ["Denumirea Indicatorilor", "Cod rand", "Bugetul local", "Buget institutii", "Credite interne", "Fonduri externe nerambursabile", "Total", "Transferuri intre bugete", "Total buget general"],
        ["A", "0", "1", "2", "3", "4", "5=1+2+3+4", "6", "7=5-6"],
        ["VENITURI TOTAL", "01", "100", "20", "3", "4", "127", "7", "120"],
    ]
    payload = map_payload({"tables_raw": [grid]}, budget_year=2026)
    assert payload["layout"] == "scan_general_matrix"
    assert payload["lines"][0]["row_no"] == 1
    assert payload["lines"][0]["values"] == {
        "buget_local": "100",
        "inst_venituri_proprii_subventii": "20",
        "credite_interne": "3",
        "fonduri_externe": "4",
        "total": "127",
        "transferuri": "7",
        "total_general": "120",
    }
