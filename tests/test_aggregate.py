"""Corpus aggregate + multi-year site build, on a synthetic data/ tree."""

import json

import pytest

from bgconvertor import aggregate as agg
from bgconvertor.site import build_all


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
        entry["conversion"] = {"status": "converted", "lines": 100, "pct_clean": 95.0}
        entry["timeline"] = {"debate_date": f"{year}-04-01", "approved_date": f"{year}-04-28",
                             "hcl": f"HCL 1/{year}"}
        (city / "budget_file.xlsx").write_bytes(b"xlsx")
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
    assert data["schema_version"] == agg.SCHEMA_VERSION
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
        "municipii": {"1017": {"populatie": 64227, "suprafata_km2": 103.65}}
    }))
    corpus = agg.build_aggregate(data_root)
    assert corpus.cities[0].populatie == 64227
    assert corpus.cities[0].suprafata_km2 == 103.65

    out = tmp_path / "site"
    build_all(data_root, out, base_url="/repo")
    page = (out / "city" / "1017.html").read_text()
    assert "SIRUTA 1017" in page
    assert "64.227 locuitori (2021)" in page and "103.65 km²" in page
    # ordered layout: identity and adoption first, then the tabbed budget /
    # execution views, with conversion quality and downloads closing the page
    assert page.index("SIRUTA 1017") < page.index("Adoptarea bugetului") \
        < page.index("Bugetul și execuția lui") \
        < page.index("Cheltuieli planificate pe capitole") \
        < page.index("Calitatea conversiei") < page.index("Descarcă Excel")


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
