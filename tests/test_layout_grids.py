"""Per-family grid fixtures: every mapper exercised with zero PDFs.

These are distilled OCR text grids from real corpus pages — the CI-safe
counterpart of the golden-fixture eval (which needs the sample PDFs).
"""

import json
from pathlib import Path

from bgconvertor.layouts import map_grid
from bgconvertor.layouts.matrix import try_map as matrix_try
from bgconvertor.layouts.transposed import try_map as transposed_try


def _by_code(lines):
    return {ln["raw_code"]: ln for ln in lines if ln.get("raw_code")}


def test_generic_header_table_romanian():
    grid = [
        ["DENUMIREA INDICATORILOR", "Cod indicator", "Buget 2026 PREVEDERI ANUALE TOTAL", "Estimari 2027"],
        ["Ordine publica", "610203", "21.303,00", "21.303,00"],
        ["Politie locala", "61020304", "21.303,00", "21.303,00"],
    ]
    idx = _by_code(map_grid(grid))
    assert idx["610203"]["code"] == "61.02.03"
    assert idx["610203"]["values"]["total_2026"] == "21303.00"
    assert idx["61020304"]["values"]["est2027"] == "21303.00"


def test_us_locale_and_dotted_codes():
    grid = [
        ["DENUMIREA INDICATORILOR", "Cod indicator", "Nr. Rand", "PREVEDERI ANUALE TOTAL"],
        ["Transferuri curente", "51.01", "269", "19,809.00"],
        ["Transferuri din bugetele locale", "51.01.46", "270", "19,809.00"],
    ]
    idx = _by_code(map_grid(grid))
    assert idx["51.01"]["values"]["total_2026"] == "19809.00"
    assert idx["51.01.46"]["code"] == "51.01.46"


def test_headerless_positional_code_first():
    grid = [["990296", "Deficitul sectiunii", "231,18", "231,88", "232,91", "233,95"]] + [
        [f"65020{i}", f"Indicator {i}", "100,00", "x", "1x", "x1"] for i in range(1, 9)
    ]
    idx = _by_code(map_grid(grid))
    assert idx["990296"]["values"]["total_2026"] == "231.18"
    # x-marker zoo ('x', '1x', 'x1') normalizes to the X marker
    assert idx["650201"]["values"]["total_2026"] == "100.00"
    assert idx["650201"]["values"]["est2027"] == "X"
    assert idx["650201"]["values"]["est2029"] == "X"


def test_headerless_positional_name_first():
    grid = [["Constructii", "71.01.01", "300,00", "x", "x", "x"]] + [
        [f"Indicator {i}", f"71.01.{i:02d}", "50,00", "x", "x", "x"] for i in range(2, 9)
    ]
    idx = _by_code(map_grid(grid))
    assert idx["71.01.01"]["name"] == "Constructii"
    assert idx["71.01.01"]["values"]["total_2026"] == "300.00"


def test_combined_capitol_economic_codes():
    grid = [
        ["DENUMIREA INDICATORILOR", "Cod indicator", "PREVEDERI ANUALE TOTAL"],
        ["Cheltuieli salariale", "7002.1001", "3.882,00"],
        ["Salarii de baza", "7002.100101", "3.663,00"],
    ]
    idx = _by_code(map_grid(grid))
    assert idx["7002.1001"]["code"] == "10.01"
    assert idx["7002.1001"]["func_code"] == "70.02"


def test_transposed_family():
    grid = [
        ["2029", "7.470,00", "4.000,00"],
        ["2028", "7.360,00", "3.950,00"],
        ["2027", "7.250,00", "3.900,00"],
        ["Trim. IV", "1.386,00", "833,00"],
        ["Trim. III", "1.889,00", "1.000,00"],
        ["Trim. II", "1.770,00", "1.000,00"],
        ["Trim. I", "2.100,00", "1.000,00"],
        ["TOTAL", "7.145,00", "3.833,00"],
        ["Cod", "070202", "07020201"],
        ["Denumirea indicatorilor", "Impozit teren", "Teren PF"],
    ]
    lines = transposed_try(grid)
    assert lines is not None
    idx = _by_code(lines)
    assert idx["070202"]["values"]["total"] == "7145.00"
    assert idx["070202"]["values"]["trim4"] == "1386.00"
    assert idx["070202"]["values"]["est2029"] == "7470.00"
    # row checksum holds: 2100+1770+1889+1386 = 7145
    assert map_grid(grid) == lines  # registry dispatches to transposed


def test_matrix_family_with_index_row():
    grid = [
        ["", "Cod rând", "Bugetul local", "Total buget general"],
        ["A", "0", "1", "8=6-7"],
        ["VENITURI TOTAL", "01", "", ""],
        ["2026", "1", "1.250.646,50", "1.373.387,50"],
        ["2027", "II", "969.696,37", "1.037.543,37"],
    ]
    lines = matrix_try(grid)
    assert lines is not None
    y2026 = next(ln for ln in lines if ln.get("year") == 2026)
    assert y2026["values"]["bugetul_local"] == "1250646.50"
    assert y2026["values"]["total_general"] == "1373387.50"
    assert y2026["name"].startswith("VENITURI TOTAL")


def test_transposed_rejects_normal_tables():
    grid = [
        ["DENUMIREA", "Cod", "TOTAL"],
        ["Ceva", "610203", "5,00"],
    ]
    assert transposed_try(grid) is None
    assert matrix_try(grid) is None


def test_institution_budget_recovers_collapsed_code_value_streams():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p301.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)
    numeric = [line for line in lines if line["values"]]

    assert len(numeric) == 51
    assert len({line["section"].split(" — ")[0] for line in numeric}) == 3
    assert next(
        line for line in numeric
        if "Stiinte Aplicate" in line["section"] and line["raw_code"] == "20.01"
    )["values"]["buget_2026"] == "68.00"
    assert next(
        line for line in numeric
        if "Francisc Neuman" in line["section"] and line["raw_code"] == "01"
    )["values"]["buget_2026"] == "458.00"
    assert all(line["code"] is None for line in numeric if line["raw_code"] == "96")
    assert all(
        line["func_code"] == "65.10"
        for line in numeric
        if line["raw_code"] in {"01", "20", "90", "93.01", "93.01.96"}
    )
    assert not any(line.get("cell_issues") for line in numeric)
