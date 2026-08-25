"""Layout registry: grid-mapping strategies tried in priority order.

Each strategy's try_map(grid) returns contract lines or None ("not my
shape"); the generic table mapper is the guaranteed last resort. Adding a
new layout family = one new module here + a registration line + fixtures.
"""

from __future__ import annotations

from . import (
    collapsed,
    collapsed_detail,
    expense_chapter,
    institution,
    investment,
    matrix,
    revenue_detail,
    table,
    transposed,
)

MAPPERS = [
    institution.try_map,
    collapsed.try_map,
    collapsed_detail.try_map,
    expense_chapter.try_map,
    investment.try_map,
    revenue_detail.try_map,
    transposed.try_map,
    matrix.try_map,
    table.map_grid,  # always succeeds
]


def map_grid(grid: list[list[str]]) -> list[dict]:
    if not grid or not grid[0]:
        return []
    for mapper in MAPPERS:
        lines = mapper(grid)
        if lines is not None:
            return lines
    return []
