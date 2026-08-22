"""Per-city analysis snapshot, written next to the workbook as analysis.json.

Computed from the validated ConversionResult at conversion time, so the
static site generator (and CI) needs only committed files — no run stores.
All figures are drawn from VERIFIED lines only and are in mii lei.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from .model import ConversionResult

TOTAL_COLS = ("total", "total_2026", "buget_2026")


def _line_total(ln) -> Decimal | None:
    for col in TOTAL_COLS:
        if col in ln.values:
            return ln.values[col]
    return None


def city_analysis(result: ConversionResult) -> dict:
    stats = result.stats()
    verified = [
        ln
        for doc in result.documents
        for ln in doc.lines
        if ln.kind != "heading" and ln.code and not any(i.severity == "error" for i in ln.issues)
    ]

    def first_total(pred) -> float | None:
        for ln in verified:
            if pred(ln):
                v = _line_total(ln)
                if v is not None:
                    return float(v)
        return None

    total_venituri = first_total(
        lambda ln: ln.kind == "revenue" and (ln.raw_code or "").startswith("0001")
        and (ln.section in (None, "TOTAL"))
    )
    total_cheltuieli = first_total(
        lambda ln: ln.kind == "expense_functional"
        and ln.code in ("50.02", "50.10", "49.02", "49.10")
        and "CHELTUIELI" in ln.name.upper()
        and (ln.section in (None, "TOTAL"))
    )

    # top functional capitole by total, verified lines, main local-budget doc
    capitole: dict[str, dict] = {}
    for ln in verified:
        if ln.kind != "expense_functional" or not ln.code or len(ln.code) != 5:
            continue
        v = _line_total(ln)
        if v is None or ln.section not in (None, "TOTAL"):
            continue
        prev = capitole.get(ln.code)
        if prev is None or float(v) > prev["total"]:
            capitole[ln.code] = {"code": ln.code, "name": ln.name[:70], "total": float(v)}
    top_capitole = sorted(capitole.values(), key=lambda c: -c["total"])[:10]

    return {
        "quality": {
            "lines": stats["lines"],
            "pct_clean": stats["pct_clean"],
            "errors": stats["issues"]["error"],
            "warnings": stats["issues"]["warning"],
            "documents": stats["documents"],
        },
        "totals_mii_lei": {
            "venituri": total_venituri,
            "cheltuieli": total_cheltuieli,
        },
        "top_capitole": top_capitole,
        "note": "Figuri din liniile verificate aritmetic; mii lei. Vezi DISCLAIMER.md.",
    }


def write_analysis(result: ConversionResult, out: Path) -> Path:
    out.write_text(json.dumps(city_analysis(result), ensure_ascii=False, indent=2))
    return out
