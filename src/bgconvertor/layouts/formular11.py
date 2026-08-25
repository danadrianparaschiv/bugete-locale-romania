"""MF 'Formular 11' general-budget matrix: one row per indicator, values inline.

Born-digital pages printed without ruling lines (Baia Mare): the digital
extractor finds no grid, so docling recovers the table and the printed
column-index row "A 0 1 2 3 4 5 6 7=1+2+3+4+5+6 8 9=7-8" is the source of
truth for column roles — the worded headers wrap across several grid rows
and OCR splits them unpredictably. Unlike the Arad matrix (matrix.py,
"6=1+2+3+4+5"/"8=6-7" with per-year sub-rows), each indicator row carries
its values directly and the loan columns split into externe/interne.
"""

from __future__ import annotations

from .common import fold, mk_line, parse_cell, split_header

INDEX_ROLES = {
    "A": "name", "0": "rowno", "1": "bugetul_local",
    "2": "inst_venituri_proprii_subventii", "3": "inst_integral_venituri_proprii",
    "4": "imprumuturi_externe", "5": "imprumuturi_interne", "6": "fonduri_externe",
    "8": "transferuri",
}

# fixed Formular 11 column order, printed right of the "Cod rand" column
VALUE_ROLES = [
    "bugetul_local", "inst_venituri_proprii_subventii",
    "inst_integral_venituri_proprii", "imprumuturi_externe",
    "imprumuturi_interne", "fonduri_externe", "total",
    "transferuri", "total_general",
]


def _index_columns(grid) -> tuple[dict[int, str], int] | None:
    for row_idx, row in enumerate(grid[:5]):
        tokens = [c.strip().split()[0] if c.strip() else "" for c in row]
        if not any(t.startswith("7=") for t in tokens):
            continue
        columns: dict[int, str] = {}
        for i, tok in enumerate(tokens):
            if tok.startswith("7="):
                columns[i] = "total"
            elif tok.startswith("9="):
                columns[i] = "total_general"
            elif tok in INDEX_ROLES:
                columns[i] = INDEX_ROLES[tok]
        if {"total", "total_general", "bugetul_local"} <= set(columns.values()):
            return columns, row_idx + 1  # data starts below the index row
    return None


def _header_columns(grid) -> tuple[dict[int, str], int] | None:
    """Continuation pages repeat the worded headers but not the index row."""
    header_rows, first_data = split_header(grid)
    if not header_rows:
        return None
    n_cols = max(len(r) for r in grid)
    joined = fold(" ".join(" ".join(grid[r]) for r in header_rows))
    if "cod rand" not in joined or "total buget general" not in joined:
        return None
    # the externe/interne loan-column split is the Formular 11 signature
    # that the Arad matrix (single 'imprumuturi' column) lacks
    if "externe" not in joined or "interne" not in joined:
        return None
    rowno_col = next(
        (
            i for i in range(n_cols)
            if "cod rand" in fold(" ".join(
                grid[r][i] for r in header_rows if i < len(grid[r])
            ))
        ),
        None,
    )
    if not rowno_col or n_cols - rowno_col - 1 != len(VALUE_ROLES):
        return None
    columns = {rowno_col - 1: "name", rowno_col: "rowno"}
    columns.update({rowno_col + 1 + k: role for k, role in enumerate(VALUE_ROLES)})
    return columns, first_data


def try_map(grid: list[list[str]]) -> list[dict] | None:
    found = _index_columns(grid) or _header_columns(grid)
    if found is None:
        return None
    columns, first_data = found
    n_cols = max(len(r) for r in grid)

    lines: list[dict] = []
    for row in grid[first_data:]:
        cells = {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}
        name = " ".join(
            cells[i] for i, role in columns.items() if role == "name" and cells.get(i)
        )
        rowno = next(
            (cells[i] for i, role in columns.items() if role == "rowno" and cells.get(i)), ""
        )
        values: dict = {}
        cell_issues: list = []
        for i, text in cells.items():
            role = columns.get(i)
            if text and role not in (None, "name", "rowno"):
                parse_cell(text, role, values, cell_issues)

        if not name and not rowno and not values:
            continue
        if name and not rowno and not values:
            # wrapped continuation of the previous indicator's name
            if lines:
                lines[-1]["name"] = (lines[-1]["name"] + " " + name).strip()
            else:
                lines.append(mk_line(None, name, None, {}, [], None))
            continue
        row_no = int(rowno) if rowno.isdigit() else None
        lines.append(mk_line(rowno or None, name, None, values, cell_issues, row_no))
    return lines
