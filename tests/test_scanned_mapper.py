"""Mapper tests on synthetic text grids (no OCR involved)."""

from bgconvertor.extract.scanned import map_table


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
