"""Corpus aggregate + multi-year site build, on a synthetic data/ tree."""

import json

import pytest
from openpyxl import Workbook

from bgconvertor import aggregate as agg
from bgconvertor.analytics import build_analytics, write_outputs
from bgconvertor.site import build_all


def _quality_workbook(path, lines=100, pct=95.0, errors=0, warnings=5):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sumar calitate"
    for row in (
        ("Linii de date", lines), ("% curat", pct),
        ("Erori", errors), ("Avertismente", warnings),
    ):
        ws.append(row)
    wb.save(path)


def _write_year(data_root, year, converted, totals=(123.4, 120.0)):
    ydir = data_root / str(year)
    city = ydir / "01-alba" / "1017-alba-iulia"
    city.mkdir(parents=True)
    (city / "budget_file.pdf").write_bytes(b"%PDF-fake")
    entry = {
        "county_code": "01", "county_name": "Alba",
        "capital_siruta": "1017", "capital_name": "Alba Iulia",
        "path": "01-alba/1017-alba-iulia/budget_file.pdf",
        "source_url": f"https://example.ro/{year}.pdf",
    }
    if converted:
        entry["conversion"] = {
            "status": "converted", "lines": 100, "pct_clean": 95.0,
            "errors": 0, "warnings": 5,
        }
        entry["timeline"] = {"debate_date": f"{year}-04-01", "approved_date": f"{year}-04-28",
                             "hcl": f"HCL 1/{year}"}
        _quality_workbook(city / "budget_file.xlsx")
        (city / "analysis.json").write_text(json.dumps({
            "quality": {"lines": 100, "pct_clean": 95.0, "errors": 0, "warnings": 5, "documents": 1},
            "totals_mii_lei": {"venituri": totals[0], "cheltuieli": totals[1]},
            "top_capitole": [{"code": "65.02", "name": "Învățământ", "total": 50.0}],
        }))
    (ydir / "manifest.json").write_text(json.dumps({"year": year, "entries": [entry]}))


@pytest.fixture
def data_root(tmp_path):
    root = tmp_path / "data"
    _write_year(root, 2026, converted=True)
    _write_year(root, 2025, converted=False)
    return root


def test_aggregate_merges_years_by_siruta(data_root):
    corpus = agg.build_aggregate(data_root)
    assert corpus.years == [2026, 2025]
    (city,) = corpus.cities
    assert city.siruta == "1017" and city.county_code == "01"
    assert set(city.years) == {"2026", "2025"}

    cy26 = city.years["2026"]
    assert cy26.status == "converted" and cy26.has_analysis
    assert cy26.quality.pct_clean == 95.0
    assert cy26.totals_mii_lei["venituri"] == 123.4
    assert cy26.top_capitole[0].code == "65.02"
    assert cy26.timeline.hcl == "HCL 1/2026"
    assert cy26.files.pdf == "data/2026/01-alba/1017-alba-iulia/budget_file.pdf"
    assert cy26.files.xlsx.endswith(".xlsx")

    cy25 = city.years["2025"]
    assert cy25.status == "pending" and not cy25.has_analysis
    assert cy25.quality is None
    assert cy25.files.pdf and cy25.files.xlsx is None


def test_oversize_pdf_not_linked(data_root, monkeypatch):
    monkeypatch.setattr(agg, "GITHUB_FILE_LIMIT", 1)
    corpus = agg.build_aggregate(data_root)
    cy = corpus.cities[0].years["2026"]
    assert cy.files.pdf is None
    assert cy.files.source_url == "https://example.ro/2026.pdf"


