"""Static GitHub Pages site: corpus index + one page per converted city.

Every page is a projection of the corpus aggregate (see aggregate.py),
which is itself built exclusively from committed files — so the site
renders identically locally and in a GitHub Action. The aggregate is
also published verbatim at site/data/corpus.json.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .aggregate import City, CityYear, Corpus, aggregate_manifests, build_aggregate, write_aggregate
from .manifest import Manifest

log = logging.getLogger("bgc.site")


def _ro_date(iso: str | None) -> str | None:
    """'2026-04-28' -> '28.04.2026'; passes through anything non-ISO."""
    if not iso:
        return None
    parts = iso.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        return f"{d}.{m}.{y}"
    return iso


REPO_RAW = "https://github.com/danadrianparaschiv/bugete-locale-romania/raw/main"


def _row(city: City, cy: CityYear) -> dict:
    """Flatten one city-year of the aggregate into what templates expect."""
    return {
        "siruta": city.siruta,
        "name": city.name,
        "county": city.county,
        "county_code": city.county_code,
        "status": cy.status,
        "pct_clean": cy.quality.pct_clean if cy.quality else None,
        "lines": cy.quality.lines if cy.quality else None,
        "analysis": cy.has_analysis,
        "source_url": cy.files.source_url,
        "pdf_rel": cy.files.pdf,
        "xlsx_rel": cy.files.xlsx,
        "debate_date": _ro_date(cy.timeline.debate_date),
        "debate_url": cy.timeline.debate_url,
        "approved_date": _ro_date(cy.timeline.approved_date),
        "approved_url": cy.timeline.approved_url,
        "hcl": cy.timeline.hcl,
        "timeline_notes": cy.timeline.notes,
    }


def _build_year(corpus: Corpus, year: int, out: Path, base_url: str, raw_base: str,
                editions: list[dict], repo_root: Path) -> dict:
    env = Environment(
        loader=PackageLoader("bgconvertor", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "city").mkdir(exist_ok=True)

    rows = []
    n_converted = 0
    for city, cy in corpus.year_rows(year):
        row = _row(city, cy)
        rows.append(row)
        if cy.has_analysis:
            n_converted += 1
            page = env.get_template("city.html").render(
                city=row, a=cy, year=year, base=base_url, raw=raw_base,
            )
            (out / "city" / f"{city.siruta}.html").write_text(page)

    index = env.get_template("index.html").render(
        cities=rows, year=year, n_converted=n_converted,
        base=base_url, raw=raw_base, editions=editions,
    )
    (out / "index.html").write_text(index)

    disclaimer = repo_root / "DISCLAIMER.md"
    if disclaimer.exists():
        shutil.copy(disclaimer, out / "DISCLAIMER.md")
    return {"cities": len(rows), "converted_pages": n_converted, "out": str(out)}


def build_all(data_root: Path, out: Path, base_url: str = "", raw_base: str = REPO_RAW) -> list[dict]:
    """One site for every corpus year: newest at out/, older at out/<year>/."""
    corpus = build_aggregate(data_root)
    write_aggregate(corpus, out / "data" / "corpus.json")
    editions = [
        {"year": y, "href": f"{base_url}/" if i == 0 else f"{base_url}/{y}/"}
        for i, y in enumerate(corpus.years)
    ]
    return [
        _build_year(
            corpus, y,
            out if i == 0 else out / str(y),
            base_url if i == 0 else f"{base_url}/{y}",
            raw_base, editions, data_root.parent,
        )
        for i, y in enumerate(corpus.years)
    ]


def build(manifest: Manifest, out: Path, base_url: str = "", raw_base: str = REPO_RAW,
          editions: list[dict] | None = None) -> dict:
    """Single-year build straight from one manifest (no edition links)."""
    corpus = aggregate_manifests([manifest])
    return _build_year(
        corpus, manifest.year, out, base_url, raw_base,
        editions or [], manifest.root.parent.parent,
    )
