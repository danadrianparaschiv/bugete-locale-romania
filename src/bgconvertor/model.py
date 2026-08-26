"""Core data model: budget lines, documents, and validation issues."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["revenue", "expense_functional", "expense_economic", "annex", "heading"]
Severity = Literal["error", "warning", "info"]

QUALITY_SCHEMA_VERSION = 2
QUALITY_METRIC = "observed_strict_line_rate"


class Issue(BaseModel):
    check: str  # V1_code | V2_name | V3_row_checksum | V4_hierarchy | V5_identity | V7_hygiene
    severity: Severity
    message: str
    page: int | None = None
    code: str | None = None
    column: str | None = None


class BudgetLine(BaseModel):
    raw_code: str | None = None
    code: str | None = None  # normalized; economic code for expense_economic lines
    func_code: str | None = None  # capitol context for expense_economic lines
    name: str
    kind: Kind = "heading"
    row_no: int | None = None
    page: int
    section: str | None = None  # TOTAL | FUNCTIONARE | DEZVOLTARE
    values: dict[str, Decimal] = Field(default_factory=dict)
    # Cell-level provenance is needed when paid recovery fills only gaps in a
    # deterministic row. Missing entries inherit ``source`` for old bundles.
    value_sources: dict[str, str] = Field(default_factory=dict)
    x_markers: list[str] = Field(default_factory=list)  # columns holding "X"
    source: str = "digital"  # digital | ocr | llm
    code_source: str | None = None  # independent provenance when only code changed
    issues: list[Issue] = Field(default_factory=list)

    @property
    def strictly_verified(self) -> bool:
        """True only when no validator emitted an error, warning, or info issue."""
        return not self.issues

    @property
    def provenance_sources(self) -> set[str]:
        """Every extractor that contributed a numeric value to this row."""
        sources = set(self.value_sources.values())
        if len(self.value_sources) < len(self.values):
            sources.add(self.source)
        if self.code_source:
            sources.add(self.code_source)
        return sources or {self.source}

    def set_value_with_source(self, column: str, value: Decimal, source: str) -> None:
        """Replace one value without losing provenance of the other cells."""
        previous_source = self.source
        for existing in self.values:
            self.value_sources.setdefault(existing, previous_source)
        self.values[column] = value
        self.value_sources[column] = source
        sources = set(self.value_sources.values())
        self.source = next(iter(sources)) if len(sources) == 1 else "mixed"


class BudgetDocument(BaseModel):
    title: str
    budget: str  # "local" | "own_revenue" | "unknown"
    suffix: str  # "02" | "10"
    pages: list[int]
    lines: list[BudgetLine]
    # Stable validation/analytics scope for repeated forms inside one PDF.
    # Individual institution budgets often print the same document title and
    # the same indicator codes; the fiscal code is the preferred identity,
    # with the first physical page as a deterministic fallback.
    context_id: str | None = None
    institution: str | None = None

    def section_lines(self, section: str | None) -> list[BudgetLine]:
        return [ln for ln in self.lines if ln.section == section]


class ConversionResult(BaseModel):
    pdf: str
    documents: list[BudgetDocument]
    issues: list[Issue] = Field(default_factory=list)  # document-level issues
    # Scope is recorded by the pipeline when known.  It makes partial runs
    # distinguishable from complete-PDF conversions, without pretending that
    # validator cleanliness measures rows which were never extracted.
    pages_expected: int | None = None
    pages_selected: list[int] = Field(default_factory=list)
    pages_processed: list[int] = Field(default_factory=list)

    def all_issues(self) -> list[Issue]:
        out = list(self.issues)
        for doc in self.documents:
            for ln in doc.lines:
                out.extend(ln.issues)
        return out

    def stats(self) -> dict:
        all_lines = [ln for d in self.documents for ln in d.lines]
        lines = [ln for ln in all_lines if ln.kind != "heading"]
        issues = self.all_issues()
        by_sev = {s: sum(1 for i in issues if i.severity == s) for s in ("error", "warning", "info")}
        strict = [ln for ln in lines if not ln.issues]
        # Printed totals and section markers can legitimately carry values
        # without a nomenclator code. They remain heading-kind so analytics do
        # not mistake them for classifications, but they are still exported
        # numeric cells and belong in the quality denominator.
        numeric_cells = sum(len(ln.values) for ln in all_lines)
        strict_numeric_cells = sum(len(ln.values) for ln in all_lines if not ln.issues)
        selected = sorted(set(self.pages_selected))
        processed = sorted(set(self.pages_processed))
        scope_complete = (
            self.pages_expected is not None
            and selected == list(range(1, self.pages_expected + 1))
            and processed == selected
        )
        return {
            "quality_schema_version": QUALITY_SCHEMA_VERSION,
            "metric": QUALITY_METRIC,
            # This is deliberately false.  The validators can measure
            # consistency of extracted lines, not recall of absent rows/cells.
            "recall_measured": False,
            "documents": len(self.documents),
            "lines": len(lines),
            "lines_strictly_verified": len(strict),
            "pct_lines_strictly_verified": (
                round(100 * len(strict) / len(lines), 1) if lines else 0.0
            ),
            "numeric_cells": numeric_cells,
            "numeric_cells_strictly_verified": strict_numeric_cells,
            "pct_numeric_cells_strictly_verified": (
                round(100 * strict_numeric_cells / numeric_cells, 1)
                if numeric_cells else 0.0
            ),
            "scope": {
                "pages_expected": self.pages_expected,
                "pages_selected": len(selected),
                "pages_processed": len(processed),
                "complete_pdf": scope_complete,
            },
            "issues": by_sev,
            # Backward-compatible aliases for existing consumers.  New public
            # artifacts also carry the explicit metric name above.
            "lines_clean": len(strict),
            "pct_clean": round(100 * len(strict) / len(lines), 1) if lines else 0.0,
        }
