"""Generic header-driven budget table (the workhorse mapper).

Covers the simple annex tables (cod + total + estimari), economic detail
(with the 'din care credite' subcolumn), per-chapter tables with inline
SECTIUNEA rows, and per-institution budgets with numbered headings.
Always returns lines — it is the registry's last resort.
"""

from __future__ import annotations

import re

from .common import (
    SECTION_ROW,
    column_semantics,
    fold,
    is_code_cell,
    mk_line,
    parse_cell,
    split_header,
    widest_text_col,
)


# continuation pages print no header: when the first column is code-like and
# the trailing columns are numeric, assume the dominant annex shape
def _positional_roles(n_cols: int, budget_year: int | None) -> list[str] | None:
    year = budget_year or 2026
    total = f"total_{year}"
    estimates = [f"est{year + offset}" for offset in (1, 2, 3)]
    return {
        4: ["code", "name", total, "credite_restante"],
        5: ["code", "name", total, *estimates[:2]],
        6: ["code", "name", total, *estimates],
        7: ["code", "name", total, "trim1", "trim2", "trim3", "trim4"],
        10: [
            "code", "name", total, "trim1", "trim2", "trim3", "trim4", *estimates
        ],
    }.get(n_cols)


def _positional_columns(
    grid, n_cols: int, budget_year: int | None = None
) -> dict[int, str] | None:
    roles = _positional_roles(n_cols, budget_year)
    if roles is None:
        return None
    rows = [r for r in grid if any(c.strip() for c in r)]
    if len(rows) < 6:
        return None
    # some vendors print name first, code second (Braila's institution pages)
    code0 = sum(1 for r in rows if len(r) > 0 and is_code_cell(r[0]))
    code1 = sum(1 for r in rows if len(r) > 1 and is_code_cell(r[1]))
    if code1 > code0:
        roles = ["name", "code", *roles[2:]]
    codeish = max(code0, code1)
    numericish = sum(
        1 for r in rows
        if sum(
            1 for c in r[2:n_cols]
            if c.strip() and (any(ch.isdigit() for ch in c) or fold(c).strip() == "x")
        ) >= 2
    )
    # wrapped-name and section rows legitimately have empty numeric cells,
    # so the numeric bar is lower when the code-column signal is strong
    if codeish >= len(rows) / 3 and numericish >= 0.35 * len(rows):
        return dict(enumerate(roles))
    return None


def _infer_columns(
    grid: list[list[str]],
    context: dict | None = None,
    budget_year: int | None = None,
) -> tuple[dict[int, str], int, bool]:
    n_cols = max(len(r) for r in grid)
    header_rows, first_data = split_header(grid)
    columns = column_semantics(grid, header_rows, n_cols)
    rows_ = [row for row in grid[first_data:] if any(cell.strip() for cell in row)]

    # Some vendors merge ``Cod`` and ``Denumire indicator`` in the second
    # header cell while data still occupy separate columns 0/1. Header-only
    # semantics then swaps every code and name. Recover the geometry only
    # when the unassigned left neighbour is code-like on at least one third
    # of data rows.
    for index, role in list(columns.items()):
        joined = fold(
            " ".join(grid[row][index] for row in header_rows if index < len(grid[row]))
        )
        if role != "code" or not all(word in joined for word in ("cod", "indicator")):
            continue
        left = index - 1
        if left < 0 or columns.get(left):
            continue
        hits = sum(
            1 for row in rows_ if left < len(row) and is_code_cell(row[left])
        )
        if rows_ and hits >= len(rows_) / 3:
            columns[left] = "code"
            columns[index] = "name"
            break
    inherited = False
    if not header_rows and context and context.get("n_cols") == n_cols:
        prior = {int(index): role for index, role in (context.get("columns") or {}).items()}
        if "name" in prior.values() and any(
            role not in ("name", "code", "func_code", "rowno", "ignore")
            for role in prior.values()
        ):
            columns = prior
            inherited = True
    if not header_rows and len([r for r in columns.values() if r not in ("name",)]) < 2:
        positional = _positional_columns(grid, n_cols, budget_year)
        if positional:
            columns = positional
    if "code" not in columns.values():
        # a column of code-like cells whose header OCR lost (or that sits
        # past the columns positional detection checks) is the code column
        for i in range(min(3, n_cols)):
            if columns.get(i):
                continue
            hits = sum(1 for r in rows_ if len(r) > i and is_code_cell(r[i]))
            if rows_ and hits >= len(rows_) / 3:
                columns[i] = "code"
                break
    if "name" not in columns.values():
        columns[widest_text_col(grid, first_data, n_cols, columns)] = "name"
    # value-column rescue: unmapped majority-numeric columns get positional
    # roles (garbled headers like 'Tatal' or headerless continuation pages)
    value_roles = [r for r in columns.values() if r not in ("name", "code", "rowno")]
    if not value_roles:
        numeric_cols = []
        for i in range(n_cols):
            if columns.get(i):
                continue
            hits = sum(
                1 for r in rows_
                if len(r) > i and r[i].strip()
                and (any(ch.isdigit() for ch in r[i]) or fold(r[i]).strip() == "x")
            )
            if rows_ and hits >= len(rows_) / 3:
                numeric_cols.append(i)
        year = budget_year or 2026
        tail = (
            [f"total_{year}", "trim1", "trim2", "trim3", "trim4"]
            if len(numeric_cols) >= 5
            else [f"total_{year}", *(f"est{year + offset}" for offset in (1, 2, 3))]
        )
        for i, role in zip(numeric_cols, tail, strict=False):
            columns[i] = role
    # the "din care credite..." subcolumn often loses its header to OCR:
    # an unmapped column immediately right of the current budget takes that role
    for i, role in list(columns.items()):
        if role.startswith("buget_") and (i + 1) not in columns and (i + 1) < n_cols:
            if "credite_restante" not in columns.values():
                columns[i + 1] = "credite_restante"
    return columns, first_data, inherited


