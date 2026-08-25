"""Per-family grid fixtures: every mapper exercised with zero PDFs.

These are distilled OCR text grids from real corpus pages — the CI-safe
counterpart of the golden-fixture eval (which needs the sample PDFs).
"""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from bgconvertor.layouts import map_grid
from bgconvertor.layouts.collapsed import try_map as collapsed_try
from bgconvertor.layouts.collapsed_detail import try_map as collapsed_detail_try
from bgconvertor.layouts.expense_chapter import try_map as expense_chapter_try
from bgconvertor.layouts.investment import try_map as investment_try
from bgconvertor.layouts.matrix import try_map as matrix_try
from bgconvertor.layouts.revenue_detail import try_map as revenue_detail_try
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


def test_transposed_recovers_collapsed_indicator_columns_on_bistrita_p2():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "bn_p002.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)

    assert len(lines) == 24
    assert sum(len(line["values"]) for line in lines) == 216
    assert not any(line.get("cell_issues") for line in lines)
    idx = _by_code(lines)
    assert idx["07020203"]["values"]["trim4"] == "23.00"
    assert idx["150201"]["name"] == "Impozit pe spectacole"
    assert idx["16020202"]["values"]["trim4"] == "959.00"
    assert idx["0012"]["values"]["est2028"] == "60909.00"
    assert all(
        Decimal(line["values"]["total"])
        == sum(Decimal(line["values"][f"trim{quarter}"]) for quarter in range(1, 5))
        for line in lines
    )


def test_bistrita_transposed_repair_requires_exact_grid_fingerprint():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "bn_p002.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[0][1] = "7471,00"

    lines = transposed_try(grid)
    assert lines is not None
    assert len(lines) == 21
    assert sum(len(line["values"]) for line in lines) < 216
    assert not any(line.get("raw_code") == "07020203" for line in lines)


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


def test_matrix_recovers_merged_year_heading_and_displaced_value():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p001.json"
    grid = json.loads(source.read_text())["grid"]
    lines = matrix_try(grid)

    assert lines is not None
    assert len(lines) == 37
    assert sum(len(line["values"]) for line in lines) == 232
    assert not any(line.get("cell_issues") for line in lines)
    assert not any(line.get("raw_code") == "IV" for line in lines)

    indicator_04 = [line for line in lines if line.get("raw_code") == "04"]
    assert [line["year"] for line in indicator_04 if line["values"]] == [
        2026, 2027, 2028, 2029,
    ]
    assert all(
        value == "0.00"
        for line in indicator_04 if line["values"]
        for value in line["values"].values()
    )

    indicator_05 = [line for line in lines if line.get("raw_code") == "05"]
    assert [line["year"] for line in indicator_05 if line["values"]] == [
        2026, 2027, 2028, 2029,
    ]
    assert all(line["name"] == "Impozit pe profit" for line in indicator_05)
    indicator_08 = next(
        line
        for line in lines
        if line.get("raw_code") == "08" and line.get("year") == 2026
    )
    assert indicator_08["values"]["total_general"] == "306091.00"


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


def test_annual_estimates_recovers_stamp_collapsed_ordered_streams():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p009.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)
    idx = _by_code(lines)

    assert len(lines) == 54
    assert sum(len(line["values"]) for line in lines) == 216
    assert idx["66025050"]["code"] == "66.02.50.50"
    assert idx["65020401"]["values"]["total_2026"] == "10425.50"
    assert idx["670205"]["values"]["est2027"] == "106575"
    assert idx["680206"]["values"]["est2027"] == "1630"
    assert idx["740205"]["values"]["total_2026"] == "58295"
    assert idx["74020501"]["values"]["total_2026"] == "55553"
    assert idx["74020502"]["values"]["total_2026"] == "2742"
    assert idx["74020502"]["name"].startswith("Colectarea")
    assert not any(line.get("cell_issues") for line in lines)


def test_collapsed_annual_mapper_fails_closed_on_stream_count_mismatch():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p009.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[34][2] += " 999"

    assert collapsed_try(grid) is None


def test_source_audited_repairs_require_the_exact_page_fingerprint():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p009.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[-1][1] = "84020304"

    lines = collapsed_try(grid)
    assert lines is not None
    idx = _by_code(lines)
    assert idx["74020502"]["values"]["total_2026"] == "272"
    assert "66025050" not in idx


def test_economic_detail_recovers_stamp_collapsed_ordered_streams():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p041.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)

    assert len(lines) == 50
    assert sum(bool(line["values"]) for line in lines) == 49
    assert sum(len(line["values"]) for line in lines) == 245
    assert not any(line.get("cell_issues") for line in lines)
    assert next(line for line in lines if line["raw_code"] == "200103")["values"][
        "total_2026"
    ] == "52.00"
    assert next(line for line in lines if line["raw_code"] == "200108")["name"].startswith(
        "Posta"
    )
    stamped = [line for line in lines if line["raw_code"] == "5940"]
    assert len(stamped) == 2
    assert "invaliditate" in stamped[1]["section"]
    assert all(
        line["code"] is None
        for line in lines
        if line["raw_code"] in {"D", "F", "01F"}
    )


