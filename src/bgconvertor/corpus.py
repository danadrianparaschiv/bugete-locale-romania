"""Corpus-level outputs: one normalized dataset + a cross-file quality report.

The export is long-format (one row per code+column+value) so cross-
municipality analysis is a groupby away, with provenance and verification
carried on every row:

    municipality, document, budget, suffix, section, kind, code, func_code,
    name, column, value, source (digital|ocr|llm), verified, page

verified=True means the line passed every validator check (codes, names,
row checksums, hierarchy sums) — the tier of data safe to analyze without
looking back at the PDF.
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
    "municipality", "document", "budget", "suffix", "section", "kind",
    "code", "func_code", "name", "column", "value", "source", "verified", "page",
]


def _municipality(pdf: Path) -> str:
    return pdf.stem.removeprefix("budget_file_")


def build_result(config: RunConfig, pdf: Path) -> ConversionResult:
    from pypdf import PdfReader

    from .assemble import assemble
    from .validate import validate

    registry = load_registry(config.reference_dir)
    store = RunStore(config, pdf)
    pages = list(range(1, len(PdfReader(pdf).pages) + 1))
    result = ConversionResult(
        pdf=pdf.name, documents=assemble(store, pages, registry)
    )
    return validate(result, registry)


def export_rows(config: RunConfig, pdf: Path):
    """Yield long-format dataset rows for one converted PDF."""
    result = build_result(config, pdf)
    muni = _municipality(pdf)
    for doc in result.documents:
        for ln in doc.lines:
            if ln.kind == "heading" or ln.code is None:
                continue
            verified = not any(i.severity == "error" for i in ln.issues)
            for column, value in ln.values.items():
                yield {
                    "municipality": muni,
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
            recs = [json.loads(l) for l in ledger.read_text().splitlines()]
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
    """PDFs in `root` that already have a run store with extraction artifacts."""
    found = []
    for pdf in sorted(root.glob("*.pdf")):
        stem_dir = config.runs_dir / pdf.stem / "extract"
        if stem_dir.is_dir() and any(stem_dir.glob("p*.json")):
            found.append(pdf)
    return found
