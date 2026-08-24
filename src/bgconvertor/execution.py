"""Forexebug quarterly execution reports (FXB-EXB-901) -> validated snapshots.

Unlike the budget PDFs, these are already-structured Excel exports from the
Ministry of Finance, so there is no extraction risk: the work is mapping
Forexebug's 6-digit codes onto the classification this project already
validates against (Ordinul MFP 1954/2005) and cross-checking the report's
own totals.

Two conventions differ from the rest of the corpus and are normalized here:

* Forexebug prints values in **lei**; everything else in this project is in
  **mii lei**, so parsed values are divided by 1000.
* Forexebug omits the budget suffix from functional/revenue codes (``510103``)
  and carries it instead in the funding-source column: sources A–D belong to
  the local budget (``.02``), E–G to the self-financed institutions (``.10``).
  So ``510103`` + source ``A`` becomes ``51.02.01.03``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .nomenclator import Registry

log = logging.getLogger("bgc.execution")

SCHEMA_VERSION = 1
LEI_PER_MIE = 1000

# funding-source letter -> which budget document the line belongs to
SOURCE_BUDGET = {
    "A": "local",        # integral de la buget
    "B": "local",        # credite externe
    "C": "local",        # credite interne
    "D": "local",        # fonduri externe nerambursabile
    "E": "own_revenue",  # activități finanțate integral din venituri proprii
    "F": "own_revenue",  # integral venituri proprii
    "G": "own_revenue",  # venituri proprii și subvenții
}
BUDGET_SUFFIX = {"local": "02", "own_revenue": "10"}
SECTION = {"F": "FUNCTIONARE", "D": "DEZVOLTARE"}

_DATE_RE = re.compile(r"LA DATA:\s*(\d{2})-([A-Z]{3})-(\d{2})", re.I)
_CIF_RE = re.compile(r"Cod Fiscal IP:\s*(\d+)\s*Denumire IP\s*:\s*(.+?)\s*$", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


class ExecutionLine(BaseModel):
    kind: str  # revenue | expense_functional
    code: str  # dotted functional/revenue code, budget suffix restored
    econ_code: str | None = None  # dotted economic code (expense lines)
    name: str
    section: str | None = None  # FUNCTIONARE | DEZVOLTARE
    budget: str  # local | own_revenue
    source_class: str  # A..G
    value: float  # mii lei, cumulative since 1 January
    known_code: bool = True  # False when the registry does not enumerate it


class ExecutionReport(BaseModel):
    """One parsed Forexebug workbook."""

    report_type: str = "FXB-EXB-901"
    report_date: str | None = None  # ISO
    entity_cif: str | None = None
    entity_name: str | None = None
    quarter: int | None = None
    lines: list[ExecutionLine] = Field(default_factory=list)
    printed_totals: dict[str, float] = Field(default_factory=dict)  # mii lei
    issues: list[str] = Field(default_factory=list)

    def total(self, kind: str, budget: str = "local") -> float:
        return sum(ln.value for ln in self.lines
                   if ln.kind == kind and ln.budget == budget)


def _iso_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, mon, yy = m.groups()
    month = _MONTHS.get(mon.upper())
    if not month:
        return None
    return f"20{yy}-{month:02d}-{int(day):02d}"


def dotted_code(raw: str | int | float, kind: str, budget: str) -> str | None:
    """``510103`` + local -> ``51.02.01.03``; economic codes keep no suffix."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    s = digits.zfill(6)[:6]
    head, rest = s[0:2], [s[2:4], s[4:6]]
    tail = [p for p in rest if p != "00"]
    if kind == "expense_economic":
        return ".".join([head, *tail])
    return ".".join([head, BUDGET_SUFFIX[budget], *tail])


