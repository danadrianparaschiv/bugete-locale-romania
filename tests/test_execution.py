"""Forexebug execution reports: code mapping, parsing, validation, snapshot."""

import json

import pytest
from openpyxl import Workbook

from bgconvertor import execution as ex
from bgconvertor.aggregate import PLAN_RATIO_LIMIT, build_aggregate

HEADER = ["Tip Indicator", "Sursa finantare", "Clasificatie Functionala",
          "Clasificatie Functionala Descriere", "Clasificatie Economica",
          "Clasificatie Economica Descriere", "Sectiune", "Executie Cumulat"]

ROWS = [
    # tip, sursa, func, func_desc, econ, econ_desc, sectiune, lei
    [" Venit", "A-Integral de la buget", "040100", "Cote defalcate din impozitul pe venit",
     None, None, "F - FUNCTIONARE", 600_000.0],
    [" Venit", "A-Integral de la buget", "420288", "Alocari din PNRR",
     None, None, "D - DEZVOLTARE", 400_000.0],
    [" Venit", "G-Venituri proprii si subventii", "330800", "Venituri din prestari de servicii",
     None, None, "F - FUNCTIONARE", 50_000.0],
    [" Cheltuiala", "A-Integral de la buget", "650300", "Invatamant prescolar si primar",
     "100101", "Salarii de baza", "F - FUNCTIONARE", 700_000.0],
    [" Cheltuiala", "A-Integral de la buget", "840300", "Transport rutier",
     "710101", "Construcţii", "D - DEZVOLTARE", 200_000.0],
    [" Cheltuiala", "E-Activitati finantate integral din venituri proprii", "650300",
     "Invatamant prescolar si primar", "200101", "Furnituri de birou",
     "F - FUNCTIONARE", 25_000.0],
]


def _workbook(path, rows=ROWS, totals=(1_050_000.0, 925_000.0), spacer=False):
    """Write a Forexebug-shaped workbook; `spacer` mimics the report's Sheet1."""
    wb = Workbook()
    ws = wb.active
    ws.append(["MINISTERUL FINANTELOR"])
    ws.append(["RAPORT DE EXECUTIE BUGETARA COFOG3"])
    ws.append(["LA DATA: 30-JUN-26"])
    ws.append(["Sector bugetar: 02 - Bugetul local   (administratie locala)"])
    ws.append(["Cod Fiscal IP:   4562923 Denumire IP : MUNICIPIUL TEST"])

    def out(row):
        if not spacer or len(row) < 3:
            return row
        return [row[0], row[1], None, *row[2:]]  # extra empty column, as in Sheet1

    ws.append(out(HEADER))
    for r in rows:
        ws.append(out(r))
    if totals[0] is not None:
        ws.append(out([" TOTAL VENITURI:", None, None, None, None, None, None, totals[0]]))
        ws.append(out([" TOTAL CHELTUIELI:", None, None, None, None, None, None, totals[1]]))
    ws.append(out(["FXB-EXB-901"]))
    wb.save(path)
    return path


@pytest.mark.parametrize("code,kind,budget,expected", [
    ("510103", "expense_functional", "local", "51.02.01.03"),
    ("650300", "expense_functional", "local", "65.02.03"),
    ("650300", "expense_functional", "own_revenue", "65.10.03"),
    ("040100", "revenue", "local", "04.02.01"),
    ("100101", "expense_economic", "local", "10.01.01"),
    ("100000", "expense_economic", "local", "10"),
    (None, "revenue", "local", None),
])
def test_dotted_code(code, kind, budget, expected):
    assert ex.dotted_code(code, kind, budget) == expected


@pytest.mark.parametrize("spacer", [False, True])
def test_parse_report(tmp_path, spacer):
    """Columns are bound by header label, so both sheet layouts parse alike."""
    rep = ex.parse_report(_workbook(tmp_path / "e.xlsx", spacer=spacer), quarter=2)
    assert rep.issues == []
    assert rep.report_date == "2026-06-30"
    assert rep.entity_cif == "4562923" and rep.entity_name == "MUNICIPIUL TEST"
    assert len(rep.lines) == 6

    # values converted lei -> mii lei; own-revenue sources kept apart
    assert rep.total("revenue", "local") == 1000.0
    assert rep.total("revenue", "own_revenue") == 50.0
    assert rep.total("expense_functional", "local") == 900.0

    inv = next(ln for ln in rep.lines if ln.code == "65.02.03")
    assert inv.econ_code == "10.01.01" and inv.section == "FUNCTIONARE"
    assert inv.budget == "local" and inv.source_class == "A"
    # source E maps onto the .10 budget
    assert any(ln.code == "65.10.03" and ln.budget == "own_revenue" for ln in rep.lines)


def test_printed_totals_mismatch_is_flagged(tmp_path):
    rep = ex.parse_report(_workbook(tmp_path / "bad.xlsx", totals=(9_999_999.0, 925_000.0)))
    assert any("venituri" in i for i in rep.issues)


