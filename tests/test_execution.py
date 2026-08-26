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


def _corpus_with_execution(tmp_path, plan_venituri, plan_cheltuieli=2000.0):
    """A minimal data/ tree: one converted city plus its execution reports."""
    data = tmp_path / "data"
    city = data / "2026" / "01-alba" / "1017-alba-iulia"
    city.mkdir(parents=True)
    (city / "budget_file.pdf").write_bytes(b"%PDF-fake")
    _budget_quality_workbook(city / "budget_file.xlsx", lines=10, pct=100.0)
    (city / "analysis.json").write_text(json.dumps({
        "quality": {"lines": 10, "pct_clean": 100.0, "errors": 0, "warnings": 0},
        "totals_mii_lei": {"venituri": plan_venituri, "cheltuieli": plan_cheltuieli},
    }))
    (data / "2026" / "manifest.json").write_text(json.dumps({"year": 2026, "entries": [{
        "county_code": "01", "county_name": "Alba", "capital_siruta": "1017",
        "capital_name": "Alba Iulia", "path": "01-alba/1017-alba-iulia/budget_file.pdf",
        "conversion": {
            "status": "converted", "lines": 10, "pct_clean": 100.0,
            "errors": 0, "warnings": 0,
        },
    }]}))
    ex_city = data / "execution" / "2026" / "01-alba" / "1017-alba-iulia"
    (ex_city / "q2").mkdir(parents=True)
    _workbook(ex_city / "q2" / "forexebug_execution.xlsx")
    snap = ex.build_city(data / "execution" / "2026", "01-alba", "1017-alba-iulia")
    ex.write_snapshot(snap, ex_city / "execution.json")
    return data


def _budget_quality_workbook(path, lines, pct, errors=0, warnings=0):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sumar calitate"
    for row in (
        ("Linii de date", lines), ("% curat", pct),
        ("Erori", errors), ("Avertismente", warnings),
    ):
        ws.append(row)
    wb.save(path)


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
    assert 'data-chart-quality="executie-t2"' in page
    assert "raport Forexebug structurat" in page
    assert "<strong>Acoperire:</strong>" in page
    # the tabs close the page: nothing follows them inside the content block
    assert page.index("Calitatea conversiei") < page.index("Bugetul și execuția lui")


def test_budget_tab_explains_a_failed_conversion(tmp_path):
    """No totals and no capitole: say why, instead of an empty panel."""
    from bgconvertor.site import build_all

    data = _corpus_with_execution(tmp_path, plan_venituri=None)
    city = data / "2026" / "01-alba" / "1017-alba-iulia"
    _budget_quality_workbook(city / "budget_file.xlsx", lines=500, pct=57.3)
    (city / "analysis.json").write_text(json.dumps({
        "quality": {"lines": 500, "pct_clean": 57.3, "errors": 0, "warnings": 0},
        "totals_mii_lei": {"venituri": None, "cheltuieli": None},
        "top_capitole": [],
    }))
    manifest_path = data / "2026" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"][0]["conversion"].update({"lines": 500, "pct_clean": 57.3})
    manifest_path.write_text(json.dumps(manifest))
    out = tmp_path / "site"
    build_all(data, out, base_url="/repo")
    page = (out / "city" / "1017.html").read_text()
    assert "nu s-au putut extrage" in page and "57.3%" in page
    assert "Execuția bugetară, în celelalte vizualizări" in page
    # execution is unaffected by a poor PDF conversion
    assert "Venituri încasate" in page


@pytest.mark.parametrize("today,expected", [
    ("2026-03-31", None),   # T1 tocmai s-a închis, raportul nu e publicat
    ("2026-05-24", None),   # nici la 54 de zile
    ("2026-05-25", 1),      # abia după termenul de publicare
    ("2026-08-24", 2),      # data auditului corpusului: T2 disponibil
    ("2026-11-30", 3),
    ("2027-03-01", 4),
])
def test_expected_quarter(today, expected):
    from datetime import date
    assert ex.expected_quarter(date.fromisoformat(today), 2026) == expected


