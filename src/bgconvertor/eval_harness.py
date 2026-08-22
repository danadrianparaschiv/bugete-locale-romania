"""Golden-fixture evaluation harness.

Fixtures live in tests/fixtures/golden/*.json: hand-verified anchor facts
for ~a dozen pages covering every layout family and hazard in the corpus.
`bgconvertor eval` scores the extraction stage against them — cell-level,
per layout family — so tuning is measured, never eyeballed.

This module also pins the EXTRACTION OUTPUT CONTRACT that every extractor
(digital, docling, LLM fallback) must produce per page:

    {
      "lines": [
        {
          "raw_code": "5102.2030" | null,   # code as printed
          "code": "20.30" | null,           # normalized dotted form
          "name": "Alte cheltuieli",
          "section": "SECTIUNEA DE FUNCTIONARE" | null,  # nearest heading
          "year": 2026 | null,              # for matrix layouts with year rows
          "values": {"total": "4599.00", ...}   # canonical decimal strings
        }, ...
      ],
      "text": "..." | null                  # non-table page text, if any
    }
"""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, model_validator

from .config import RunConfig
from .runstore import RunStore

DEFAULT_FIXTURES_DIR = Path("tests/fixtures/golden")


class Anchor(BaseModel):
    """One hand-verified fact about a page."""

    column: str | None = None
    value: str | None = None  # canonical decimal string, e.g. "48152.87"
    raw_code: str | None = None
    code: str | None = None
    name_contains: str | None = None
    context_contains: str | None = None  # match against line.section
    row_year: int | None = None
    hard: bool = False  # known-hazard cell (stamp, strikethrough...)

    @model_validator(mode="after")
    def _check(self):
        if self.value is not None and self.column is None:
            raise ValueError("value anchor needs a column")
        if not any([self.raw_code, self.code, self.name_contains]):
            raise ValueError("anchor needs raw_code, code or name_contains")
        return self


class Fixture(BaseModel):
    id: str
    pdf: str
    page: int
    layout: str
    budget: str = "local"
    source_type: str  # "digital" | "scanned"
    hazards: list[str] = []
    columns: list[str] = []
    anchors: list[Anchor] = []
    text_contains: list[str] = []
    notes: str = ""


class AnchorResult(BaseModel):
    anchor: Anchor
    matched: bool
    detail: str = ""


class FixtureResult(BaseModel):
    fixture_id: str
    layout: str
    status: str  # "evaluated" | "missing"
    anchors_total: int = 0
    anchors_matched: int = 0
    hard_total: int = 0
    hard_matched: int = 0
    text_total: int = 0
    text_matched: int = 0
    misses: list[str] = []


def load_fixtures(fixtures_dir: Path) -> list[Fixture]:
    fixtures = [
        Fixture.model_validate_json(p.read_text())
        for p in sorted(fixtures_dir.glob("*.json"))
    ]
    if not fixtures:
        raise FileNotFoundError(f"no fixtures in {fixtures_dir}")
    return fixtures


def _fold(s: str) -> str:
    """Case- and diacritic-insensitive comparison form (OCR mangles ă/ș/ț)."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _fuzzy_contains(needle: str, haystack: str) -> bool:
    """Substring match tolerant to OCR noise ('Politie locaia' ~ 'Politie locala')."""
    n, h = _fold(needle), _fold(haystack)
    if n in h:
        return True
    from rapidfuzz import fuzz

    return fuzz.partial_ratio(n, h) >= 85


def _line_matches(line: dict, anchor: Anchor) -> bool:
    if anchor.raw_code and (line.get("raw_code") or "").replace(" ", "") != anchor.raw_code:
        return False
    if anchor.code and line.get("code") != anchor.code:
        return False
    if anchor.name_contains and not _fuzzy_contains(anchor.name_contains, line.get("name") or ""):
        return False
    if anchor.context_contains and not _fuzzy_contains(
        anchor.context_contains, line.get("section") or ""
    ):
        return False
    if anchor.row_year is not None and line.get("year") != anchor.row_year:
        return False
    return True


def _value_equal(got: str | None, expected: str) -> bool:
    if got is None:
        return False
    if expected == "X" or got == "X":
        return got == expected
    try:
        return Decimal(got) == Decimal(expected)
    except InvalidOperation:
        return False


def check_anchor(payload: dict, anchor: Anchor) -> AnchorResult:
    candidates = [ln for ln in payload.get("lines", []) if _line_matches(ln, anchor)]
    if not candidates:
        return AnchorResult(anchor=anchor, matched=False, detail="no matching line")
    if anchor.value is None:  # presence-only anchor
        return AnchorResult(anchor=anchor, matched=True)
    for line in candidates:
        got = (line.get("values") or {}).get(anchor.column)
        if _value_equal(got, anchor.value):
            return AnchorResult(anchor=anchor, matched=True)
    got_vals = [(line.get("values") or {}).get(anchor.column) for line in candidates]
    return AnchorResult(
        anchor=anchor, matched=False, detail=f"expected {anchor.value}, got {got_vals}"
    )


def evaluate_fixture(fixture: Fixture, payload: dict | None) -> FixtureResult:
    r = FixtureResult(fixture_id=fixture.id, layout=fixture.layout, status="missing")
    if payload is None:
        return r
    r.status = "evaluated"

    for anchor in fixture.anchors:
        res = check_anchor(payload, anchor)
        r.anchors_total += 1
        r.anchors_matched += res.matched
        if anchor.hard:
            r.hard_total += 1
            r.hard_matched += res.matched
        if not res.matched:
            label = anchor.raw_code or anchor.code or anchor.name_contains
            col = f" {anchor.column}" if anchor.column else ""
            r.misses.append(f"{label}{col}: {res.detail}")

    haystack = (payload.get("text") or "") + " ".join(
        ln.get("name") or "" for ln in payload.get("lines", [])
    )
    for needle in fixture.text_contains:
        r.text_total += 1
        if _fuzzy_contains(needle, haystack):
            r.text_matched += 1
        else:
            r.misses.append(f"text missing: {needle!r}")
    return r


def evaluate_all(
    config: RunConfig,
    fixtures_dir: Path,
    project_root: Path,
    stage: str = "extract",
) -> list[FixtureResult]:
    results = []
    stores: dict[str, RunStore] = {}
    for fixture in load_fixtures(fixtures_dir):
        pdf_path = project_root / fixture.pdf
        if fixture.pdf not in stores and pdf_path.exists():
            stores[fixture.pdf] = RunStore(config, pdf_path)
        store = stores.get(fixture.pdf)
        payload = None
        if store:
            payload = store.get(stage, fixture.page)
            if stage == "extract":
                # same precedence as assembly: full-page LLM extraction wins
                payload = store.get("llm_extract", fixture.page) or payload
        results.append(evaluate_fixture(fixture, payload))
    return results


def summarize_by_layout(results: list[FixtureResult]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in results:
        agg = out.setdefault(
            r.layout,
            {"fixtures": 0, "missing": 0, "anchors_total": 0, "anchors_matched": 0},
        )
        agg["fixtures"] += 1
        if r.status == "missing":
            agg["missing"] += 1
        agg["anchors_total"] += r.anchors_total + r.text_total
        agg["anchors_matched"] += r.anchors_matched + r.text_matched
    return out