def mapping_context(
    grid: list[list[str]],
    context: dict | None = None,
    budget_year: int | None = None,
) -> dict | None:
    if not grid:
        return context
    columns, _, inherited = _infer_columns(grid, context=context, budget_year=budget_year)
    if "name" not in columns.values():
        return context
    if not any(
        role not in ("name", "code", "func_code", "rowno", "ignore")
        for role in columns.values()
    ):
        return context
    return {
        "family": context.get("family", "table") if inherited and context else "table",
        "n_cols": max(len(row) for row in grid),
        "columns": {str(index): role for index, role in columns.items()},
        "budget_year": budget_year or (context or {}).get("budget_year"),
    }


def map_grid(grid: list[list[str]]) -> list[dict]:
    lines, _ = map_grid_with_context(grid)
    return lines


def map_grid_with_context(
    grid: list[list[str]],
    context: dict | None = None,
    budget_year: int | None = None,
) -> tuple[list[dict], dict | None]:
    n_cols = max(len(r) for r in grid)
    columns, first_data, _ = _infer_columns(
        grid, context=context, budget_year=budget_year
    )

    lines: list[dict] = []
    section: str | None = None
    for row in grid[first_data:]:
        cells = {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}
        raw_code, name, values, cell_issues, row_no = None, [], {}, [], None
        func_ctx = None
        for i, text in cells.items():
            role = columns.get(i)
            if not text:
                continue
            if role == "code":
                raw_code = text
            elif role == "func_code":
                func_ctx = text.replace(" ", "")
            elif role == "name":
                if not name or name[-1] != text:  # docling row-span dup
                    name.append(text)
            elif role == "rowno":
                row_no = int(text) if text.isdigit() else None
            elif role and role not in ("ignore",):
                if "sectiun" in fold(text):
                    continue  # row-span artifact: section text smeared into cells
                parse_cell(text, role, values, cell_issues)
        name_text = " ".join(name)

        # Printed column-index row, not a budget fact.
        if fold(raw_code or "") == "a" and fold(name_text) == "b":
            continue

        # SECTIUNEA rows sometimes carry the section totals as values
        # (ar-style chapter tables print them inline)
        marker = name_text if "sectiunea" in fold(name_text) else (
            raw_code if raw_code and "sectiunea" in fold(raw_code) else None
        )
        if marker:
            section = name_text or marker
            lines.append(mk_line(None, section, section, values, cell_issues, row_no))
            continue

        # full-width context rows ("68020502 Asistenta sociala...", numbered
        # institution headings) set section for the rows that follow
        if not values and name_text and raw_code is None:
            if SECTION_ROW.match(fold(name_text)) or fold(name_text).startswith("sectiunea"):
                section = name_text
                lines.append(mk_line(None, name_text, section, {}, [], row_no))
                continue
        if raw_code is None and not name_text and not values:
            continue
        if raw_code is None and name_text and not values and lines:
            last = lines[-1]
            if last["values"] or last["raw_code"]:
                last["name"] = (last["name"] + " " + name_text).strip()
                continue

        # embedded section header where the code cell holds a long code and
        # there are no values (e.g. "68020502 | Asistenta sociala in caz de")
        if raw_code and not values and len(re.sub(r"\D", "", raw_code)) >= 6 and name_text:
            section = f"{raw_code} {name_text}"

        if raw_code is None and func_ctx:
            # functional subtotal row in a dual-code grid: the economic cell
            # is empty and the functional code IS the line code
            raw_code, func_ctx = func_ctx, None
        lines.append(mk_line(raw_code, name_text, section, values, cell_issues,
                             row_no, func_ctx=func_ctx))
    return lines, mapping_context(grid, context=context, budget_year=budget_year)
