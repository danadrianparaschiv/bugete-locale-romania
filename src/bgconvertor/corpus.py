"""Corpus-level outputs: one normalized dataset + a cross-file quality report.

The export is long-format (one row per code+column+value) so cross-
municipality analysis is a groupby away, with provenance and verification
carried on every row:

    year, SIRUTA, municipality, document, budget, section,
    functional_code, economic_code, column, value, unit, page, source,
    verification_status, validation_evidence

Legacy ``code`` and ``func_code`` columns remain alongside the explicit
classification fields so existing consumers can migrate without ambiguity.

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
from .nomenclator import load_registry_for_year
from .runstore import RunStore

log = logging.getLogger("bgc.corpus")

COLUMNS = [
    "municipality", "siruta", "county_code", "county", "year",
    "document", "context_id", "institution", "budget", "suffix", "section", "kind",
    "code", "code_source", "func_code", "functional_code", "economic_code",
    "name", "column", "value", "unit", "source", "verified",
    "verification_status", "validation_evidence", "validation_issues", "page",
]

FACT_UNIT = "mii lei"


def _classification_codes(line) -> tuple[str | None, str | None]:
    """Return the explicit functional/economic code pair for one fact.

    Revenue indicators belong to the economic classification.  Functional
    expense rows carry only a functional code, while detailed economic
    expense rows carry their parent functional context plus their own
    economic code.
    """
    if line.kind == "expense_functional":
        return line.code, None
    if line.kind == "expense_economic":
        return line.func_code, line.code
    if line.kind == "revenue":
        return None, line.code
    return line.func_code, None


def _validation_evidence(line, column: str, cell_source: str) -> str:
    """Compact positive and negative evidence for one exported numeric fact.

    Validators historically stored only findings.  This contract also states
    which positive controls the fact passed.  The evidence deliberately does
    not claim PDF recall: a clean extracted cell says nothing about an absent
    row or page.
    """
    relevant = [
        issue for issue in line.issues
        if issue.column is None or issue.column == column
    ]
    findings = [
        {
            "check": issue.check,
            "severity": issue.severity,
            "message": issue.message,
            "column": issue.column,
        }
        for issue in line.issues
    ]
    passed = []
    failed_checks = {issue.check for issue in relevant}
    if "V1_code" not in failed_checks:
        passed.append("V1_code_or_recognized_rollup")
    checksum_columns = {"total", "trim1", "trim2", "trim3", "trim4"}
    if checksum_columns <= set(line.values) and column in checksum_columns:
        if "V3_row_checksum" not in failed_checks:
            passed.append("V3_row_checksum")
    if not line.issues:
        passed.append("strict_line_validation")
    evidence = {
        "schema_version": 1,
        "status": "strictly_verified" if not line.issues else "flagged",
        "cell_source": cell_source,
        "code_source": line.code_source or line.source,
        "passed": passed,
        "findings": findings,
        "recall_measured": False,
    }
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    from .years import infer_budget_year_from_path

    budget_year = infer_budget_year_from_path(pdf)
    registry = load_registry_for_year(
        config.reference_dir, budget_year
    )
    if pdf.suffix.lower() in {".xls", ".xlsx"}:
        if budget_year is None:
            raise ValueError(f"cannot infer budget year from native source path: {pdf}")
        from .native_workbook import convert_workbook

        return convert_workbook(pdf, budget_year, registry)

    from pypdf import PdfReader

    from .assemble import assemble
    from .validate import validate

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
            functional_code, economic_code = _classification_codes(ln)
            for column, value in ln.values.items():
                cell_source = ln.value_sources.get(column, ln.source)
                yield {
                    **ident,
                    "document": doc.title[:160],
                    "context_id": ln.subdocument or doc.context_id,
                    "institution": ln.institution or doc.institution,
                    "budget": doc.budget,
                    "suffix": doc.suffix,
                    "section": ln.section,
                    "kind": ln.kind,
                    "code": ln.code,
                    "code_source": ln.code_source,
                    "func_code": ln.func_code,
                    "functional_code": functional_code,
                    "economic_code": economic_code,
                    "name": ln.name[:120],
                    "column": column,
                    "value": str(value),
                    "unit": FACT_UNIT,
                    "source": cell_source,
                    "verified": verified,
                    "verification_status": "strictly_verified" if verified else "flagged",
                    "validation_evidence": _validation_evidence(
                        ln, column, cell_source
                    ),
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
    """Converted PDF/native-Excel sources discoverable from committed state.

    The historical function name is retained for API compatibility. Corpus
    manifests, rather than a single newest-year manifest, define the public
    source list; ad-hoc root-level PDFs remain discoverable through run cache.
    """
    from .manifest import Manifest
    from .runstore import store_key

    candidates = sorted(root.glob("*.pdf"))
    found = []
    for pdf in candidates:
        stage_dir = config.runs_dir / store_key(pdf) / "extract"
        if stage_dir.is_dir() and any(stage_dir.glob("p*.json")):
            found.append(pdf)

    data_root = root if root.name == "data" else root / "data"
    manifests = sorted(data_root.glob("[0-9][0-9][0-9][0-9]/manifest.json"))
    for manifest_path in manifests:
        manifest = Manifest(manifest_path)
        for city in manifest.cities():
            if not city.pdf.exists():
                continue
            if (city.entry.get("conversion") or {}).get("status") != "converted":
                continue
            found.append(city.pdf)
    return list(dict.fromkeys(found))
