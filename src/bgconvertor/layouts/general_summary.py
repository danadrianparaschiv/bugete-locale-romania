"""Consolidated one-row-per-indicator budget summary (Cluj-style).

Unlike the Arad matrix, these rows do not repeat once per forecast year.
The second printed row provides formulas ``5=1+2+3+4`` and ``7=5-6``;
those formulas make the nine-column schema deterministic even when OCR
damages the long institutional-budget headers.
"""

from __future__ import annotations

from .common import fold, mk_line, parse_cell

ROLES = {
    0: "name",
    1: "rowno",
    2: "buget_local",
    3: "inst_venituri_proprii_subventii",
    4: "credite_interne",
    5: "fonduri_externe",
    6: "total",
    7: "transferuri",
    8: "total_general",
}


def _first_data(grid: list[list[str]]) -> int | None:
    if not grid or max(len(row) for row in grid) != 9:
        return None
    header = fold(" ".join(grid[0]))
    if not all(marker in header for marker in ("buget", "transferuri", "total")):
        return None
    for index, row in enumerate(grid[1:3], start=1):
        cells = [cell.replace(" ", "") for cell in row[:9]]
        if len(cells) == 9 and cells[:6] == ["A", "0", "1", "2", "3", "4"]:
            if cells[6].startswith(("5=", "5-")) and cells[8].startswith(("7=", "7-")):
                return index + 1
    return None


def try_map(grid: list[list[str]]) -> list[dict] | None:
    first_data = _first_data(grid)
    if first_data is None:
        return None
    lines: list[dict] = []
    for row in grid[first_data:]:
        cells = [row[index].strip() if index < len(row) else "" for index in range(9)]
        name = cells[0]
        row_no = int(cells[1]) if cells[1].isdigit() else None
        values: dict[str, str] = {}
        issues: list[dict] = []
        for index in range(2, 9):
            if cells[index]:
                parse_cell(cells[index], ROLES[index], values, issues)
        if not name and not values:
            continue
        lines.append(mk_line(None, name, None, values, issues, row_no))
    return lines
