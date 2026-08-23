"""Coordinate-based extraction for born-digital budget PDFs.

v2: works on ANY ruled grid, not just Alba Iulia's 13-line 'detaliat'.
Vertical ruling lines give the column spans (double-ruled borders are
deduplicated); the header band's words decide each column's semantic role
(Cod / Denumire / TOTAL / Trim I-IV / Estimari YYYY / credite ...), so
column count and order are free to vary per budget-software vendor.

Words are clustered into visual rows by y and assigned to columns by
x-center — never by text-stream order, which pypdf demonstrably scrambles.
Output follows the extraction contract in eval_harness.py.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from decimal import Decimal

from ..parsing import NumberParseError, parse_ro_number

log = logging.getLogger("bgc.extract.digital")

SECTION_RE = re.compile(r"=+\s*(SECTIUNEA[A-Z ]*?)\s*=+")
ROW_TOL = 1.6  # y tolerance for clustering words into a visual row
MIN_LINE_GAP = 4.0  # vertical rules closer than this are one border

HEADER_ROLES = [
    (re.compile(r"\bcod\b"), "code"),
    (re.compile(r"denumirea|denumire"), "name"),
    (re.compile(r"\brand\b"), "rowno"),
    (re.compile(r"trim\.?\s*(i|1|l)$|trim\.?\s*(i|1|l)\b(?!i|v|l)"), "trim1"),
    (re.compile(r"trim\.?\s*(ii|11)\b(?!i)"), "trim2"),
    (re.compile(r"trim\.?\s*(iii|ill|lll)"), "trim3"),
    (re.compile(r"trim\.?\s*iv"), "trim4"),
    (re.compile(r"2027"), "est2027"),
    (re.compile(r"2028"), "est2028"),
    (re.compile(r"2029"), "est2029"),
    (re.compile(r"credite|din care"), "credite_stinse"),
    (re.compile(r"\btotal\b"), "total"),
]
HEADER_ANCHOR = re.compile(r"\bcod\b")  # the row that anchors the header band


def _fold(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def extract_page(plumber_page) -> dict:
    boundaries = _column_boundaries(plumber_page)
    words = plumber_page.extract_words(keep_blank_chars=False)
    columns, header_top, header_bottom = _header_columns(words, boundaries)
    if "name" not in columns.values() or "code" not in columns.values():
        raise ValueError(
            "no recognizable grid header (need Cod + Denumire columns) — "
            "not a supported digital layout"
        )

    head_words, body_words = [], []
    for w in words:
        (head_words if w["top"] < header_top else body_words).append(w)
    text = " ".join(w["text"] for w in sorted(head_words, key=lambda w: (w["top"], w["x0"])))
    body_words = [w for w in body_words if w["top"] > header_bottom]

    # parse all visual rows once, then decide this vendor's wrap style:
    # 'inline' (ab/Oradea: the code row carries name text) vs 'name_above'
    # (Brasov: names print on their own row ABOVE the code+values row)
    parsed_rows = []
    for row in _visual_rows(body_words):
        cells = _assign_columns(row, boundaries, columns)
        name = " ".join(w["text"] for w in cells.get("name", []))
        raw_code = "".join(w["text"] for w in cells.get("code", [])) or None
        rand = _rand_no(cells.get("rowno", []))
        values, cell_issues = _parse_values(cells)
        parsed_rows.append((row, name, raw_code, rand, values, cell_issues))

    code_rows = [r for r in parsed_rows if r[2] is not None]
    empty_named = sum(1 for r in code_rows if not r[1].strip())
    name_above = bool(code_rows) and empty_named > 0.5 * len(code_rows)

    lines: list[dict] = []
    section: str | None = None
    pending_names: list[str] = []
    for row, name, raw_code, rand, values, cell_issues in parsed_rows:
        m = SECTION_RE.search(name)
        if m:
            section = m.group(1).strip()

        if raw_code is None and rand is None and not values:
            if not (name and not SECTION_RE.search(name) and set(name) != {"="}):
                continue
            if name_above:
                pending_names.append(name)
            elif lines:
                # continuation of the previous line's wrapped name
                lines[-1]["name"] = (lines[-1]["name"] + " " + name).strip()
            continue
        if name_above and raw_code is not None:
            name = " ".join([*pending_names, name]).strip()
            pending_names = []
        if raw_code is None and values and lines and lines[-1]["raw_code"] and not lines[-1]["values"]:
            # Oradea-style wrap: the code row holds the name start, the
            # continuation row holds the rest of the name AND the values
            prev = lines[-1]
            prev["name"] = (prev["name"] + " " + name).strip()
            prev["values"] = values
            if cell_issues:
                prev["cell_issues"] = prev.get("cell_issues", []) + cell_issues
            continue

        if (raw_code is not None and not name and not values and lines
                and lines[-1]["raw_code"] is None and lines[-1]["name"]):
            # Craiova-style inverse wrap: the NAME row (with values) prints
            # above, the code row below is otherwise empty — reunite them
            code, func_code = _normalize(raw_code)
            prev = lines[-1]
            prev["raw_code"], prev["code"], prev["func_code"] = raw_code, code, func_code
            if rand is not None:
                prev["row_no"] = rand
            continue

        code, func_code = _normalize(raw_code)
        line = {
            "raw_code": raw_code,
            "code": code,
            "func_code": func_code,
            "name": name,
            "row_no": rand,
            "section": section,
            "year": None,
            "values": values,
            "bbox_top": round(min(w["top"] for w in row), 1),
        }
        if cell_issues:
            line["cell_issues"] = cell_issues
        lines.append(line)

    return {"lines": lines, "text": text or None, "layout": "digital_detail"}


def _column_boundaries(page) -> list[float]:
    xs = sorted({round(ln["x0"], 1) for ln in page.lines if abs(ln["x0"] - ln["x1"]) < 0.5})
    deduped: list[float] = []
    for x in xs:
        if not deduped or x - deduped[-1] >= MIN_LINE_GAP:
            deduped.append(x)
    if len(deduped) < 5:
        raise ValueError(
            f"only {len(deduped)} vertical ruling lines — no digital grid on this page"
        )
    return deduped


def _header_columns(words, xs: list[float]) -> tuple[dict[int, str], float, float]:
    """Column roles from the header band; returns (roles, band_top, band_bottom)."""
    anchor_tops = [
        w["top"] for w in words
        if HEADER_ANCHOR.search(_fold(w["text"])) and w["text"].strip().lower() != "codul"
    ]
    if not anchor_tops:
        raise ValueError("no header row containing 'Cod' found")
    anchor = min(anchor_tops)
    band_top, band_bottom = anchor - 50, anchor + 65
    band = [w for w in words if band_top <= w["top"] <= band_bottom]

    columns: dict[int, str] = {}
    for i in range(len(xs) - 1 + 1):
        lo = xs[i]
        hi = xs[i + 1] if i + 1 < len(xs) else float("inf")
        cell = _fold(" ".join(
            w["text"] for w in sorted(band, key=lambda w: (w["top"], w["x0"]))
            if lo <= (w["x0"] + w["x1"]) / 2 < hi
        ))
        if not cell:
            continue
        for pattern, role in HEADER_ROLES:
            if pattern.search(cell) and role not in columns.values():
                columns[i] = role
                break
    return columns, band_top, band_bottom


def _visual_rows(words):
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= ROW_TOL:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _assign_columns(row, xs: list[float], columns: dict[int, str]) -> dict:
    cells: dict[str, list] = {}
    for w in sorted(row, key=lambda w: w["x0"]):
        cx = (w["x0"] + w["x1"]) / 2
        idx = None
        for i in range(len(xs)):
            hi = xs[i + 1] if i + 1 < len(xs) else float("inf")
            if xs[i] <= cx < hi:
                idx = i
                break
        if idx is None:
            continue
        role = columns.get(idx)
        if role:
            cells.setdefault(role, []).append(w)
    return cells


def _rand_no(rand_words) -> int | None:
    for w in rand_words:
        if w["text"].isdigit():
            return int(w["text"])
    return None


NUMERIC_ROLES = (
    "total", "credite_stinse", "trim1", "trim2", "trim3", "trim4",
    "est2027", "est2028", "est2029",
)


def _parse_values(cells) -> tuple[dict, list]:
    values: dict[str, str] = {}
    issues: list[dict] = []
    for role in NUMERIC_ROLES:
        group = cells.get(role, [])
        raw = "".join(w["text"] for w in group)
        if not raw:
            continue
        try:
            parsed = parse_ro_number(raw)
        except NumberParseError:
            issues.append({"column": role, "raw": raw})
            continue
        if parsed is None:
            continue
        values[role] = "X" if parsed == "X" else str(Decimal(parsed))
    return values, issues


def _normalize(raw_code: str | None) -> tuple[str | None, str | None]:
    from ..parsing import split_combined_code

    return split_combined_code(raw_code, aggressive=True)
