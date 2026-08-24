"""Year-over-year cross-validation: which lines deserve a re-read.

Two editions of the same city are two independent readings of the same
budget structure. A line whose value moved by a factor no budget line
plausibly moves — while the rest of the city moved normally — is far more
likely a misread digit than a real policy change.

This module NEVER corrects anything. It ranks suspects so a targeted
re-read (LLM or human) spends its budget where the evidence is, and the
arithmetic gate stays the only thing that may change a value.

Baseline: each city's OWN median ratio across matched lines. That absorbs
real budget growth, inflation, and even a units change between editions
(lei vs mii lei shows up as a median near 1000 — reported as a
document-level finding, not as thousands of false line suspects).
"""

from __future__ import annotations

import csv
import logging
import statistics
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

log = logging.getLogger("bgc.crossyear")

# a line is only worth comparing if both years carry a real amount
MIN_ABS = Decimal("1")
# how far from the city's own median a ratio must sit to be suspicious
DEVIATION = 8.0
# ratios within this tolerance of a power of ten look like a lost separator
DECIMAL_TOL = 0.02
# a city whose median ratio is near a power of ten changed units, not budgets
UNIT_SHIFT_TOL = 0.05

KEY_FIELDS = ("siruta", "suffix", "section", "kind", "code", "func_code", "column")


@dataclass
class Suspect:
    city: str
    siruta: str
    code: str
    func_code: str | None
    column: str
    section: str | None
    old_value: Decimal
    new_value: Decimal
    ratio: float
    rel: float  # ratio relative to the city's median ratio
    old_verified: bool
    new_verified: bool
    new_page: int | None
    signature: str  # decimal_shift | outlier

    @property
    def priority(self) -> float:
        """Higher = better evidence that the NEW reading is wrong.

        A verified old value against an unverified new one is the strongest
        case; a clean power-of-ten signature is stronger than a vague
        outlier; bigger amounts matter more."""
        p = self.rel if self.rel > 1 else 1 / max(self.rel, 1e-9)
        if self.old_verified and not self.new_verified:
            p *= 4
        elif self.old_verified and self.new_verified:
            p *= 0.5  # both passed arithmetic — likelier a real change
        if self.signature == "decimal_shift":
            p *= 2
        return p * (1 + float(max(abs(self.new_value), abs(self.old_value))) ** 0.25)


@dataclass
class CityReport:
    city: str
    siruta: str
    matched: int = 0
    median_ratio: float = 1.0
    unit_shift: int | None = None  # 1000 when the editions use different units
    suspects: list[Suspect] = field(default_factory=list)


def _num(text: str) -> Decimal | None:
    try:
        v = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return v if abs(v) >= MIN_ABS else None


def _load(path: Path) -> dict[tuple, dict]:
    """Long-format corpus rows keyed by line identity."""
    out: dict[tuple, dict] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            value = _num(row.get("value", ""))
            if value is None or not row.get("code"):
                continue
            key = tuple((row.get(k) or "") for k in KEY_FIELDS)
            # a duplicated identity means the edition itself is ambiguous —
            # keep the first and let V7 hygiene own that problem
            out.setdefault(key, {**row, "_value": value})
    return out


def _near_power_of_ten(ratio: float) -> bool:
    for p in (0.001, 0.01, 0.1, 10, 100, 1000):
        if abs(ratio / p - 1) <= DECIMAL_TOL:
            return True
    return False


def compare(old_csv: Path, new_csv: Path) -> list[CityReport]:
    """Rank re-read candidates per city; nothing is ever modified."""
    old, new = _load(old_csv), _load(new_csv)
    shared = old.keys() & new.keys()
    log.info("linii comparabile: %d (din %d vechi / %d noi)",
             len(shared), len(old), len(new))

    per_city: dict[str, list[tuple]] = {}
    for key in shared:
        o, n = old[key], new[key]
        per_city.setdefault(n.get("siruta") or "?", []).append((key, o, n))

    reports = []
    for siruta, items in per_city.items():
        city = items[0][2].get("municipality") or "?"
        ratios = [float(n["_value"]) / float(o["_value"])
                  for _, o, n in items if o["_value"] != 0]
        if len(ratios) < 20:
            continue  # too little overlap for a trustworthy baseline
        median = statistics.median(ratios)
        rep = CityReport(city=city, siruta=siruta, matched=len(items),
                         median_ratio=median)
        for p in (1000, 0.001):
            if abs(median / p - 1) <= UNIT_SHIFT_TOL:
                rep.unit_shift = int(p) if p > 1 else 1
        for (_key, o, n) in items:
            if o["_value"] == 0:
                continue
            ratio = float(n["_value"]) / float(o["_value"])
            rel = ratio / median if median else ratio
            dev = max(rel, 1 / rel) if rel else 0
            signature = ("decimal_shift" if _near_power_of_ten(rel)
                         else "outlier" if dev >= DEVIATION else None)
            if signature is None:
                continue
            rep.suspects.append(Suspect(
                city=city, siruta=siruta, code=n.get("code") or "",
                func_code=n.get("func_code") or None,
                column=n.get("column") or "", section=n.get("section") or None,
                old_value=o["_value"], new_value=n["_value"],
                ratio=ratio, rel=rel,
                old_verified=str(o.get("verified", "")).lower() == "true",
                new_verified=str(n.get("verified", "")).lower() == "true",
                new_page=int(n["page"]) if (n.get("page") or "").isdigit() else None,
                signature=signature,
            ))
        rep.suspects.sort(key=lambda s: -s.priority)
        reports.append(rep)
    reports.sort(key=lambda r: -len(r.suspects))
    return reports


def write_csv(reports: list[CityReport], out: Path) -> int:
    n = 0
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["oras", "siruta", "cod", "cod_functional", "sectiune",
                    "coloana", "valoare_veche", "valoare_noua", "raport",
                    "raport_relativ", "semnatura", "vechi_verificat",
                    "nou_verificat", "pagina_noua", "prioritate"])
        for rep in reports:
            for s in rep.suspects:
                w.writerow([s.city, s.siruta, s.code, s.func_code or "",
                            s.section or "", s.column, s.old_value, s.new_value,
                            f"{s.ratio:.4g}", f"{s.rel:.4g}", s.signature,
                            s.old_verified, s.new_verified, s.new_page or "",
                            f"{s.priority:.1f}"])
                n += 1
    return n
