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
from .analytics import AnalyticsDataset, AnalyticsRow, build_analytics, write_outputs
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


def _execution_chart_quality(block) -> dict:
    """Coverage/confidence note for one structured Forexebug chart."""
    def get(key, default=None):
        if isinstance(block, dict):
            return block.get(key, default)
        return getattr(block, key, default)

    chapters = get("capitole", []) or []
    total = get("cheltuieli")
    covered = sum(
        item.get("val", 0) if isinstance(item, dict) else item.val
        for item in chapters
    )
    coverage = round(covered / total * 100, 1) if total else None
    problems = get("probleme", []) or []
    unknown = get("coduri_neenumerate", 0) or 0
    confidence = (
        "official_structured_validated"
        if not problems and not unknown
        else "official_structured_flagged"
    )
    return {
        "coverage_pct": coverage,
        "coverage_note": (
            f"capitolele afișate însumează {coverage:.1f}% din plățile raportate"
            if coverage is not None else
            "totalul necesar calculului de acoperire nu este disponibil"
        ),
        "confidence": confidence,
        "problems": len(problems),
        "unknown_codes": unknown,
    }


def _plan_chart_quality(infographic: dict | None) -> dict:
    """Use embedded chart metadata, backfilling it for legacy analyses."""
    if not infographic:
        return {}
    quality = dict(infographic.get("chart_quality") or {})
    confidence = "strictly_verified_cells"

    revenues = infographic.get("venituri") or {}
    if revenues and "venituri" not in quality:
        coverage = revenues.get("acoperire_pct")
        quality["venituri"] = {
            "coverage_pct": coverage,
            "coverage_note": (
                f"sursele afișate însumează {coverage:.1f}% din totalul veniturilor"
                if coverage is not None else "acoperirea numerică nu este disponibilă"
            ),
            "confidence": confidence,
            "recall_measured": False,
        }

    chapters = infographic.get("capitole") or []
    total = infographic.get("total_cheltuieli")
    if chapters and total:
        coverage = round(sum(item.get("val", 0) for item in chapters) / total * 100, 1)
        default = {
            "coverage_pct": coverage,
            "coverage_note": (
                f"capitolele afișate însumează {coverage:.1f}% din totalul cheltuielilor"
            ),
            "confidence": confidence,
            "recall_measured": False,
        }
        quality.setdefault("cheltuieli", default)
        quality.setdefault("100_lei", dict(default))

    trim = infographic.get("trim") or {}
    if trim and "trim" not in quality:
        series = [values for values in trim.values() if values]
        cells = sum(len(values) for values in series)
        quality["trim"] = {
            "coverage_pct": 100.0,
            "coverage_note": f"{cells} din {cells} valori necesare seriilor afișate sunt prezente",
            "confidence": confidence,
            "recall_measured": False,
        }

    years = infographic.get("ani") or {}
    if years and "ani" not in quality:
        series = [
            values for key, values in years.items()
            if key != "years" and isinstance(values, list)
        ]
        cells = sum(len(values) for values in series)
        quality["ani"] = {
            "coverage_pct": 100.0,
            "coverage_note": f"{cells} din {cells} valori necesare seriilor afișate sunt prezente",
            "confidence": confidence,
            "recall_measured": False,
        }
    return quality


def _row(city: City, cy: CityYear, analytics: AnalyticsRow) -> dict:
    """Flatten one city-year of the aggregate into what templates expect."""
    region = city.regional_classification
    execution_quality = {
        int(block.get("trimestru")): _execution_chart_quality(block)
        for block in (cy.executie.trimestre if cy.executie else [])
        if block.get("trimestru")
    }
    return {
        "siruta": city.siruta,
        "name": city.name,
        "county": city.county,
        "county_code": city.county_code,
        "populatie": city.populatie,
        "populatie_data": _ro_date(city.populatie_data),
        "suprafata_km2": city.suprafata_km2,
        "regional_classification_version": (
            region.dataset_version if region else None
        ),
        "nuts2_code": region.nuts2_code if region else None,
        "nuts2_name": region.nuts2_name if region else None,
        "nuts3_code": region.nuts3_code if region else None,
        "density_per_km2": analytics.density_per_km2,
        "hicp_annual_average_rate_pct": analytics.hicp_annual_average_rate_pct,
        "inflation_status": analytics.inflation_status,
        "plan_comparison_eligible": analytics.plan_comparison_eligible,
        "plan_exclusion_reason": analytics.plan_exclusion_reason,
        "planned_revenue_lei_per_capita": analytics.planned_revenue_lei_per_capita,
        "planned_expense_lei_per_capita": analytics.planned_expense_lei_per_capita,
        "plan_expense_per_capita_rank": analytics.plan_expense_per_capita_rank,
        "plan_expense_rank_cohort": analytics.plan_expense_rank_cohort,
        "executie": cy.executie,
        "execution_chart_quality": execution_quality,
        "plan_chart_quality": _plan_chart_quality(cy.infografic),
        "status": cy.status,
        "artifact_status": cy.artifact_status,
        "artifact_issues": cy.artifact_issues,
        "pct_clean": (
            cy.quality.pct_lines_strictly_verified
            if cy.quality and cy.quality.pct_lines_strictly_verified is not None
            else cy.quality.pct_clean if cy.quality else None
        ),
        "lines": cy.quality.lines if cy.quality else None,
        "analysis": cy.has_analysis,
        "source_url": cy.files.source_url,
        "pdf_rel": cy.files.pdf,
        "source_rel": cy.files.source,
        "source_format": cy.files.source_format,
        "xlsx_rel": cy.files.xlsx,
        "debate_date": _ro_date(cy.timeline.debate_date),
        "debate_url": cy.timeline.debate_url,
        "approved_date": _ro_date(cy.timeline.approved_date),
        "approved_url": cy.timeline.approved_url,
        "hcl": cy.timeline.hcl,
        "timeline_notes": cy.timeline.notes,
    }


