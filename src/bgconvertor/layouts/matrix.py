"""Arad-style 'buget general' matrix: indicator rows + one sub-row per year.

Header pages carry full column names; continuation pages lose them to OCR,
so the printed column-index row ("A 0 1 2 ... 6=1+2+3+4+5 7 8=6-7") is the
fallback source of truth for column roles.
"""

from __future__ import annotations

from .common import YEAR_RE, column_semantics, mk_line, parse_cell, split_header

INDEX_ROLES = {
    "A": "name", "0": "rowno", "1": "bugetul_local",
    "2": "inst_venituri_proprii_subventii", "3": "inst_integral_venituri_proprii",
    "4": "imprumuturi", "5": "fonduri_externe", "7": "transferuri",
}


def _index_columns(grid) -> dict[int, str] | None:
    for row in grid[:4]:
        cells = [c.strip() for c in row]
        if not any(c.startswith("6=") or c.startswith("8=") for c in cells):
            continue
        columns: dict[int, str] = {}
        for i, c in enumerate(cells):
            token = c.split()[0] if c else ""
            if token.startswith("6="):
                columns[i] = "total"
            elif token.startswith("8="):
                columns[i] = "total_general"
            elif token in INDEX_ROLES:
                columns[i] = INDEX_ROLES[token]
        if "total_general" in columns.values() and "bugetul_local" in columns.values():
            return columns
    return None


def try_map(grid: list[list[str]]) -> list[dict] | None:
    n_cols = max(len(r) for r in grid)
    header_rows, first_data = split_header(grid)
    header_cols = column_semantics(grid, header_rows, n_cols)

    idx = _index_columns(grid)
    if idx is not None:
        columns = dict(idx)
        columns.update({i: r for i, r in header_cols.items() if r in ("name", "rowno")})
    elif "total_general" in header_cols.values():
        columns = header_cols
    else:
        return None

    lines: list[dict] = []
    pending_name: str | None = None
    pending_rowcode: str | None = None
    for row in grid[first_data:]:
        cells = {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}
        name_text = " ".join(
            cells[i] for i, role in columns.items() if role == "name" and cells.get(i)
        )
        rowno_text = next(
            (cells[i] for i, role in columns.items() if role == "rowno" and cells.get(i)), ""
        )
        year = int(name_text) if YEAR_RE.match(name_text) else None

        if year is None:
            if name_text:
                pending_name, pending_rowcode = name_text, rowno_text or None
                lines.append(mk_line(pending_rowcode, name_text, None, {}, [], None))
            continue

        values: dict = {}
        cell_issues: list = []
        for i, text in cells.items():
            role = columns.get(i)
            if text and role not in (None, "name", "rowno", "code"):
                parse_cell(text, role, values, cell_issues)
        line = mk_line(pending_rowcode, pending_name or "?", None, values, cell_issues, None)
        line["year"] = year
        lines.append(line)
    return lines