def test_unknown_source_class_flagged(tmp_path):
    rows = ROWS + [[" Venit", "Z-Sursa inventata", "040100", "x", None, None, "F", 1.0]]
    rep = ex.parse_report(_workbook(tmp_path / "z.xlsx", rows=rows, totals=(None, None)))
    assert any("sursă de finanțare necunoscută" in i for i in rep.issues)
    assert all(ln.source_class != "Z" for ln in rep.lines)


def test_snapshot_and_capitole(tmp_path):
    root = tmp_path / "execution" / "2026"
    city = root / "01-alba" / "1017-alba-iulia"
    (city / "q1").mkdir(parents=True)
    (city / "q2").mkdir()
    _workbook(city / "q1" / "forexebug_execution.xlsx",
              rows=[r[:-1] + [r[-1] / 2] for r in ROWS], totals=(525_000.0, 462_500.0))
    _workbook(city / "q2" / "forexebug_execution.xlsx")

    snap = ex.build_city(root, "01-alba", "1017-alba-iulia")
    assert snap["trimestru"] == 2  # newest quarter leads
    assert snap["unitate"] == "mii lei"
    assert snap["venituri"] == 1000.0 and snap["cheltuieli"] == 900.0
    assert [t["trimestru"] for t in snap["trimestre"]] == [1, 2]
    assert snap["trimestre"][0]["venituri"] == 500.0  # q1 half
    caps = {c["cod"]: c["val"] for c in snap["capitole"]}
    assert caps == {"65.02": 700.0, "84.02": 200.0}  # own-revenue line excluded
    assert snap["probleme"] == []


def _corpus_with_execution(tmp_path, plan_venituri):
    """A minimal data/ tree: one converted city plus its execution reports."""
    data = tmp_path / "data"
    city = data / "2026" / "01-alba" / "1017-alba-iulia"
    city.mkdir(parents=True)
    (city / "budget_file.pdf").write_bytes(b"%PDF-fake")
    (city / "analysis.json").write_text(json.dumps({
        "quality": {"lines": 10, "pct_clean": 100.0},
        "totals_mii_lei": {"venituri": plan_venituri, "cheltuieli": 2000.0},
    }))
    (data / "2026" / "manifest.json").write_text(json.dumps({"year": 2026, "entries": [{
        "county_code": "01", "county_name": "Alba", "capital_siruta": "1017",
        "capital_name": "Alba Iulia", "path": "01-alba/1017-alba-iulia/budget_file.pdf",
        "conversion": {"status": "converted", "lines": 10, "pct_clean": 100.0},
    }]}))
    ex_city = data / "execution" / "2026" / "01-alba" / "1017-alba-iulia"
    (ex_city / "q2").mkdir(parents=True)
    _workbook(ex_city / "q2" / "forexebug_execution.xlsx")
    snap = ex.build_city(data / "execution" / "2026", "01-alba", "1017-alba-iulia")
    ex.write_snapshot(snap, ex_city / "execution.json")
    return data


def test_aggregate_links_execution_and_computes_share(tmp_path):
    corpus = build_aggregate(_corpus_with_execution(tmp_path, plan_venituri=2000.0))
    e = corpus.cities[0].years["2026"].executie
    assert e is not None and e.trimestru == 2
    assert e.venituri == 1000.0
    assert e.pct_venituri == 50.0  # 1000 executed of a 2000 plan
    assert e.pct_cheltuieli == 45.0
    assert e.plan_incomplet is False


def test_city_page_has_one_tab_per_view(tmp_path):
    """Buget + T1..T4 + the annual view; quarters without a report say so."""
    from bgconvertor.site import build_all

    data = _corpus_with_execution(tmp_path, plan_venituri=2000.0)  # only q2 exists
    out = tmp_path / "site"
    build_all(data, out, base_url="/repo")
    page = (out / "city" / "1017.html").read_text()

    for tab in ('id="tab-buget"', 'id="tab-t1"', 'id="tab-t2"',
                'id="tab-t3"', 'id="tab-t4"', 'id="tab-an"'):
        assert tab in page
    # the reported quarter carries figures; the others explain the gap
    assert "Venituri încasate" in page
    assert page.count("nu este încă\n    publicat") == 3  # T1, T3, T4
    assert "Anul 2026 nu este încheiat" in page
    # empty quarters are dimmed, not hidden
    assert page.count('data-empty="1"') == 4  # T1, T3, T4 and the annual view


def test_partial_plan_suppresses_share(tmp_path):
    """A plan extracted from a bad scan can be a fraction of the real budget;
    the ratio would read as massive overspending, so it is withheld."""
    small = 1000.0 / (PLAN_RATIO_LIMIT / 100) / 2  # guarantees a ratio far above the cap
    corpus = build_aggregate(_corpus_with_execution(tmp_path, plan_venituri=small))
    e = corpus.cities[0].years["2026"].executie
    assert e.plan_incomplet is True
    assert e.pct_venituri is None
    assert e.venituri == 1000.0  # the official figure is still reported
