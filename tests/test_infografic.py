"""The chart-ready `infografic` block in analysis.json, on a synthetic budget."""

from decimal import Decimal

import pytest

from bgconvertor.analysis import city_analysis, infografic
from bgconvertor.model import BudgetDocument, BudgetLine, ConversionResult


def _ln(kind, code, name, section, total, raw=None, trim=None, est=None):
    values = {"total": Decimal(str(total))}
    for i, v in enumerate(trim or []):
        values[f"trim{i + 1}"] = Decimal(str(v))
    for i, v in enumerate(est or []):
        values[f"est{2027 + i}"] = Decimal(str(v))
    return BudgetLine(code=code, raw_code=raw or code.replace(".", ""), name=name,
                      kind=kind, page=1, section=section, values=values)


def _result():
    lines = [
        _ln("revenue", "00.01", "TOTAL VENITURI", "TOTAL", 1000, raw="000102",
            trim=[400, 300, 200, 100], est=[900, 850, 800]),
        _ln("revenue", "04.02", "Cote din impozitul pe venit (cod 04.02.01+04.02.04)", "TOTAL", 600),
        _ln("revenue", "42.02", "Subventii de la bugetul de stat", "TOTAL", 300),
        _ln("revenue", "45.02", "Sume primite de la UE", "TOTAL", 100),
        _ln("expense_functional", "50.02", "TOTAL CHELTUIELI", "TOTAL", 1000,
            trim=[400, 300, 200, 100], est=[900, 850, 800]),
        _ln("expense_functional", "50.02", "SECTIUNEA FUNCTIONARE", "FUNCTIONARE", 700,
            trim=[200, 200, 150, 150]),
        _ln("expense_functional", "50.02", "SECTIUNEA DEZVOLTARE", "DEZVOLTARE", 300,
            trim=[200, 100, 50, -50]),
        _ln("expense_functional", "63.02", "Partea a III-a CHELTUIELI SOCIAL-CULTURALE", "TOTAL", 650),
        _ln("expense_functional", "65.02", "CAP. Invatamant", "TOTAL", 650),
        _ln("expense_functional", "65.02", "CAP. Invatamant", "FUNCTIONARE", 500),
        _ln("expense_functional", "65.02", "CAP. Invatamant", "DEZVOLTARE", 150),
        _ln("expense_functional", "65.02.03", "Invatamant prescolar", "TOTAL", 400),
        _ln("expense_functional", "84.02", "CAP. Transporturi", "TOTAL", 350),
    ]
    doc = BudgetDocument(title="BUGET LOCAL", budget="local", suffix="02",
                         pages=[1], lines=lines)
    return ConversionResult(pdf="x.pdf", documents=[doc])


def test_infografic_blocks():
    ig = infografic(_result())
    assert ig is not None

    ven = ig["venituri"]
    assert ven["total"] == 1000 and ven["acoperire_pct"] == 100.0
    assert [s["grup"] for s in ven["surse"]] == ["proprii", "stat", "ue"]
    assert ven["surse"][0]["nume"] == "Cote din impozitul pe venit"  # "(cod …)" stripped

    caps = ig["capitole"]
    assert [c["cod"] for c in caps] == ["65.02", "84.02"]  # 63.02 "Partea" excluded
    inv = caps[0]
    assert inv["nume"] == "Invatamant" and inv["func"] == 500 and inv["dezv"] == 150
    assert inv["copii"] == [{"nume": "Invatamant prescolar", "val": 400.0}]

    assert ig["sectiuni"] == {"functionare": 700.0, "dezvoltare": 300.0}
    assert ig["trim"]["venituri"] == [400, 300, 200, 100]
    assert ig["ani"]["cheltuieli"] == [1000, 900, 850, 800]


def test_top_capitole_excludes_aggregates():
    a = city_analysis(_result())
    codes = [c["code"] for c in a["top_capitole"]]
    assert "50.02" not in codes and "63.02" not in codes
    assert codes[0] == "65.02"
    assert a["infografic"] is not None