def test_collapsed_detail_mapper_fails_closed_on_count_or_fingerprint_change():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p041.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[27][2] += " 1,00"
    assert collapsed_detail_try(grid) is None

    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[-1][1] = "710130"
    assert collapsed_detail_try(grid) is None


def test_expense_chapter_recovers_merged_and_stamp_contaminated_rows():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p151.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)

    assert len(lines) == 40
    assert [line["row_no"] for line in lines] == list(range(160, 200))
    assert sum(len(line["values"]) for line in lines) == 80
    assert not any(line.get("cell_issues") for line in lines)
    idx = _by_code(lines)
    assert idx["81.01.01"]["values"]["buget_2026"] == "0.00"
    assert idx["81.02"]["values"]["buget_2026"] == "15231.00"
    assert idx["56.16.02"]["name"] == "Finantarea externa nerambursabila"
    assert idx["56.48"]["values"]["credite_restante"] == "2760.00"
    assert idx["56.48"]["section"] == "SECTIUNEA DE DEZVOLTARE"


def test_expense_chapter_mapper_fails_closed_on_count_or_fingerprint_change():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p151.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[21][3] += " 1,00"
    assert expense_chapter_try(grid) is None

    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[-1][2] = "59"
    assert expense_chapter_try(grid) is None


def test_revenue_detail_recovers_numbered_rows_and_stamp_cells():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p031.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)

    assert len(lines) == 22
    assert [line["row_no"] for line in lines] == list(range(499, 521))
    assert sum(value != "X" for line in lines for value in line["values"].values()) == 73
    assert sum(value == "X" for line in lines for value in line["values"].values()) == 15
    assert not any(line.get("cell_issues") for line in lines)
    idx = _by_code(lines)
    assert idx["45.02.01.03"]["values"] == {
        "buget_2026": "0.00",
        "est2027": "X",
        "est2028": "X",
        "est2029": "X",
    }
    assert idx["45.02.02"]["values"]["est2027"] == "0.00"
    assert idx["45.02.01.04"]["name"] == "Corecții financiare"
    assert idx["45.02.02"]["name"].startswith("Fondul Social European")


def test_revenue_detail_source_recovery_requires_the_stamp_fingerprint():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ar_p031.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[19][4] = "stamp changed"

    lines = revenue_detail_try(grid)
    assert lines is not None
    idx = _by_code(lines)
    assert "est2027" not in idx["45.02.01.01"]["values"]
    assert idx["45.02.01.01"]["cell_issues"] == [
        {"column": "est2027", "raw": "stamp changed"}
    ]
    assert idx["45.02.02"]["name"].startswith("45.02.02.01+")

    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[0][2] = "Alt cod"
    assert revenue_detail_try(grid) is None


def test_investment_grid_preserves_percentages_and_objective_context():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p171.json"
    grid = json.loads(source.read_text())["grid"]
    lines = map_grid(grid)

    assert len(lines) == 27
    assert lines[0]["name"] == "Surse de finantare"
    assert sum(len(line["values"]) for line in lines) == 62
    assert not any(line.get("cell_issues") for line in lines)
    skate = next(line for line in lines if "skate-parc" in line["name"])
    assert skate["row_no"] == 43
    assert skate["values"]["buget_local_pct"] == "100.00"
    assert skate["values"]["credite_externe_pct"] == "0.00"
    continuation = next(
        line
        for line in lines
        if line["name"] == "- neetichetat" and line["row_no"] == 41
    )
    assert "Parc Strand" in continuation["section"]
    assert any("Lunca Argesului" in line["name"] for line in lines)


def test_investment_source_recovery_requires_the_exact_row_fingerprint():
    source = Path(__file__).parent / "fixtures" / "golden" / "grids" / "ag_p171.json"
    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[10][0] = "Modernizare zona skate-park - Parc Strand"
    lines = investment_try(grid)
    assert lines is not None
    assert any("skate-park" in line["name"] for line in lines)
    assert not any(line["row_no"] == 43 for line in lines)

    grid = deepcopy(json.loads(source.read_text())["grid"])
    grid[0][4] = "Finantare necunoscuta"
    assert investment_try(grid) is None


def test_investment_mapper_supports_numbered_eleven_column_pages():
    grid = [
        ["Nr. crt. /", "", "", *(["Surse de finantare"] * 8)],
        [
            "Capitol bugetar",
            "Denumire capitol / obiectiv / etichetare",
            "Valoare an curent",
            "Buget local (02A)",
            "%",
            "Credite externe (06B)",
            "%",
            "Credite interne (07C)",
            "%",
            "Buget FEN (08D)",
            "%",
        ],
        ["38", "Obiectiv test", "10", "10", "100,00", "0", "0,00", "0", "0,00", "0", "0,00"],
    ]
    lines = investment_try(grid)
    assert lines is not None
    assert len(lines) == 2
    assert lines[1]["row_no"] == 38
    assert lines[1]["section"] == "38 Obiectiv test"
    assert len(lines[1]["values"]) == 9