def test_quarter_status_flags_missing_quarter(tmp_path):
    from datetime import date

    root = tmp_path / "2026"
    (root / "q1").mkdir(parents=True)
    entry = {"county_code": "01", "capital_name": "Alba Iulia",
             "path": "01-alba/1017-alba-iulia/q1/forexebug_execution.xlsx",
             "source_url": "https://example.ro/a.xlsx", "entity_cif": "4562923"}
    (root / "q1" / "manifest.json").write_text(json.dumps(
        {"year": 2026, "quarter": 1, "report_date": "2026-03-31", "entries": [entry]}))
    (root / entry["path"]).parent.mkdir(parents=True)
    (root / entry["path"]).write_bytes(b"x")

    st = ex.quarter_status(root, 2026, date(2026, 8, 24))
    assert st["trimestre_complete"] == [1]
    assert st["trimestru_asteptat"] == 2
    assert st["urmatorul_de_adus"] == 2 and st["manifest_lipsa"] == [2]

    # before T1 was even due, nothing is outstanding
    assert ex.quarter_status(root, 2026, date(2026, 4, 1))["de_adus"] == []


def _quarter_tree(tmp_path, quarter=2, url="https://static.anaf.ro/x.xlsx"):
    """A corpus year with one entity and a manifest for `quarter`."""
    root = tmp_path / "2026"
    rel = f"01-alba/1017-alba-iulia/q{quarter}/forexebug_execution.xlsx"
    entry = {"county_code": "01", "county_name": "Alba", "capital_siruta": "1017",
             "capital_name": "Alba Iulia", "entity_cif": "4562923",
             "entity_name": "MUNICIPIUL TEST", "path": rel,
             "report_date": f"2026-{QUARTER_MONTH[quarter]}"}
    if url:
        entry["source_url"] = url
    (root / f"q{quarter}").mkdir(parents=True)
    (root / f"q{quarter}" / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "year": 2026, "quarter": quarter,
        "report_date": f"2026-{QUARTER_MONTH[quarter]}", "entries": [entry],
    }, ensure_ascii=False, indent=2) + "\n")
    return root, rel


QUARTER_MONTH = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def test_ingest_verifies_placed_files(tmp_path):
    """Files dropped in by hand are checked, then recorded with a checksum."""
    root, rel = _quarter_tree(tmp_path)
    (root / rel).parent.mkdir(parents=True)
    _workbook(root / rel)  # header says 30-JUN-26, CIF 4562923

    r = ex.ingest_quarter(root, 2)
    assert (r["verified"], r["failed"], r["missing"]) == (1, 0, 0)

    v = json.loads((root / "q2" / "verification.json").read_text())["entries"][0]
    assert v["verification_status"] == "verified"
    assert v["sha256"] and v["bytes"] > 0 and v["lines"] == 6
    assert (root / "q2" / "checksums.sha256").read_text().split()[1] == rel


def test_ingest_rejects_a_report_from_another_quarter(tmp_path):
    """The commonest hand-placement mistake: last quarter's file in the new folder."""
    root, rel = _quarter_tree(tmp_path, quarter=3, url=None)
    (root / rel).parent.mkdir(parents=True)
    _workbook(root / rel)  # a Q2 report (30-JUN-26) placed under q3

    r = ex.ingest_quarter(root, 3)
    assert (r["verified"], r["failed"]) == (0, 1)
    problems = r["entries"][0]["problems"]
    assert any("2026-09-30" in p for p in problems)


def test_ingest_rejects_a_foreign_entity(tmp_path):
    root, rel = _quarter_tree(tmp_path)
    (root / rel).parent.mkdir(parents=True)
    _workbook(root / rel)
    m = json.loads((root / "q2" / "manifest.json").read_text())
    m["entries"][0]["entity_cif"] = "9999999"  # corpus expects a different city
    (root / "q2" / "manifest.json").write_text(json.dumps(m))

    r = ex.ingest_quarter(root, 2)
    assert r["failed"] == 1
    assert any("CIF" in p for p in r["entries"][0]["problems"])


def test_ingest_is_idempotent(tmp_path):
    """Re-running on an unchanged corpus must not rewrite anything."""
    root, rel = _quarter_tree(tmp_path)
    (root / rel).parent.mkdir(parents=True)
    _workbook(root / rel)
    ex.ingest_quarter(root, 2)

    before = {p: p.read_bytes() for p in (root / "q2").iterdir()}
    ex.ingest_quarter(root, 2)
    assert {p: p.read_bytes() for p in (root / "q2").iterdir()} == before


