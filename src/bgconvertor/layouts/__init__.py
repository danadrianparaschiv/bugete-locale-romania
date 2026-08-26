"""Layout registry: grid-mapping strategies tried in priority order.

Each strategy's try_map(grid) returns contract lines or None ("not my
shape"); the generic table mapper is the guaranteed last resort. Adding a
new layout family = one new module here + a registration line + fixtures.
"""

from __future__ import annotations

from . import (
    annual_total,
    collapsed,
    collapsed_detail,
    expense_chapter,
    formular11,
    general_summary,
    institution,
    investment,
    matrix,
    rectificare,
    revenue_detail,
    table,
    transposed,
)

MAPPERS = [
    annual_total.try_map,
    institution.try_map,
    collapsed.try_map,
    collapsed_detail.try_map,
    expense_chapter.try_map,
    investment.try_map,
    revenue_detail.try_map,
    formular11.try_map,
    rectificare.try_map,
    transposed.try_map,
    general_summary.try_map,
    matrix.try_map,
    table.map_grid,  # always succeeds
]


def map_grid(
    grid: list[list[str]],
    context: dict | None = None,
    budget_year: int | None = None,
) -> list[dict]:
    lines, _ = map_grid_with_context(grid, context=context, budget_year=budget_year)
    return lines


def map_grid_with_context(
    grid: list[list[str]],
    context: dict | None = None,
    budget_year: int | None = None,
) -> tuple[list[dict], dict | None]:
    if not grid or not grid[0]:
        return [], context
    for mapper in MAPPERS:
        if mapper is annual_total.try_map:
            lines = mapper(grid, budget_year=budget_year, context=context)
        elif mapper is transposed.try_map:
            lines = mapper(grid, budget_year=budget_year)
        elif mapper is table.map_grid:
            return table.map_grid_with_context(
                grid, context=context, budget_year=budget_year
            )
        else:
            lines = mapper(grid)
        if lines is not None:
            mapped_context = (
                annual_total.mapping_context(grid, budget_year, context=context)
                if mapper is annual_total.try_map
                else table.mapping_context(grid, budget_year=budget_year)
            )
            return lines, mapped_context or context
    return [], context