def test_build_all_writes_years_and_data_endpoint(data_root, tmp_path):
    out = tmp_path / "site"
    results = build_all(data_root, out, base_url="/repo")
    assert [r["converted_pages"] for r in results] == [1, 0]

    index = (out / "index.html").read_text()
    assert "Bugetele locale 2026" in index
    assert '<a href="/repo/2025/">2025</a>' in index
    assert (out / "city" / "1017.html").exists()

    index25 = (out / "2025" / "index.html").read_text()
    assert "Bugetele locale 2025" in index25
    assert '<a href="/repo/">2026</a>' in index25
    assert not (out / "2025" / "city" / "1017.html").exists()

    data = json.loads((out / "data" / "corpus.json").read_text())
    assert data["schema_version"] == agg.SCHEMA_VERSION == 3
    assert data["years"] == [2026, 2025]
    assert data["cities"][0]["years"]["2026"]["totals_mii_lei"]["cheltuieli"] == 120.0

    # the animated procedure explainer, per edition, linked from the index
    proc = (out / "procedura.html").read_text()
    assert "45 de zile" in proc and "28.04.2026" in proc  # stats from newest year
    assert (out / "2025" / "procedura.html").exists()
    assert '/repo/procedura.html' in index
    assert '/repo/2025/procedura.html' in index25

    # a single year with figures -> no year-over-year section
    assert "Evoluție an-la-an" not in (out / "city" / "1017.html").read_text()


def test_reference_populates_city_meta(data_root, tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "municipii.json").write_text(json.dumps({
        "sursa": {"populatie": {"referinta": "2021-12-01", "url": "https://ins.test"}},
        "municipii": {"1017": {
            "populatie": 64227, "populatie_data": "2021-12-01", "suprafata_km2": 103.65,
        }},
    }))
    corpus = agg.build_aggregate(data_root)
    assert corpus.cities[0].populatie == 64227
    assert corpus.cities[0].populatie_data == "2021-12-01"
    assert corpus.cities[0].suprafata_km2 == 103.65
    assert corpus.augmentation_sources["populatie"]["url"] == "https://ins.test"

    out = tmp_path / "site"
    build_all(data_root, out, base_url="/repo")
    page = (out / "city" / "1017.html").read_text()
    assert "SIRUTA 1017" in page
    assert "64.227 locuitori (01.12.2021)" in page and "103.65 km²" in page
    # ordered layout: identity, adoption, conversion quality and downloads,
    # then the tabbed budget / execution views closing the page
    assert page.index("SIRUTA 1017") < page.index("Adoptarea bugetului") \
        < page.index("Calitatea conversiei") < page.index("Descarcă Excel") \
        < page.index("Bugetul și execuția lui") \
        < page.index("Cheltuieli planificate pe capitole")


def test_city_page_year_over_year(tmp_path):
    data_root = tmp_path / "data"
    _write_year(data_root, 2025, converted=True, totals=(100.0, 80.0))
    _write_year(data_root, 2026, converted=True, totals=(110.0, 76.0))
    out = tmp_path / "site"
    build_all(data_root, out, base_url="/repo")

    page = (out / "city" / "1017.html").read_text()
    assert "Evoluție an-la-an" in page
    assert "+10.0%" in page and "-5.0%" in page
    assert '<a href="/repo/2025/city/1017.html">2025</a>' in page
    assert "<strong>2026</strong>" in page

    page25 = (out / "2025" / "city" / "1017.html").read_text()
    assert "Evoluție an-la-an" in page25
    assert '<a href="/repo/city/1017.html">2026</a>' in page25
    assert "<strong>2025</strong>" in page25


