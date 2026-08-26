"""Per-city analysis snapshot, written next to the workbook as analysis.json.

Computed from the validated ConversionResult at conversion time, so the
static site generator (and CI) needs only committed files — no run stores.
All figures are drawn from VERIFIED lines only and are in mii lei.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path

from .model import ConversionResult
from .years import annual_columns, estimate_columns, role_year

ANALYSIS_SCHEMA_VERSION = 3

TRIM_COLS = ("trim1", "trim2", "trim3", "trim4")

# revenue chapters grouped by who controls the money (rest -> "proprii")
GRUP_STAT = {"11.02", "42.02", "43.02"}
GRUP_UE = {"45.02", "46.02", "48.02"}

# functional codes that are grand totals, "Partea ..." subtotals, or the
# excedent/deficit block — aggregates, not chapters
AGG_FUNC_PREFIX = {"49", "50", "59", "63", "69", "79", "89", "96", "97", "98", "99"}


def _is_total_section(ln) -> bool:
    """Line belongs to the whole-budget view rather than a section split.

    The section field is only trustworthy when it names one of the two legal
    sections; many layouts leave stray header text in it, and treating that as
    a section hid every capitol in those documents.
    """
    return (ln.section or "TOTAL").strip().upper() not in ("FUNCTIONARE", "DEZVOLTARE")


def _plausible(total: float | None, parts: list[float]) -> float | None:
    """Drop a total that the document's own parts contradict.

    A whole-budget figure can never be smaller than the largest single chapter
    inside it; when it is, the row we matched is not the total (mis-parsed
    page, per-institution annex, stray line).
    """
    if total is None or not parts:
        return total
    return total if total >= max(parts) else None


def _is_total_venituri(ln) -> bool:
    """The 'TOTAL VENITURI' row, however the document spells its code.

    Layouts print it as ``000102``, ``00.01`` or ``0001``; matching the raw
    string alone silently missed every dotted variant, which is most of them.
    """
    if ln.kind != "revenue":
        return False
    digits = (ln.raw_code or "").replace(".", "")
    return digits.startswith("0001") or (ln.code or "") == "00.01"


def _is_total_cheltuieli(ln) -> bool:
    return (ln.kind == "expense_functional"
            and ln.code in ("50.02", "50.10", "49.02", "49.10")
            and "CHELTUIELI" in ln.name.upper())


def _is_capitol(ln) -> bool:
    return (ln.code.split(".")[0] not in AGG_FUNC_PREFIX
            and not ln.name.lstrip().lower().startswith("partea"))


def _line_total(ln) -> Decimal | None:
    for col in annual_columns(ln.values):
        if col in ln.values:
            return ln.values[col]
    return None


def _clean_name(name: str) -> str:
    """Drop the printed '(cod 42.02.01+…)' enumerations and 'CAP.' prefixes."""
    name = re.sub(r"\s*©\s*MINDSOFT-SICO\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s*\(\s*cod[^)]*\)?\s*", " ", name, flags=re.I)
    name = re.sub(r"^\s*CAP\.?\s*", "", name)
    return " ".join(name.split()).strip(" ,+")


def _floats(ln, cols) -> list[float] | None:
    """All requested columns as floats, or None if any is absent."""
    if not all(c in ln.values for c in cols):
        return None
    return [float(ln.values[c]) for c in cols]


def infografic(result: ConversionResult) -> dict | None:
    """Chart-ready snapshot of the main local budget (suffix 02), verified lines only.

    Every block is optional: what cannot be assembled from verified lines is
    simply absent, and the site falls back to the plain top-10 table. Nothing
    is ever estimated to fill a gap.
    """
    docs = [d for d in result.documents if d.suffix == "02"]
    verified = [
        ln for d in docs for ln in d.lines
        if ln.kind != "heading" and ln.code and ln.strictly_verified
    ]
    if not verified:
        return None

    def rows(pred):
        return [ln for ln in verified if pred(ln)]

    def total_row(kind, codes, section):
        """Largest matching total row in a section — layouts repeat it per page."""
        best = None
        for ln in verified:
            if ln.kind != kind:
                continue
            if not (_is_total_section(ln) if section == "TOTAL"
                    else (ln.section or "").strip().upper() == section):
                continue
            # the grand-total code also lands on "Partea a N-a ..." headings in
            # some layouts; those are subtotals, not the total
            match = _is_total_venituri(ln) if kind == "revenue" else (
                ln.code in codes and not ln.name.lstrip().lower().startswith("partea"))
            v = _line_total(ln)
            if not match or v is None or float(v) <= 0:
                continue
            if best is None or float(v) > float(_line_total(best)):
                best = ln
        return best

    out: dict = {"unitate": "mii lei", "chart_quality": {}}
    tot_ch = total_row("expense_functional", ("50.02", "49.02"), "TOTAL")
    tot_ven = total_row("revenue", (), "TOTAL")
    total_cheltuieli = float(_line_total(tot_ch)) if tot_ch else None
    total_venituri = float(_line_total(tot_ven)) if tot_ven else None

    # venituri pe surse: revenue chapters (xx.02), no aggregates (00.*, 49.*)
    surse = []
    for ln in rows(lambda x: x.kind == "revenue" and x.code and len(x.code) == 5
                   and _is_total_section(x)
                   and not x.code.startswith(("00.", "49."))):
        v = _line_total(ln)
        if v is None or float(v) == 0:
            continue
        grup = "stat" if ln.code in GRUP_STAT else "ue" if ln.code in GRUP_UE else "proprii"
        surse.append({"cod": ln.code, "nume": _clean_name(ln.name)[:80], "grup": grup, "val": float(v)})
    surse.sort(key=lambda s: -s["val"])
    if surse and total_venituri:
        acoperire = sum(s["val"] for s in surse) / total_venituri * 100
        if 90 <= acoperire <= 110:
            out["venituri"] = {"total": total_venituri, "surse": surse,
                               "acoperire_pct": round(acoperire, 1)}
            out["chart_quality"]["venituri"] = {
                "coverage_pct": round(acoperire, 1),
                "coverage_note": (
                    f"sursele afișate însumează {acoperire:.1f}% din totalul veniturilor"
                ),
                "confidence": "strictly_verified_cells",
                "recall_measured": False,
            }

    # capitole with functionare/dezvoltare split, quarters, and subchapters
    def by_code(kind, length, section):
        best: dict[str, object] = {}
        for ln in rows(lambda x: x.kind == kind and x.code and len(x.code) == length
                       and (_is_total_section(x) if section == "TOTAL"
                            else (x.section or "").strip().upper() == section)):
            v = _line_total(ln)
            if v is None:
                continue
            prev = best.get(ln.code)
            if prev is None or float(v) > float(_line_total(prev)):
                best[ln.code] = ln
        return best

    cap_t = by_code("expense_functional", 5, "TOTAL")
    cap_f = by_code("expense_functional", 5, "FUNCTIONARE")
    cap_d = by_code("expense_functional", 5, "DEZVOLTARE")
    sub_t = by_code("expense_functional", 8, "TOTAL")
    capitole = []
    for cod, ln in cap_t.items():
        if not _is_capitol(ln):
            continue
        val = float(_line_total(ln))
        if val == 0:
            continue
        copii = sorted(
            ({"nume": _clean_name(s.name)[:70], "val": float(_line_total(s))}
             for c, s in sub_t.items() if c.startswith(cod) and float(_line_total(s)) > 0),
            key=lambda k: -k["val"])[:8]
        cap = {"cod": cod, "nume": _clean_name(ln.name)[:70], "val": val, "copii": copii}
        for key, src in (("func", cap_f.get(cod)), ("dezv", cap_d.get(cod))):
            if src is not None:
                cap[key] = float(_line_total(src))
        trim = _floats(ln, TRIM_COLS)
        if trim:
            cap["trim"] = trim
        capitole.append(cap)
    capitole.sort(key=lambda c: -c["val"])
    if capitole and total_cheltuieli:
        acoperire = sum(c["val"] for c in capitole) / total_cheltuieli * 100
        if 90 <= acoperire <= 110:
            out["capitole"] = capitole
            out["total_cheltuieli"] = total_cheltuieli
            quality = {
                "coverage_pct": round(acoperire, 1),
                "coverage_note": (
                    f"capitolele afișate însumează {acoperire:.1f}% din totalul cheltuielilor"
                ),
                "confidence": "strictly_verified_cells",
                "recall_measured": False,
            }
            out["chart_quality"]["cheltuieli"] = quality
            out["chart_quality"]["100_lei"] = dict(quality)

    # sections + quarterly rhythm from the section total rows
    fu = total_row("expense_functional", ("50.02", "49.02"), "FUNCTIONARE")
    dv = total_row("expense_functional", ("50.02", "49.02"), "DEZVOLTARE")
    if fu is not None and dv is not None:
        out["sectiuni"] = {"functionare": float(_line_total(fu)),
                           "dezvoltare": float(_line_total(dv))}
        trim_f, trim_d = _floats(fu, TRIM_COLS), _floats(dv, TRIM_COLS)
        trim_v = _floats(tot_ven, TRIM_COLS) if tot_ven is not None else None
        if trim_f and trim_d:
            out["trim"] = {"functionare": trim_f, "dezvoltare": trim_d}
            if trim_v:
                out["trim"]["venituri"] = trim_v
            series = 3 if trim_v else 2
            out["chart_quality"]["trim"] = {
                "coverage_pct": 100.0,
                "coverage_note": (
                    f"{series * 4} din {series * 4} celule trimestriale necesare sunt prezente"
                ),
                "confidence": "strictly_verified_cells",
                "recall_measured": False,
            }

    # multi-year projections from the printed estimate columns
    if tot_ch is not None:
        est_cols = estimate_columns(tot_ch.values)
        est_c = _floats(tot_ch, est_cols) if est_cols else None
        est_v = _floats(tot_ven, est_cols) if tot_ven is not None and est_cols else None
        if est_c and total_cheltuieli is not None:
            annual = annual_columns(tot_ch.values)
            base_year = role_year(annual[0]) if annual else None
            if base_year is None:
                base_year = int(est_cols[0][3:]) - 1
            out["ani"] = {
                "years": [base_year, *(int(column[3:]) for column in est_cols)],
                "cheltuieli": [total_cheltuieli, *est_c],
            }
            if est_v and total_venituri is not None:
                out["ani"]["venituri"] = [total_venituri, *est_v]
            series = 2 if "venituri" in out["ani"] else 1
            cells = series * len(out["ani"]["years"])
            out["chart_quality"]["ani"] = {
                "coverage_pct": 100.0,
                "coverage_note": (
                    f"{cells} din {cells} valori necesare seriilor afișate sunt prezente"
                ),
                "confidence": "strictly_verified_cells",
                "recall_measured": False,
            }

    # a chart-worthy snapshot needs at least the expense breakdown
    return out if "capitole" in out else None


def city_analysis(result: ConversionResult) -> dict:
    stats = result.stats()
    # Headline municipality figures must come from the main local budget
    # (source suffix .02).  Annexes financed from own revenues (.10), special
    # funds, loans, or external grants are useful conversion outputs, but they
    # are not interchangeable with the municipality-wide plan.
    main_verified = [
        ln
        for doc in result.documents
        if doc.suffix == "02"
        for ln in doc.lines
        if ln.kind != "heading" and ln.code and ln.strictly_verified
    ]

    def first_total(pred) -> float | None:
        """Largest matching total: the row repeats per page and per section,
        and the whole-budget figure is the largest of them."""
        vals = [float(_line_total(ln)) for ln in main_verified
                if pred(ln) and _line_total(ln) is not None and float(_line_total(ln)) > 0]
        return max(vals) if vals else None

    total_venituri = first_total(
        lambda ln: _is_total_venituri(ln) and _is_total_section(ln)
    )
    total_cheltuieli = first_total(
        lambda ln: _is_total_cheltuieli(ln) and _is_total_section(ln)
    )

    # top functional capitole by total, verified lines, main local-budget doc
    capitole: dict[str, dict] = {}
    for ln in main_verified:
        if ln.kind != "expense_functional" or not ln.code or len(ln.code) != 5:
            continue
        if not _is_capitol(ln):  # skip TOTAL / "Partea ..." / excedent aggregates
            continue
        v = _line_total(ln)
        if v is None or not _is_total_section(ln):
            continue
        prev = capitole.get(ln.code)
        if prev is None or float(v) > prev["total"]:
            capitole[ln.code] = {
                "code": ln.code,
                "name": _clean_name(ln.name)[:70],
                "total": float(v),
            }
    all_capitole = sorted(capitole.values(), key=lambda c: -c["total"])
    top_capitole = all_capitole[:10]


    # a total smaller than the largest chapter under it is not the total
    rev_chapters = [
        float(_line_total(ln)) for ln in main_verified
        if ln.kind == "revenue" and ln.code and len(ln.code) == 5
        and not ln.code.startswith(("00.", "49.")) and _is_total_section(ln)
        and _line_total(ln) is not None
    ]
    total_venituri = _plausible(total_venituri, rev_chapters)
    total_cheltuieli = _plausible(total_cheltuieli, [c["total"] for c in all_capitole])

    # No fallback total for expenses: summing the verified capitole was tried
    # and understates badly wherever those capitole are themselves partial
    # (Botoșani would report 48.290 against an actual ~700.000 mii lei). An
    # absent figure is honest; a plausible-looking wrong one is not.

    # provenance: which LLMs contributed lines/values ("llm:<model>" sources);
    # bare "llm" (pre-provenance caches) reports as "llm (model neînregistrat)"
    llm_models = sorted({
        source.split(":", 1)[1] if ":" in source else "llm (model neînregistrat)"
        for doc in result.documents for ln in doc.lines
        for source in ln.provenance_sources
        if source.startswith("llm")
    })

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "quality": {
            "schema_version": stats["quality_schema_version"],
            "metric": stats["metric"],
            "recall_measured": stats["recall_measured"],
            "lines": stats["lines"],
            "lines_strictly_verified": stats["lines_strictly_verified"],
            "pct_lines_strictly_verified": stats["pct_lines_strictly_verified"],
            "numeric_cells": stats["numeric_cells"],
            "numeric_cells_strictly_verified": stats["numeric_cells_strictly_verified"],
            "pct_numeric_cells_strictly_verified": stats[
                "pct_numeric_cells_strictly_verified"
            ],
            "scope": stats["scope"],
            # Kept while downstream consumers migrate to the explicit name.
            "pct_clean": stats["pct_clean"],
            "errors": stats["issues"]["error"],
            "warnings": stats["issues"]["warning"],
            "info": stats["issues"]["info"],
            "documents": stats["documents"],
        },
        "llm_models": llm_models,
        "totals_mii_lei": {
            "venituri": total_venituri,
            "cheltuieli": total_cheltuieli,
        },
        "top_capitole": top_capitole,
        "infografic": infografic(result),
        "note": (
            "Figuri numai din linii fără erori, avertismente sau note de validare; "
            "mii lei. Rata observată nu măsoară rândurile lipsă. Vezi DISCLAIMER.md."
        ),
    }


def write_analysis(
    result: ConversionResult,
    out: Path,
    publication: dict | None = None,
) -> Path:
    payload = city_analysis(result)
    if publication is not None:
        payload["publication"] = publication
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, out)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return out
