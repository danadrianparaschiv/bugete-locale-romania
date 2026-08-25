"""Rectification annex: Denumire | cod | Buget 2026 | Influente | Buget rectificat.

Detail pages of a rectification HCL (Baia Mare). The vendor titles the code
column "COD RAND" but prints real indicator codes in it (00.01, 48.02,
57.02.01), so the generic table mapper files it as row numbers and loses
the codes. Detection anchors on the "Influente" column header — no other
corpus family has one. The rectified budget is the operative 2026 value
(total_2026); the pre-rectification column keeps the buget_2026 role.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

# TableFormer often merges the split "Buget / rectificat" header into the
# first data cell of the column: "rectificat 939.00"
RECT_PREFIX = re.compile(r"^\s*rectificat\s*", re.I)


def _header_columns(grid) -> tuple[dict[int, str], int] | None:
    for row_idx, row in enumerate(grid[:3]):
        folded = [fold(c) for c in row]
        columns: dict[int, str] = {}
        for i, cell in enumerate(folded):
            if not cell.strip():
                continue
            if "denumire" in cell and "name" not in columns.values():
                columns[i] = "name"
            elif "influente" in cell and "influente" not in columns.values():
                columns[i] = "influente"
            elif "cod" in cell and "code" not in columns.values():
                columns[i] = "code"
            elif "buget" in cell and "buget_2026" not in columns.values():
                columns[i] = "buget_2026"
        if {"name", "code", "buget_2026", "influente"} <= set(columns.values()):
            infl = next(i for i, r in columns.items() if r == "influente")
            columns[infl + 1] = "total_2026"  # "Buget rectificat", right of Influente
            return columns, row_idx
    return None


def try_map(grid: list[list[str]]) -> list[dict] | None:
    found = _header_columns(grid)
    if found is None:
        return None
    columns, hdr_row = found
    n_cols = max(len(r) for r in grid)

    lines: list[dict] = []
    section: str | None = None
    pending_name: str | None = None
    for row in grid[hdr_row + 1:]:
        cells = {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}
        name = " ".join(
            cells[i] for i, role in columns.items() if role == "name" and cells.get(i)
        )
        raw_code = next(
            (cells[i] for i, role in columns.items() if role == "code" and cells.get(i)), ""
        )
        values: dict = {}
        cell_issues: list = []
        for i, text in cells.items():
            role = columns.get(i)
            if not text or role in (None, "name", "code"):
                continue
            if role == "total_2026":
                text = RECT_PREFIX.sub("", text)
                if not text:
                    continue
            parse_cell(text, role, values, cell_issues)

        if "sectiunea" in fold(name) and not values:
            section = name
            lines.append(mk_line(None, name, section, {}, [], None))
            continue
        if not raw_code and not values:
            if name:
                # names print on their own row ABOVE the code+values row
                pending_name = name if pending_name is None else f"{pending_name} {name}"
            continue
        if pending_name:
            name = f"{pending_name} {name}".strip()
            pending_name = None
        lines.append(mk_line(raw_code or None, name, section, values, cell_issues, None))
    return lines