def test_analytics_keeps_exclusions_visible_and_writes_auditable_outputs(data_root, tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "municipii.json").write_text(json.dumps({
        "sursa": {"populatie": {
            "referinta": "2021-12-01", "url": "https://ins.test/rpl2021",
        }},
        "municipii": {"1017": {
            "populatie": 100, "populatie_data": "2021-12-01", "suprafata_km2": 4,
        }},
    }))
    dataset = build_analytics(agg.build_aggregate(data_root))
    current = next(row for row in dataset.rows if row.year == 2026)
    pending = next(row for row in dataset.rows if row.year == 2025)

    assert current.plan_comparison_eligible is True
    assert current.planned_expense_lei_per_capita == 1200
    assert current.plan_expense_per_capita_rank == 1
    assert current.plan_expense_rank_cohort == 1
    assert current.density_per_km2 == 25
    assert pending.plan_comparison_eligible is False
    assert pending.plan_exclusion_reason == "status_pending"
    assert len(dataset.chapters) == 1

    outputs = write_outputs(dataset, tmp_path / "analytics")
    assert set(outputs) == {"json", "csv", "xlsx"}
    payload = json.loads(outputs["json"].read_text())
    assert payload["coverage"]["2026"]["plan_comparison_eligible"] == 1

    from openpyxl import load_workbook

    workbook = load_workbook(outputs["xlsx"], data_only=False)
    assert workbook.sheetnames == ["Sumar", "Municipii", "Capitole", "Surse augmentare"]
    assert workbook["Municipii"]["R2"].value == \
        '=IF(OR($E2<>"da",O2=""),"",IFERROR(O2*1000/J2,""))'
    assert workbook["Municipii"]["E2"].value == "da"
    source_values = [cell.value for cell in workbook["Surse augmentare"]["C"]]
    assert "https://ins.test/rpl2021" in source_values

    out = tmp_path / "site"
    build_all(data_root, out, base_url="/repo")
    comparison = (out / "comparatii.html").read_text()
    assert "Cheltuieli planificate pe locuitor" in comparison
    assert "1 din" in comparison
    assert (out / "data" / "analytics.xlsx").exists()


def test_incomplete_plan_is_not_ranked(data_root):
    corpus = agg.build_aggregate(data_root)
    city = corpus.cities[0]
    city.populatie = 100
    cy = city.years["2026"]
    cy.executie = agg.Executie(
        trimestru=2, venituri=1000, cheltuieli=900, plan_incomplet=True,
    )
    row = build_analytics(corpus).year_rows(2026)[0]
    assert row.plan_comparison_eligible is False
    assert row.plan_exclusion_reason == "plan_incomplet_fata_de_executie"
    assert row.planned_balance_mii_lei is None
    assert row.planned_expense_lei_per_capita is None
    assert row.plan_expense_per_capita_rank is None
    # The official execution remains independently comparable.
    assert row.execution_comparison_eligible is True
    assert row.actual_expense_lei_per_capita == 9000


def test_public_population_reference_uses_one_census_cohort():
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).parents[1] / "reference" / "municipii.json").read_text()
    )
    municipalities = payload["municipii"]
    assert len(municipalities) == 41
    assert {item["populatie_data"] for item in municipalities.values()} == {"2021-12-01"}
    assert municipalities["60419"]["populatie"] == 263688  # Constanța
    assert municipalities["130534"]["populatie"] == 180540  # Ploiești
    assert municipalities["143450"]["populatie"] == 134309  # Sibiu
    assert municipalities["146263"]["populatie"] == 84322  # Suceava
    assert "Tabel-1.22.xlsx" in payload["sursa"]["populatie"]["fisier_url"]


def test_execution_rank_uses_one_reporting_period(data_root):
    corpus = agg.build_aggregate(data_root)
    first = corpus.cities[0]
    first.populatie = 100
    first.years["2026"].executie = agg.Executie(
        trimestru=2, la_data="2026-06-30", venituri=1000, cheltuieli=900,
    )
    older = first.model_copy(deep=True)
    older.siruta = "2"
    older.name = "Alt municipiu"
    older.years["2026"].executie = agg.Executie(
        trimestru=1, la_data="2026-03-31", venituri=500, cheltuieli=400,
    )
    corpus.cities.append(older)

    rows = {row.siruta: row for row in build_analytics(corpus).year_rows(2026)}
    assert rows[first.siruta].execution_comparison_eligible is True
    assert rows[first.siruta].execution_expense_per_capita_rank == 1
    assert rows["2"].execution_comparison_eligible is False
    assert rows["2"].execution_exclusion_reason == "perioada_executie_necomparabila"
    assert rows["2"].execution_expense_per_capita_rank is None