def _pct_change(prev: float | None, cur: float | None) -> str | None:
    if not prev or cur is None:
        return None
    return f"{(cur - prev) / prev * 100:+.1f}%"


def _evolution(city: City, editions: list[dict]) -> list[dict] | None:
    """Chronological totals for every year the corpus lists this city.

    None unless at least two years carry figures — a one-row table says
    nothing the cards above it don't.
    """
    href_by_year = {str(e["year"]): e["href"] for e in editions}
    rows = []
    prev: CityYear | None = None
    for key in sorted(city.years, key=int):
        cy = city.years[key]
        t = cy.totals_mii_lei
        pt = prev.totals_mii_lei if prev else {}
        href = None
        if cy.has_analysis and key in href_by_year:
            href = f"{href_by_year[key]}city/{city.siruta}.html"
        rows.append({
            "year": int(key),
            "venituri": t.get("venituri"),
            "cheltuieli": t.get("cheltuieli"),
            "d_venituri": _pct_change(pt.get("venituri"), t.get("venituri")),
            "d_cheltuieli": _pct_change(pt.get("cheltuieli"), t.get("cheltuieli")),
            "href": href,
        })
        if t:
            prev = cy
    if sum(1 for r in rows if r["venituri"] is not None or r["cheltuieli"] is not None) < 2:
        return None
    return rows


def _build_year(corpus: Corpus, analytics: AnalyticsDataset, year: int, out: Path,
                base_url: str, raw_base: str, editions: list[dict], repo_root: Path,
                data_base: str) -> dict:
    env = Environment(
        loader=PackageLoader("bgconvertor", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "city").mkdir(exist_ok=True)

    rows = []
    n_converted = 0
    analytics_by_siruta = {row.siruta: row for row in analytics.year_rows(year)}
    for city, cy in corpus.year_rows(year):
        row = _row(city, cy, analytics_by_siruta[city.siruta])
        rows.append(row)
        if cy.has_analysis:
            n_converted += 1
            page = env.get_template("city.html").render(
                city=row, a=cy, year=year, base=base_url, raw=raw_base,
                evolution=_evolution(city, editions),
            )
            (out / "city" / f"{city.siruta}.html").write_text(page)

    index = env.get_template("index.html").render(
        cities=rows, year=year, n_converted=n_converted,
        base=base_url, raw=raw_base, editions=editions, data_base=data_base,
    )
    (out / "index.html").write_text(index)

    year_analytics = analytics.year_rows(year)
    plan_rankings = sorted(
        (row for row in year_analytics if row.plan_comparison_eligible),
        key=lambda row: row.planned_expense_lei_per_capita or 0,
        reverse=True,
    )
    execution_rankings = sorted(
        (row for row in year_analytics if row.execution_comparison_eligible),
        key=lambda row: row.actual_expense_lei_per_capita or 0,
        reverse=True,
    )
    page = env.get_template("comparatii.html").render(
        year=year, base=base_url, data_base=data_base,
        coverage=analytics.coverage.get(str(year), {}),
        plan_rankings=plan_rankings, execution_rankings=execution_rankings,
        sources=analytics.sources,
    )
    (out / "comparatii.html").write_text(page)

    # the animated budget-procedure explainer, with live stats from the
    # newest corpus year (the current budget cycle)
    latest = corpus.years[0] if corpus.years else year
    approved = sorted(
        cy.timeline.approved_date
        for _, cy in corpus.year_rows(latest) if cy.timeline.approved_date
    )
    stats = {
        "year": latest,
        "total": len(corpus.year_rows(latest)),
        "aprobate": len(approved),
        "prima": _ro_date(approved[0]) if approved else None,
        "ultima": _ro_date(approved[-1]) if approved else None,
    }
    page = env.get_template("procedura.html").render(
        base=base_url, year=year, stats=stats,
    )
    (out / "procedura.html").write_text(page)

    disclaimer = repo_root / "DISCLAIMER.md"
    if disclaimer.exists():
        shutil.copy(disclaimer, out / "DISCLAIMER.md")
    return {"cities": len(rows), "converted_pages": n_converted, "out": str(out)}


def build_all(data_root: Path, out: Path, base_url: str = "", raw_base: str = REPO_RAW) -> list[dict]:
    """One site for every corpus year: newest at out/, older at out/<year>/."""
    corpus = build_aggregate(data_root)
    write_aggregate(corpus, out / "data" / "corpus.json")
    analytics = build_analytics(corpus)
    write_outputs(analytics, out / "data")
    editions = [
        {"year": y, "href": f"{base_url}/" if i == 0 else f"{base_url}/{y}/"}
        for i, y in enumerate(corpus.years)
    ]
    return [
        _build_year(
            corpus, analytics, y,
            out if i == 0 else out / str(y),
            base_url if i == 0 else f"{base_url}/{y}",
            raw_base, editions, data_root.parent, f"{base_url}/data",
        )
        for i, y in enumerate(corpus.years)
    ]


def build(manifest: Manifest, out: Path, base_url: str = "", raw_base: str = REPO_RAW,
          editions: list[dict] | None = None) -> dict:
    """Single-year build straight from one manifest (no edition links)."""
    corpus = aggregate_manifests([manifest])
    analytics = build_analytics(corpus)
    write_outputs(analytics, out / "data")
    return _build_year(
        corpus, analytics, manifest.year, out, base_url, raw_base,
        editions or [], manifest.root.parent.parent, f"{base_url}/data",
    )
