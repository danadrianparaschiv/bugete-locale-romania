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
POSITIONAL_ROLES = {
    4: ["code", "name", "total_2026", "credite_restante"],
    5: ["code", "name", "total_2026", "est2027", "est2028"],
    6: ["code", "name", "total_2026", "est2027", "est2028", "est2029"],
}


def _positional_columns(grid, n_cols: int) -> dict[int, str] | None:
    roles = POSITIONAL_ROLES.get(n_cols)
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
    if codeish >= 0.5 * len(rows) and numericish >= 0.35 * len(rows):
        return dict(enumerate(roles))
    return None


def map_grid(grid: list[list[str]]) -> list[dict]:
    n_cols = max(len(r) for r in grid)
    header_rows, first_data = split_header(grid)
    columns = column_semantics(grid, header_rows, n_cols)
    if not header_rows and len([r for r in columns.values() if r not in ("name",)]) < 2:
        positional = _positional_columns(grid, n_cols)
        if positional:
            columns = positional
    if "name" not in columns.values():
        columns[widest_text_col(grid, first_data, n_cols, columns)] = "name"
    # the "din care credite..." subcolumn often loses its header to OCR:
    # an unmapped column immediately right of buget_2026 takes that role
    for i, role in list(columns.items()):
        if role == "buget_2026" and (i + 1) not in columns and (i + 1) < n_cols:
            if "credite_restante" not in columns.values():
                columns[i + 1] = "credite_restante"

    lines: list[dict] = []
    section: str | None = None
    for row in grid[first_data:]:
        cells = {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}
        raw_code, name, values, cell_issues, row_no = None, [], {}, [], None
        for i, text in cells.items():
            role = columns.get(i)
            if not text:
                continue
            if role == "code":
                raw_code = text
            elif role == "name":
                name.append(text)
            elif role == "rowno":
                row_no = int(text) if text.isdigit() else None
            elif role and role not in ("ignore",):
                if "sectiun" in fold(text):
                    continue  # row-span artifact: section text smeared into cells
                parse_cell(text, role, values, cell_issues)
        name_text = " ".join(name)

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

        lines.append(mk_line(raw_code, name_text, section, values, cell_issues, row_no))
    return lines
