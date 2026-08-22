"""Core data model: budget lines, documents, and validation issues."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["revenue", "expense_functional", "expense_economic", "heading"]
Severity = Literal["error", "warning", "info"]


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
    x_markers: list[str] = Field(default_factory=list)  # columns holding "X"
    source: str = "digital"  # digital | ocr | llm
    issues: list[Issue] = Field(default_factory=list)


class BudgetDocument(BaseModel):
    title: str
    budget: str  # "local" | "own_revenue" | "unknown"
    suffix: str  # "02" | "10"
    pages: list[int]
    lines: list[BudgetLine]

    def section_lines(self, section: str | None) -> list[BudgetLine]:
        return [ln for ln in self.lines if ln.section == section]


class ConversionResult(BaseModel):
    pdf: str
    documents: list[BudgetDocument]
    issues: list[Issue] = Field(default_factory=list)  # document-level issues

    def all_issues(self) -> list[Issue]:
        out = list(self.issues)
        for doc in self.documents:
            for ln in doc.lines:
                out.extend(ln.issues)
        return out

    def stats(self) -> dict:
        lines = [ln for d in self.documents for ln in d.lines if ln.kind != "heading"]
        issues = self.all_issues()
        by_sev = {s: sum(1 for i in issues if i.severity == s) for s in ("error", "warning", "info")}
        clean = sum(1 for ln in lines if not ln.issues)
        return {
            "documents": len(self.documents),
            "lines": len(lines),
            "lines_clean": clean,
            "pct_clean": round(100 * clean / len(lines), 1) if lines else 0.0,
            "issues": by_sev,
        }
