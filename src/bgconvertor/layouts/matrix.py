"""Arad-style 'buget general' matrix: indicator rows + one sub-row per year.

Header pages carry full column names; continuation pages lose them to OCR,
so the printed column-index row ("A 0 1 2 ... 6=1+2+3+4+5 7 8=6-7") is the
fallback source of truth for column roles.
"""

from __future__ import annotations

import re

from .common import (
    YEAR_RE,
    column_semantics,
    is_code_cell,
    mk_line,
    parse_cell,
    split_header,
)

INDEX_ROLES = {
    "A": "name", "0": "rowno", "1": "bugetul_local",
    "2": "inst_venituri_proprii_subventii", "3": "inst_integral_venituri_proprii",
    "4": "imprumuturi", "5": "fonduri_externe", "7": "transferuri",
}

MERGED_YEAR_HEADING = re.compile(r"^((?:19|20)\d{2})\s+(.+\S)$")


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


def _cells(row: list[str], n_cols: int) -> dict[int, str]:
    return {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}


def _field(cells: dict[int, str], columns: dict[int, str], role: str) -> str:
    return next(
        (cells[i] for i, mapped_role in columns.items() if mapped_role == role and cells[i]),
        "",
    )


def _parse_values(
    cells: dict[int, str], columns: dict[int, str]
) -> tuple[dict, list]:
    values: dict = {}
    cell_issues: list = []
    for i, value in cells.items():
        role = columns.get(i)
        if value and role not in (None, "name", "rowno", "code"):
            parse_cell(value, role, values, cell_issues)
    return values, cell_issues


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
    rows = grid[first_data:]
    row_index = 0
    while row_index < len(rows):
        cells = _cells(rows[row_index], n_cols)
        name_text = _field(cells, columns, "name")
        rowno_text = _field(cells, columns, "rowno")

        # The printed column-index row is structural, not an indicator. Header
        # detection sometimes leaves it as the first data row.
        if name_text == "A" and rowno_text == "0":
            row_index += 1
            continue

        year = int(name_text) if YEAR_RE.match(name_text) else None

        # TableFormer can merge the final year row of one indicator with the
        # heading immediately below it, while placing the displaced last value
        # on that heading's otherwise empty row. Split only when the observable
        # two-row structure is complete: "2029 <heading>", then a code-only
        # row carrying one or more numeric fragments. This recovers both rows
        # without inventing a value or relying on a city/page identifier.
        merged = MERGED_YEAR_HEADING.match(name_text)
        if merged and pending_name and row_index + 1 < len(rows):
            following = _cells(rows[row_index + 1], n_cols)
            following_name = _field(following, columns, "name")
            following_code = _field(following, columns, "rowno")
            current_values, current_issues = _parse_values(cells, columns)
            following_values, following_issues = _parse_values(following, columns)
            displaced_roles = set(following_values) - set(current_values)
            if (
                not following_name
                and is_code_cell(following_code)
                and current_values
                and following_values
                and displaced_roles == set(following_values)
                and not current_issues
                and not following_issues
            ):
                current_values.update(following_values)
                line = mk_line(
                    pending_rowcode, pending_name, None, current_values, [], None
                )
                line["year"] = int(merged.group(1))
                lines.append(line)

                pending_name = merged.group(2)
                pending_rowcode = following_code
                lines.append(mk_line(pending_rowcode, pending_name, None, {}, [], None))
                row_index += 2
                continue

        if year is None:
            if name_text:
                pending_name, pending_rowcode = name_text, rowno_text or None
                lines.append(mk_line(pending_rowcode, name_text, None, {}, [], None))
            row_index += 1
            continue

        values, cell_issues = _parse_values(cells, columns)
        line = mk_line(pending_rowcode, pending_name or "?", None, values, cell_issues, None)
        line["year"] = year
        lines.append(line)
        row_index += 1
    return lines
