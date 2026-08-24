"""Corpus aggregate: one machine-readable summary of every city-year.

Built exclusively from committed files (data/<year>/manifest.json +
analysis.json), so it is reproducible anywhere without runs/ caches.
The site renders every page from this single structure and republishes
it verbatim as site/data/corpus.json — the open-data endpoint that
mirrors exactly what the pages show.

Cities are keyed by SIRUTA (stable across years); identity fields come
from the newest year that lists the city. Line-level data stays in the
corpus.csv/parquet export — this aggregate carries only the indicators
pages display.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .manifest import CityEntry, Manifest

log = logging.getLogger("bgc.aggregate")

SCHEMA_VERSION = 1
GITHUB_FILE_LIMIT = 100 * 1024 * 1024  # files over this are not in git (see .gitignore)
# execution above this share of the approved plan means the plan figure is
# partial (bad scan), not that the city overspent — rectifications explain
# some overshoot, an 8x ratio never does
PLAN_RATIO_LIMIT = 130.0


class Capitol(BaseModel):
    code: str
    name: str
    total: float


class Quality(BaseModel):
    lines: int | None = None
    pct_clean: float | None = None
    errors: int | None = None
    warnings: int | None = None
    documents: int | None = None


class Timeline(BaseModel):
    debate_date: str | None = None  # ISO
    debate_url: str | None = None
    approved_date: str | None = None
    approved_url: str | None = None
    hcl: str | None = None
    notes: str | None = None


class Files(BaseModel):
    pdf: str | None = None  # repo-relative; None when oversize/uncommitted
    xlsx: str | None = None
    source_url: str | None = None


class Executie(BaseModel):
    """Quarterly execution reported through Forexebug (see execution.py)."""

    trimestru: int
    la_data: str | None = None
    venituri: float | None = None  # mii lei, cumulative
    cheltuieli: float | None = None
    capitole: list[dict] = Field(default_factory=list)
    trimestre: list[dict] = Field(default_factory=list)
    pct_venituri: float | None = None  # execution vs approved plan
    pct_cheltuieli: float | None = None
    plan_incomplet: bool = False  # plan extracted only partially -> no ratio shown
    sursa: str | None = None


class CityYear(BaseModel):
    status: str = "pending"
    has_analysis: bool = False  # analysis.json exists -> the site renders a city page
    timeline: Timeline = Field(default_factory=Timeline)
    quality: Quality | None = None
    totals_mii_lei: dict[str, float] = Field(default_factory=dict)
    top_capitole: list[Capitol] = Field(default_factory=list)
    infografic: dict | None = None  # chart-ready block from analysis.json
    executie: Executie | None = None  # Forexebug quarterly execution
    llm_models: list[str] = Field(default_factory=list)
    files: Files = Field(default_factory=Files)


class City(BaseModel):
    siruta: str
    name: str
    county: str
    county_code: str
    populatie: int | None = None  # RPL2021; reference/municipii.json
    suprafata_km2: float | None = None
    years: dict[str, CityYear] = Field(default_factory=dict)  # keyed by str(year)


class Corpus(BaseModel):
    schema_version: int = SCHEMA_VERSION
    years: list[int] = Field(default_factory=list)  # newest first
    cities: list[City] = Field(default_factory=list)

    def year_rows(self, year: int) -> list[tuple[City, CityYear]]:
        key = str(year)
        return [(c, c.years[key]) for c in self.cities if key in c.years]


def discover_years(data_root: Path) -> list[int]:
    """Corpus years with a manifest under data/, newest first."""
    return sorted(
        (int(d.name) for d in data_root.iterdir()
         if d.name.isdigit() and (d / "manifest.json").exists()),
        reverse=True,
    )


def _load_executie(repo_root: Path, year: int, pdf: Path) -> Executie | None:
    """execution.json for the same city-year, if the quarter reports were built.

    Execution lives in its own tree (data/execution/<year>/<county>/<city>/)
    but uses the same county/city directory convention as the budget corpus.
    """
    try:
        county_dir, city_dir = pdf.parent.parent.name, pdf.parent.name
    except (AttributeError, IndexError):
        return None
    path = repo_root / "data" / "execution" / str(year) / county_dir / city_dir / "execution.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    if not d.get("trimestru"):
        return None
    return Executie(**{k: d.get(k) for k in Executie.model_fields if k in d})


def city_year(manifest: Manifest, c: CityEntry) -> CityYear:
    conv = c.entry.get("conversion") or {}
    repo_root = manifest.root.parent.parent
    tl = c.entry.get("timeline") or {}

    files = Files(source_url=c.entry.get("source_url"))
    if c.pdf.exists() and c.pdf.stat().st_size <= GITHUB_FILE_LIMIT:
        files.pdf = str(c.pdf.relative_to(repo_root))
    xlsx = c.pdf.with_suffix(".xlsx")
    if xlsx.exists():
        files.xlsx = str(xlsx.relative_to(repo_root))

    cy = CityYear(
        status=conv.get("status") or "pending",
        timeline=Timeline(**{k: tl.get(k) for k in Timeline.model_fields}),
        files=files,
    )
    apath = c.pdf.with_name("analysis.json")
    if cy.status == "converted" and apath.exists():
        a = json.loads(apath.read_text())
        cy.has_analysis = True
        cy.quality = Quality(**(a.get("quality") or {}))
        cy.totals_mii_lei = a.get("totals_mii_lei") or {}
        cy.top_capitole = [Capitol(**cap) for cap in a.get("top_capitole") or []]
        cy.infografic = a.get("infografic")
        cy.llm_models = a.get("llm_models") or []
    elif conv:
        cy.quality = Quality(lines=conv.get("lines"), pct_clean=conv.get("pct_clean"))

    cy.executie = _load_executie(repo_root, manifest.year, c.pdf)
    if cy.executie is not None:
        _add_plan_share(cy.executie, cy.totals_mii_lei)
    return cy


def _add_plan_share(e: Executie, plan: dict[str, float]) -> None:
    """How far into the approved plan each reported quarter has run.

    The execution figure is an official report; the plan may be only partially
    extracted from a poor scan, so a ratio far above 100% means the plan is
    broken, not that the city overspent — suppress the ratio and say so.
    """
    def get(block, key):
        return block.get(key) if isinstance(block, dict) else getattr(block, key)

    def put(block, key, value):
        if isinstance(block, dict):
            block[key] = value
        else:
            setattr(block, key, value)

    blocks = [e, *e.trimestre]
    for key in ("venituri", "cheltuieli"):
        planned = plan.get(key)
        if not planned:
            continue
        shares = [None if get(b, key) is None else get(b, key) / planned * 100 for b in blocks]
        if any(s is not None and s > PLAN_RATIO_LIMIT for s in shares):
            e.plan_incomplet = True  # one broken ratio discredits the plan total
            continue
        for block, share in zip(blocks, shares, strict=True):
            if share is not None:
                put(block, f"pct_{key}", round(share, 1))


def _load_reference(repo_root: Path) -> dict:
    """SIRUTA -> {populatie, suprafata_km2, ...} from reference/municipii.json."""
    path = repo_root / "reference" / "municipii.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("municipii", {})


def aggregate_manifests(manifests: list[Manifest]) -> Corpus:
    """Merge per-year manifests into one city-keyed corpus, newest year first."""
    ordered = sorted(manifests, key=lambda m: m.year, reverse=True)
    reference = _load_reference(ordered[0].root.parent.parent)
    cities: dict[str, City] = {}
    for m in ordered:
        for c in m.cities():
            ref = reference.get(c.siruta, {})
            city = cities.setdefault(c.siruta, City(
                siruta=c.siruta, name=c.name,
                county=c.county_name, county_code=c.county_code,
                populatie=ref.get("populatie"),
                suprafata_km2=ref.get("suprafata_km2"),
            ))
            city.years[str(m.year)] = city_year(m, c)
    return Corpus(
        years=[m.year for m in ordered],
        cities=sorted(cities.values(), key=lambda c: (c.county_code, c.name)),
    )


def build_aggregate(data_root: Path) -> Corpus:
    years = discover_years(data_root)
    if not years:
        raise FileNotFoundError(f"niciun manifest sub {data_root}/<an>/manifest.json")
    return aggregate_manifests(
        [Manifest(data_root / str(y) / "manifest.json") for y in years]
    )


def write_aggregate(corpus: Corpus, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(corpus.model_dump_json(indent=1, exclude_none=True))
    return out