def test_infografic_without_sections():
    """Most scanned budgets carry no FUNCTIONARE/DEZVOLTARE split: section is
    None everywhere. None counts as TOTAL; the split blocks are simply absent."""
    r = _result()
    for ln in r.documents[0].lines:
        ln.section = None
    ig = infografic(r)
    assert ig is not None
    assert [c["cod"] for c in ig["capitole"]] == ["65.02", "84.02"]
    assert ig["venituri"]["acoperire_pct"] == 100.0
    assert "sectiuni" not in ig and "trim" not in ig
    assert "func" not in ig["capitole"][0]


def test_infografic_absent_when_coverage_poor():
    r = _result()
    # drop every chapter row -> nothing to chart
    r.documents[0].lines = [ln for ln in r.documents[0].lines
                            if ln.code in ("00.01", "50.02")]
    assert infografic(r) is None


# ---- detecția totalurilor (faza 1) ----

@pytest.mark.parametrize("raw,code,expected", [
    ("000102", "00.01", True),    # cod compact, cum apare la Alba Iulia
    ("00.01", "00.01", True),     # cod cu punct — varianta care era ignorată
    ("0001", "00.01", True),
    ("04.02", "04.02", False),    # un capitol de venit oarecare
])
def test_is_total_venituri_accepts_every_spelling(raw, code, expected):
    from bgconvertor.analysis import _is_total_venituri
    ln = BudgetLine(code=code, raw_code=raw, name="TOTAL VENITURI", kind="revenue", page=1)
    assert _is_total_venituri(ln) is expected


@pytest.mark.parametrize("section,expected", [
    (None, True), ("TOTAL", True),
    ("Varsaminte din sectiunea de functionare", True),  # antet parazit, nu secțiune
    ("FUNCTIONARE", False), ("DEZVOLTARE", False), ("dezvoltare", False),
])
def test_section_field_is_trusted_only_when_it_names_a_section(section, expected):
    from bgconvertor.analysis import _is_total_section
    ln = BudgetLine(code="65.02", name="Invatamant", kind="expense_functional",
                    page=1, section=section)
    assert _is_total_section(ln) is expected


def test_total_smaller_than_its_parts_is_rejected():
    """Botoșani: rândul «TOTAL VENITURI» citit ca 950, cu capitole de 173.000."""
    from bgconvertor.analysis import _plausible
    assert _plausible(950.0, [173052.0, 90000.0]) is None
    assert _plausible(400000.0, [173052.0, 90000.0]) == 400000.0
    assert _plausible(None, [1.0]) is None
    assert _plausible(5.0, []) == 5.0  # fără capitole nu există contradicție


def test_totals_found_with_dotted_codes_and_stray_sections():
    """Un document ca Slobozia: cod cu punct și secțiune plină de text parazit."""
    lines = [
        _ln("revenue", "00.01", "TOTAL VENITURI", "Varsaminte din sectiunea de functionare", 1000),
        _ln("revenue", "04.02", "Cote defalcate", None, 600),
        _ln("expense_functional", "65.02", "CAP. Invatamant", "Varsaminte din sectiunea", 650),
        _ln("expense_functional", "84.02", "CAP. Transporturi", None, 350),
    ]
    lines[0].raw_code = "00.01"
    doc = BudgetDocument(title="BUGET LOCAL", budget="local", suffix="02", pages=[1], lines=lines)
    a = city_analysis(ConversionResult(pdf="x.pdf", documents=[doc]))
    assert a["totals_mii_lei"]["venituri"] == 1000
    assert [c["code"] for c in a["top_capitole"]] == ["65.02", "84.02"]


def test_no_derived_expense_total_is_invented():
    """Capitolele parțiale nu devin un total: mai bine absent decât greșit."""
    lines = [
        _ln("expense_functional", "65.02", "CAP. Invatamant", None, 650),
        _ln("expense_functional", "84.02", "CAP. Transporturi", None, 350),
        _ln("expense_functional", "67.02", "CAP. Cultura", None, 100),
    ]
    doc = BudgetDocument(title="BUGET", budget="local", suffix="02", pages=[1], lines=lines)
    a = city_analysis(ConversionResult(pdf="x.pdf", documents=[doc]))
    assert a["totals_mii_lei"]["cheltuieli"] is None
    assert len(a["top_capitole"]) == 3