def test_quarters_on_disk(tmp_path):
    root, rel = _quarter_tree(tmp_path)
    (root / rel).parent.mkdir(parents=True)
    _workbook(root / rel)
    assert ex.quarters_on_disk(root) == [2]


def test_scaffold_quarter_copies_entities_without_urls(tmp_path):
    root = tmp_path / "2026"
    (root / "q2").mkdir(parents=True)
    (root / "q2" / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "year": 2026, "quarter": 2, "report_date": "2026-06-30",
        "source_audited_on": "2026-08-24",
        "entries": [
            {"county_code": "01", "capital_name": "Alba Iulia", "entity_cif": "4562923",
             "path": "01-alba/1017-alba-iulia/q2/forexebug_execution.xlsx",
             "source_url": "https://static.anaf.ro/rapfxb/LOT724/x.xlsx",
             "reporting_period": "2026-Q2", "report_date": "2026-06-30"},
            {"county_code": "25", "capital_name": "București", "entity_cif": "4267117",
             "path": "25-ilfov/179132-bucuresti/q2/forexebug_execution.xlsx",
             "copy_from": "42-bucuresti/179132-bucuresti/q2/forexebug_execution.xlsx"},
        ],
    }, ensure_ascii=False))

    out = ex.scaffold_quarter(root, 3)
    m = json.loads(out.read_text())
    assert m["quarter"] == 3 and m["report_date"] == "2026-09-30"
    assert m["source_audited_on"] is None  # not audited until the URLs are filled
    first, second = m["entries"]
    assert first["path"].endswith("/q3/forexebug_execution.xlsx")
    assert first["source_url"] is None  # the manual step
    assert first["entity_cif"] == "4562923"  # identity carried over
    assert first["reporting_period"] == "2026-Q3"
    assert second["copy_from"].endswith("/q3/forexebug_execution.xlsx")  # aliases follow

    # a scaffolded quarter is present but not complete: the URLs are missing
    from datetime import date
    st = ex.quarter_status(root, 2026, date(2026, 11, 30))
    assert 3 in st["trimestre_prezente"] and 3 not in st["trimestre_complete"]
    assert 3 not in st["manifest_lipsa"]  # manifest exists, only the URLs are pending
    assert 3 in st["de_adus"]


def test_partial_plan_suppresses_share(tmp_path):
    """A plan extracted from a bad scan can be a fraction of the real budget;
    the ratio would read as massive overspending, so it is withheld."""
    small = 1000.0 / (PLAN_RATIO_LIMIT / 100) / 2  # guarantees a ratio far above the cap
    corpus = build_aggregate(_corpus_with_execution(tmp_path, plan_venituri=small))
    e = corpus.cities[0].years["2026"].executie
    assert e.plan_incomplet is True
    assert e.pct_venituri is None
    assert e.venituri == 1000.0  # the official figure is still reported


def test_plan_printed_in_lei_is_rescaled(tmp_path):
    """Cluj, Timișoara și Vaslui publică bugetul în lei; corpusul e în mii lei."""
    corpus = build_aggregate(_corpus_with_execution(
        tmp_path, plan_venituri=2_000_000.0, plan_cheltuieli=1_800_000.0))
    cy = corpus.cities[0].years["2026"]
    assert cy.scara_corectata is True
    assert cy.totals_mii_lei["venituri"] == 2000.0  # împărțit la 1000
    assert cy.executie.pct_venituri == 50.0  # raport plauzibil după corecție


def test_ordinary_overshoot_is_not_rescaled(tmp_path):
    """O depășire obișnuită nu declanșează corecția de scară."""
    corpus = build_aggregate(_corpus_with_execution(tmp_path, plan_venituri=2500.0))
    cy = corpus.cities[0].years["2026"]
    assert cy.scara_corectata is False
    assert cy.totals_mii_lei["venituri"] == 2500.0


def test_absurdly_low_share_discredits_the_plan(tmp_path):
    """Un plan mult prea mare față de execuție e la fel de suspect ca unul prea mic."""
    # execuția e 1000 mii lei; un plan de 90.000 ar da 1,1% la jumătatea anului
    corpus = build_aggregate(_corpus_with_execution(
        tmp_path, plan_venituri=90_000.0, plan_cheltuieli=88_000.0))
    e = corpus.cities[0].years["2026"].executie
    assert e.plan_incomplet is True and e.pct_venituri is None