def parse_report(path: Path, quarter: int | None = None,
                 registry: Registry | None = None) -> ExecutionReport:
    """Parse one Forexebug workbook; values normalized to mii lei."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))

    rep = ExecutionReport(quarter=quarter)
    header_at = None
    for i, row in enumerate(rows[:40]):
        text = " ".join(str(c) for c in row if c is not None)
        if rep.report_date is None:
            rep.report_date = _iso_date(text)
        m = _CIF_RE.search(text)
        if m and rep.entity_cif is None:
            rep.entity_cif, rep.entity_name = m.group(1), m.group(2).strip()
        if row and row[0] and str(row[0]).strip() == "Tip Indicator":
            header_at = i
            break
    if header_at is None:
        rep.issues.append("antetul tabelului nu a fost găsit")
        return rep

    # column positions differ between the report's two sheets (one carries a
    # spacer column), so bind them by header label instead of fixed indices
    col: dict[str, int] = {}
    for j, cell in enumerate(rows[header_at]):
        label = str(cell or "").strip().lower()
        if label.startswith("clasificatie functionala"):
            col["func_desc" if "descriere" in label else "func"] = j
        elif label.startswith("clasificatie economica"):
            col["econ_desc" if "descriere" in label else "econ"] = j
        elif label.startswith("sectiune"):
            col["section"] = j
        elif label.startswith("executie"):
            col["value"] = j
        elif label.startswith("sursa"):
            col["source"] = j
    missing = {"func", "value", "source"} - set(col)
    if missing:
        rep.issues.append(f"coloane lipsă în antet: {sorted(missing)}")
        return rep

    def cell(row, key):
        j = col.get(key)
        return row[j] if j is not None and j < len(row) else None

    for row in rows[header_at + 1:]:
        if not row or row[0] is None:
            continue
        tip = str(row[0]).strip()
        if tip == "Tip Indicator":  # repeated header mid-sheet
            continue
        value = cell(row, "value")
        if tip.startswith("TOTAL"):
            key = "venituri" if "VENITURI" in tip.upper() else "cheltuieli"
            if value is not None:
                rep.printed_totals[key] = float(value) / LEI_PER_MIE
            continue
        if tip.startswith("FXB"):  # trailing report-id row
            continue
        raw_func = cell(row, "func")
        if value is None or raw_func is None:
            continue

        source = cell(row, "source")
        letter = str(source or "")[:1].upper()
        budget = SOURCE_BUDGET.get(letter)
        if budget is None:
            rep.issues.append(f"sursă de finanțare necunoscută: {source!r}")
            continue
        kind = "revenue" if tip.lower().startswith("venit") else "expense_functional"
        code = dotted_code(raw_func, kind, budget)
        if code is None:
            continue
        raw_econ = cell(row, "econ")
        econ = dotted_code(raw_econ, "expense_economic", budget) if raw_econ else None
        known = True
        if registry is not None:
            known = registry.get(kind, code) is not None
        rep.lines.append(ExecutionLine(
            kind=kind, code=code, econ_code=econ,
            name=str(cell(row, "func_desc") or "").strip()[:90],
            section=SECTION.get(str(cell(row, "section") or "")[:1].upper()),
            budget=budget, source_class=letter,
            value=float(value) / LEI_PER_MIE,
            known_code=known,
        ))

    # the report's own totals are the control sum: they cover every funding
    # source, so compare against all lines regardless of budget
    for key, kind in (("venituri", "revenue"), ("cheltuieli", "expense_functional")):
        printed = rep.printed_totals.get(key)
        if printed is None:
            continue
        got = sum(ln.value for ln in rep.lines if ln.kind == kind)
        if printed and abs(got - printed) / printed > 0.001:
            rep.issues.append(
                f"suma liniilor {key} ({got:.1f}) diferă de totalul tipărit ({printed:.1f})"
            )
    return rep


def capitole(rep: ExecutionReport, kind: str = "expense_functional",
             budget: str = "local") -> list[dict]:
    """Execution rolled up to capitol (``65.02``), descending."""
    agg: dict[str, dict] = {}
    for ln in rep.lines:
        if ln.kind != kind or ln.budget != budget:
            continue
        cap = ".".join(ln.code.split(".")[:2])
        e = agg.setdefault(cap, {"cod": cap, "nume": "", "val": 0.0})
        e["val"] += ln.value
        if not e["nume"] and ln.code == cap:
            e["nume"] = ln.name
    return sorted(agg.values(), key=lambda c: -c["val"])


def snapshot(reports: dict[int, ExecutionReport], registry: Registry | None = None) -> dict:
    """Per-city execution block: newest quarter in full, plus a quarterly series.

    Only the local budget (sources A–D) is reported, to match what the site
    shows for the approved budget.
    """
    if not reports:
        return {}
    quarters = sorted(reports)
    latest = reports[quarters[-1]]
    caps = capitole(latest, budget="local")
    if registry is not None:
        for c in caps:
            if not c["nume"]:
                e = registry.get("expense_functional", c["cod"])
                if e:
                    c["nume"] = e.name
    unknown = sum(1 for ln in latest.lines if not ln.known_code)
    return {
        "unitate": "mii lei",
        "sursa": latest.report_type,
        "trimestru": quarters[-1],
        "la_data": latest.report_date,
        "cif": latest.entity_cif,
        "venituri": latest.total("revenue"),
        "cheltuieli": latest.total("expense_functional"),
        "capitole": caps,
        "trimestre": [
            {
                "trimestru": q,
                "la_data": reports[q].report_date,
                "venituri": reports[q].total("revenue"),
                "cheltuieli": reports[q].total("expense_functional"),
            }
            for q in quarters
        ],
        "linii": len(latest.lines),
        "coduri_neenumerate": unknown,
        "probleme": latest.issues,
        "note": "Execuție cumulată de la 1 ianuarie, bugetul local (surse A–D). "
                "Raport oficial Forexebug; vezi DISCLAIMER.md.",
    }


def discover_reports(exec_root: Path, county_slug: str, city_slug: str) -> dict[int, Path]:
    """``{1: .../q1/forexebug_execution.xlsx, ...}`` for one city-year."""
    city_dir = exec_root / county_slug / city_slug
    out: dict[int, Path] = {}
    if not city_dir.is_dir():
        return out
    for qdir in sorted(city_dir.glob("q[1-4]")):
        f = qdir / "forexebug_execution.xlsx"
        if f.exists():
            out[int(qdir.name[1:])] = f
    return out


def build_city(exec_root: Path, county_slug: str, city_slug: str,
               registry: Registry | None = None) -> dict:
    """Parse every available quarter for one city and return its snapshot."""
    found = discover_reports(exec_root, county_slug, city_slug)
    reports = {q: parse_report(p, quarter=q, registry=registry) for q, p in found.items()}
    return snapshot(reports, registry)


def write_snapshot(data: dict, out: Path) -> Path:
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return out
