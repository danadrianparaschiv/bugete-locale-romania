"""Corpus-level outputs: one normalized dataset + a cross-file quality report.

The export is long-format (one row per code+column+value) so cross-
municipality analysis is a groupby away, with provenance and verification
carried on every row:

    municipality, document, budget, suffix, section, kind, code, func_code,
    name, column, value, source (digital|ocr|llm), verified,
    verification_status, validation_issues, page

verified=True means no validator emitted an error, warning, or information
issue for the line.  It is an observed consistency flag, not a recall claim:
rows or cells absent from extraction are not present in the denominator.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .config import RunConfig
from .model import ConversionResult
from .nomenclator import load_registry
from .runstore import RunStore

log = logging.getLogger("bgc.corpus")

COLUMNS = [
    "municipality", "siruta", "county_code", "county", "year",
    "document", "budget", "suffix", "section", "kind",
    "code", "func_code", "name", "column", "value", "source", "verified",
    "verification_status", "validation_issues", "page",
]


def _identity(pdf: Path) -> dict:
    """siruta/county/city from the governing manifest; filename fallback."""
    from .manifest import find_manifest

    m = find_manifest(pdf.parent)
    if m:
        c = m.by_pdf(pdf)
        if c:
            return {
                "municipality": c.name,
                "siruta": c.siruta,
                "county_code": c.county_code,
                "county": c.county_name,
                "year": m.year,
            }
    return {
        "municipality": pdf.stem.removeprefix("budget_file_"),
        "siruta": None, "county_code": None, "county": None, "year": None,
    }


def _municipality(pdf: Path) -> str:
    return _identity(pdf)["municipality"]


def build_result(config: RunConfig, pdf: Path) -> ConversionResult:
    from pypdf import PdfReader

    from .assemble import assemble
    from .validate import validate

    registry = load_registry(config.reference_dir)
    store = RunStore(config, pdf)
    pages = list(range(1, len(PdfReader(pdf).pages) + 1))
    result = ConversionResult(
        pdf=pdf.name,
        documents=assemble(store, pages, registry),
        pages_expected=len(pages),
        pages_selected=pages,
        pages_processed=[page for page in pages if store.get("extract", page) is not None],
    )
    return validate(result, registry)


def export_rows(config: RunConfig, pdf: Path):
    """Yield long-format dataset rows for one converted PDF."""
    result = build_result(config, pdf)
    ident = _identity(pdf)
    for doc in result.documents:
        for ln in doc.lines:
            if ln.kind == "heading" or ln.code is None:
                continue
            verified = ln.strictly_verified
            issue_summary = "; ".join(
                f"{i.check}:{i.severity}" for i in ln.issues
            )
            for column, value in ln.values.items():
                yield {
                    **ident,
                    "document": doc.title[:60],
                    "budget": doc.budget,
                    "suffix": doc.suffix,
                    "section": ln.section,
                    "kind": ln.kind,
                    "code": ln.code,
                    "func_code": ln.func_code,
                    "name": ln.name[:120],
                    "column": column,
                    "value": str(value),
                    "source": ln.source,
                    "verified": verified,
                    "verification_status": "strictly_verified" if verified else "flagged",
                    "validation_issues": issue_summary,
                    "page": ln.page,
                }


def export(config: RunConfig, pdfs: list[Path], out: Path) -> dict:
    """Write the consolidated dataset (CSV; Parquet when pyarrow is present)."""
    stats: dict[str, int] = {}
    rows_written = 0
    all_rows = []
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for pdf in pdfs:
            n = 0
            for row in export_rows(config, pdf):
                writer.writerow(row)
                all_rows.append(row)
                n += 1
            stats[_municipality(pdf)] = n
            rows_written += n
            log.info("%s: %d rows", pdf.name, n)

    parquet = None
    if out.suffix == ".csv":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.Table.from_pylist(all_rows)
            parquet = out.with_suffix(".parquet")
            pq.write_table(table, parquet)
        except ImportError:
            pass
    return {"rows": rows_written, "per_file": stats, "parquet": str(parquet) if parquet else None}


def report(config: RunConfig, pdfs: list[Path]) -> list[dict]:
    """Cross-municipality quality and spend summary."""
    out = []
    for pdf in pdfs:
        store = RunStore(config, pdf)
        result = build_result(config, pdf)
        s = result.stats()
        spend = calls = 0
        ledger = store.root / "llm_ledger.jsonl"
        if ledger.exists():
            recs = [json.loads(line) for line in ledger.read_text().splitlines()]
            spend = sum(r["cost_usd"] for r in recs)
            calls = len(recs)
        n_pages = len(store.pages_done("profile"))
        out.append({
            "municipality": _municipality(pdf),
            "pages": n_pages,
            "documents": s["documents"],
            "lines": s["lines"],
            "pct_clean": s["pct_clean"],
            "errors": s["issues"]["error"],
            "warnings": s["issues"]["warning"],
            "llm_calls": calls,
            "llm_cost_usd": round(spend, 2),
        })
    return out


def discover_pdfs(config: RunConfig, root: Path) -> list[Path]:
    """PDFs (flat and corpus-tree) that have extraction artifacts."""
    from .manifest import default_manifest
    from .runstore import store_key

    candidates = sorted(root.glob("*.pdf"))
    m = default_manifest(root)
    if m:
        candidates += [c.pdf for c in m.cities() if c.pdf.exists()]
    found = []
    for pdf in candidates:
        stage_dir = config.runs_dir / store_key(pdf) / "extract"
        if stage_dir.is_dir() and any(stage_dir.glob("p*.json")):
            found.append(pdf)
    return found
