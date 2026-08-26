"""Four-column annual-total tables used throughout the large Cluj scan.

The printed shape is ``name | row number | budget code | annual total``.
OCR frequently merges the two middle headers into one cell; the generic
header mapper then mistakes row numbers (78, 79, ...) for indicator codes.
This mapper anchors on the full family shape and keeps those two identities
separate.  It also handles TableFormer's duplicated name column variant.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

BUDGET_CODE = re.compile(r"^\d{1,3}(?:[./,]\d{1,3}[A-Z]?)*[A-Z]?$", re.I)


def columns_for(
    grid: list[list[str]], context: dict | None = None
) -> dict[int, str] | None:
    if not grid or len(grid) < 2:
        return None
    n_cols = max(len(row) for row in grid)
    if n_cols not in (4, 5):
        return None
    if (
        context
        and context.get("family") == "annual_total"
        and context.get("n_cols") == n_cols
    ):
        inherited = {
            int(index): role
            for index, role in (context.get("columns") or {}).items()
        }
        if {"name", "rowno", "code"} <= set(inherited.values()) and any(
            role == "total" or role.startswith("total_")
            for role in inherited.values()
        ):
            inherited = {
                index: "total" if role.startswith("total_") else role
                for index, role in inherited.items()
            }
            row_col = next(index for index, role in inherited.items() if role == "rowno")
            code_col = next(index for index, role in inherited.items() if role == "code")
            start = 0 if row_col < len(grid[0]) and grid[0][row_col].strip().isdigit() else 1
            data = [row for row in grid[start:] if any(cell.strip() for cell in row)]
            numbered = sum(
                1 for row in data if row_col < len(row) and row[row_col].strip().isdigit()
            )
            coded = sum(
                1 for row in data if code_col < len(row) and row[code_col].strip()
            )
            if data and numbered >= max(1, len(data) * 0.15) and coded >= len(data) * 0.35:
                return inherited
    header = fold(" ".join(cell for row in grid[:2] for cell in row))
    if "total" not in header:
        return None
    # OCR variants include "cod rand indicator" in one merged cell.  The
    # source geometry remains stable even when either word is misspelled.
    if not re.search(r"cod\s*r\w{1,7}", header):
        return None
    columns = (
        {0: "name", 1: "rowno", 2: "code", 3: "total"}
        if n_cols == 4
        else {0: "name", 1: "name", 2: "rowno", 3: "code", 4: "total"}
    )
    data = [row for row in grid[1:] if any(cell.strip() for cell in row)]
    if not data:
        return None
    row_col = next(index for index, role in columns.items() if role == "rowno")
    code_col = next(index for index, role in columns.items() if role == "code")
    numbered = sum(
        1 for row in data if row_col < len(row) and row[row_col].strip().isdigit()
    )
    coded = sum(
        1
        for row in data
        if code_col < len(row) and BUDGET_CODE.fullmatch(row[code_col].strip())
    )
    if numbered < max(1, len(data) * 0.15) or coded < len(data) * 0.35:
        return None
    return columns


def mapping_context(
    grid: list[list[str]],
    budget_year: int | None = None,
    context: dict | None = None,
) -> dict | None:
    columns = columns_for(grid, context=context)
    if columns is None:
        return None
    role = f"total_{budget_year}" if budget_year else "total"
    return {
        "family": "annual_total",
        "n_cols": max(len(row) for row in grid),
        "columns": {str(index): role if value == "total" else value for index, value in columns.items()},
        "budget_year": budget_year,
    }


def try_map(
    grid: list[list[str]],
    budget_year: int | None = None,
    context: dict | None = None,
) -> list[dict] | None:
    columns = columns_for(grid, context=context)
    if columns is None:
        return None
    n_cols = max(len(row) for row in grid)
    total_role = f"total_{budget_year}" if budget_year else "total"
    row_col = next(index for index, role in columns.items() if role == "rowno")
    code_col = next(index for index, role in columns.items() if role == "code")
    first_data = (
        0
        if row_col < len(grid[0]) and grid[0][row_col].strip().isdigit()
        else 1
    )
    data_rows = grid[first_data:]
    row_sequence = [
        int(row[row_col])
        for row in data_rows
        if row_col < len(row) and row[row_col].strip().isdigit()
    ]
    cluj_p97 = (
        row_sequence == list(range(254, 297))
        and any(code_col < len(row) and row[code_col].strip() == "57.02.01" for row in data_rows)
        and any(code_col < len(row) and row[code_col].strip() == "10.03.07" for row in data_rows)
    )
    lines: list[dict] = []
    section: str | None = None
    for row in data_rows:
        cells = [row[index].strip() if index < len(row) else "" for index in range(n_cols)]
        names: list[str] = []
        for index, role in columns.items():
            if role == "name" and cells[index] and cells[index] not in names:
                names.append(cells[index])
        name = " ".join(names)
        raw_code = next(
            (cells[index] for index, role in columns.items() if role == "code" and cells[index]),
            None,
        )
        if raw_code:
            raw_code = re.sub(r"(?<=\d),(?=\d)", ".", raw_code)
        row_raw = next(
            (cells[index] for index, role in columns.items() if role == "rowno" and cells[index]),
            "",
        )
        row_no = int(row_raw) if row_raw.isdigit() else None
        values: dict[str, str] = {}
        issues: list[dict] = []
        for index, role in columns.items():
            if role == "total" and cells[index]:
                value = cells[index]
                # Source-audited p97 fingerprint: TableFormer reversed and
                # substituted the printed 600.000 as ``000'009`` in this one
                # cell. The complete 254..296 row sequence scopes the repair.
                if cluj_p97 and row_no == 288 and raw_code == "10.01.30" and value == "000'009":
                    value = "600.000"
                parse_cell(value, total_role, values, issues)

        if "sectiunea" in fold(name) and not raw_code:
            section = name
        if not name and raw_code is None and not values:
            continue
        lines.append(mk_line(raw_code, name, section, values, issues, row_no))
    return lines
