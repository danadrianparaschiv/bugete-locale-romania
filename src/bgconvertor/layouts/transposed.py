"""Bistrita-style transposed 'detaliat': indicators are COLUMNS.

Rows carry the periods (col0 = '2029', 'Trim. I', 'TOTAL', ...); one row's
col0 is 'Cod' (indicator codes per column); the remaining rows hold the
wrapped indicator names.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

PERIOD_LABELS = [
    (re.compile(r"^20(2[5-9])$"), lambda m: f"est20{m.group(1)}"),
    (re.compile(r"^trim\.?\s*i?v$"), lambda m: "trim4"),
    (re.compile(r"^trim\.?\s*(iii|ill|lll|11l)$"), lambda m: "trim3"),
    (re.compile(r"^trim\.?\s*(ii|11)$"), lambda m: "trim2"),
    (re.compile(r"^trim\.?\s*(i|1|l)$"), lambda m: "trim1"),
    (re.compile(r"^total$"), lambda m: "total"),
    (re.compile(r"^din care credite"), lambda m: "credite_stinse"),
]


def _period_key(label: str) -> str | None:
    t = fold(label).strip()
    for pattern, keyfn in PERIOD_LABELS:
        m = pattern.match(t)
        if m:
            return keyfn(m)
    return None


def try_map(grid: list[list[str]]) -> list[dict] | None:
    """Returns contract lines, or None when the grid is not this shape."""
    n_cols = max(len(r) for r in grid)
    period_rows: dict[str, list[str]] = {}
    code_row: list[str] | None = None
    name_rows: list[list[str]] = []
    for row in grid:
        cells = [row[i].strip() if i < len(row) else "" for i in range(n_cols)]
        head = fold(cells[0])
        key = _period_key(cells[0])
        if key:
            period_rows.setdefault(key, cells)
        elif head == "cod":
            code_row = cells
        else:
            name_rows.append(cells)
    if code_row is None or len(period_rows) < 4:
        return None

    lines: list[dict] = []
    for j in range(1, n_cols):
        raw_code = code_row[j].strip() if j < len(code_row) else ""
        name = " ".join(r[j].strip() for r in name_rows if j < len(r) and r[j].strip())
        values: dict = {}
        cell_issues: list = []
        for key, cells in period_rows.items():
            text = cells[j] if j < len(cells) else ""
            if text:
                parse_cell(text, key, values, cell_issues)
        if not raw_code and not name and not values:
            continue
        lines.append(mk_line(raw_code or None, name, None, values, cell_issues, None))
    return lines
