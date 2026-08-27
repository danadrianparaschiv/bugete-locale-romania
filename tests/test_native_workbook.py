from decimal import Decimal

from openpyxl import Workbook

from bgconvertor.native_workbook import (
    comparative_payload,
    consolidated_payload,
    convert_workbook,
    read_sheets,
)
from bgconvertor.nomenclator import load_registry_for_year


def _standard_workbook(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Raport buget"
    sheet.append(["Titlu raport:", "BUGETUL DETALIAT PE ANUL 2024"])
    sheet.append(["Sursa de finanțare:", "02A"])
    sheet.append(["Mod afișare sume:", "(lei)"])
    sheet.append([])
    sheet.append([
        "Denumirea indicatorului bugetar",
        "Cod rand",
        "Indicator bugetar",
        "Total (T1+T2+T3+T4)",
    ])
    sheet.append(["SECTIUNEA TOTAL", None, None, None])
    sheet.append(["TOTAL VENITURI", 1, 0.01, 1_000_000])
    sheet["C7"].number_format = "00.00"
    sheet.append(["Cote si sume defalcate", 2, "04.02", 1_000_000])
    sheet.append(["TOTAL CHELTUIELI", 3, "50.02", 1_000_000])
    sheet.append(["Autoritati publice", 4, "51.02", 1_000_000])
    workbook.save(path)


def test_native_xlsx_preserves_displayed_codes_and_normalizes_lei(
    tmp_path, reference_dir
):
    path = tmp_path / "data" / "2024" / "city" / "buget_orig.xlsx"
    path.parent.mkdir(parents=True)
    _standard_workbook(path)

    name, grid = read_sheets(path)[0]
    assert name == "Raport buget"
    assert grid[6][2] == "00.01"

    registry = load_registry_for_year(reference_dir, 2024)
    result = convert_workbook(path, 2024, registry)
    assert result.stats()["scope"]["complete_pdf"] is True
    assert result.documents[0].suffix == "02"
    total = next(
        line for line in result.documents[0].lines if line.code == "00.01"
    )
    assert total.values["total_2024"] == Decimal("1000")
    assert total.source == "native_excel"
    assert total.value_sources == {"total_2024": "native_excel"}


class _Registry:
    codes = {"03.02.18", "04.02.01", "51.02"}

    def exists(self, code):
        return code in self.codes


def test_comparative_sheet_uses_only_current_year_and_registry_backed_codes():
    grid = [
        ["PROIECTUL BUGETULUI LOCAL AL MUNICIPIULUI"],
        ["SATU MARE PE ANUL 2024 - SECTIUNEA DE FUNCTIONARE"],
        ["VENITURI", "BUGET 2023", "BUGET 2024"],
        ["Impozit pe transferul proprietatilor -031800-", "10", "2000000"],
        ["Cote defalcate -040100-", "20", "3000000"],
        ["Total venituri", "30", "5000000"],
        ["CHELTUIELI", "", ""],
        ["Cap. 51 Autoritati publice", "40", "5000000"],
        ["Total cheltuieli", "40", "5000000"],
    ]
    payload = comparative_payload("buget initial", grid, 2024, _Registry())
    assert payload is not None
    by_code = {line["code"]: line for line in payload["lines"]}
    assert by_code["00.01"]["values"] == {"total_2024": "5000"}
    assert by_code["03.02.18"]["values"] == {"total_2024": "2000"}
    assert by_code["04.02.01"]["values"] == {"total_2024": "3000"}
    assert by_code["51.02"]["values"] == {"total_2024": "5000"}


def test_consolidated_mapper_uses_local_budget_and_ignores_form_row_codes():
    grid = [
        ["BUGETUL GENERAL CONSOLIDAT"],
        ["", "Cod rând", "Bugetul local", "Alte bugete"],
        ["VENITURI TOTAL", "01", "559052437", "1"],
        ["Cote si sume defalcate din impozitul pe venit", "08", "149574000", "2"],
        ["CHELTUIELI - TOTAL", "23", "577911194", "3"],
        ["PE CAPITOLE:", "42", "577911194", "3"],
        ["Invatamant", "61", "106680741", "4"],
        ["Sectiunea de functionare", "62", "31396000", "5"],
        ["Sectiunea de dezvoltare", "63", "75284741", "6"],
    ]

    payload = consolidated_payload("05.02.2024", grid, 2024)

    assert payload["layout"] == "native_excel_consolidated"
    by_code_section = {
        (line["code"], line["section"]): line for line in payload["lines"]
    }
    assert by_code_section[("00.01", "TOTAL")]["values"]["total_2024"] == \
        "559052.437"
    assert by_code_section[("04.02", "TOTAL")]["raw_code"] == "0402"
    assert by_code_section[("65.02", "TOTAL")]["values"]["total_2024"] == \
        "106680.741"
    assert by_code_section[("65.02", "FUNCTIONARE")]["values"]["total_2024"] == \
        "31396"
    assert all(line["code"] != "61" for line in payload["lines"])
