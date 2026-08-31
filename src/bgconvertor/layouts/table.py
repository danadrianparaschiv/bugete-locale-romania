"""Generic header-driven budget table (the workhorse mapper).

Covers the simple annex tables (cod + total + estimari), economic detail
(with the 'din care credite' subcolumn), per-chapter tables with inline
SECTIUNEA rows, and per-institution budgets with numbered headings.
Always returns lines — it is the registry's last resort.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..parsing import NumberParseError, parse_ro_number
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

GENERIC_DETAIL = re.compile(
    r"^[-=• ]*(?:cheltuieli|bunuri si servicii|asistenta sociala|"
    r"sume aferente|salarii|dobanzi|proiecte cu finantare|rambursare)",
    re.IGNORECASE,
)
EDUCATION_CONTEXT = re.compile(
    r"\b(?:gradinita|gimnazi|scoala|lice|colegi|invatamant)\w*\b|"
    r"\b(?:ciufulici|helikon)\b",
    re.IGNORECASE,
)
NUMBERED_EDUCATION = re.compile(
    r"^\s*(\d+(?:[,.]\d+)*)\s*(?=(?:gradinita|scoala|lice|colegi))",
    re.IGNORECASE,
)
NUMBER_TOKEN = re.compile(r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{2})?")
VALUE_TOKEN = re.compile(rf"{NUMBER_TOKEN.pattern}|[xX]")


def _apply_hierarchy(
    lines: list[dict], context: dict | None
) -> tuple[str | None, str | None]:
    """Attach observed parent-row context to following detail rows."""
    institution = (context or {}).get("institution")
    subdocument = (context or {}).get("subdocument")
    for index, line in enumerate(lines):
        name = (line.get("name") or "").strip()
        following = lines[index + 1] if index + 1 < len(lines) else None
        next_name = (following or {}).get("name") or ""
        is_parent = bool(
            name
            and not GENERIC_DETAIL.match(name)
            and following
            and GENERIC_DETAIL.match(next_name)
        )
        if is_parent:
            raw_code = line.get("raw_code") or ""
            if (
                EDUCATION_CONTEXT.search(name)
                or re.match(r"^65(?:[. ]|$)", raw_code)
                or "s.p.c.t" in fold(name)
            ):
                printed_name = name
                numbered = NUMBERED_EDUCATION.match(name)
                if numbered and not raw_code:
                    raw_code = numbered.group(1)
                    line["raw_code"] = raw_code
                    line["name"] = name[numbered.end():].strip()
                institution, subdocument = printed_name, None
                line["institution"] = institution
            else:
                institution, subdocument = None, name
                line["subdocument"] = subdocument
            continue
        if institution:
            line["institution"] = institution
        if subdocument:
            line["subdocument"] = subdocument
    return institution, subdocument


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
        9: ["name", total, "trim1", "trim2", "trim3", "trim4", *estimates],
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
    if n_cols == 9:
        text_rows = sum(
            bool(row and len(row[0].strip()) >= 8) for row in rows
        )
        structured_code_rows = sum(
            bool(
                len(row) > 1
                and re.fullmatch(r"\d{2}(?:[./ ]\d{1,2}){1,4}", row[1].strip())
            )
            for row in rows
        )
        numeric_rows = sum(
            len([
                token
                for cell in row[1:]
                for token in VALUE_TOKEN.findall(cell)
            ]) >= 2
            for row in rows
        )
        if (
            text_rows >= len(rows) * 0.75
            and numeric_rows >= len(rows) * 0.35
            and structured_code_rows < len(rows) * 0.1
        ):
            return dict(enumerate(roles))
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

    # Some generators print dotted years (``2.025`` / ``2.026``) in the
    # second header row.  ``is_code_cell`` quite reasonably accepts those in
    # ordinary data, but here that used to terminate header detection after
    # row one and silently drop TOTAL plus two forecast columns.  The full
    # source geometry is unambiguous: name, code, annual total, four quarters,
    # three estimates.
    first_two = fold(" ".join(
        cell for row in grid[:2] for cell in row
    ))
    compact_second = [
        fold(cell).replace(".", "").replace(" ", "")
        for cell in (grid[1] if len(grid) > 1 else [])
    ]
    calarasi_quarter_header = (
        n_cols == 10
        and "prevederi trimestriale" in first_two
        and "estimari" in first_two
        and sum(value.startswith("trim") for value in compact_second) >= 4
        and {"2025", "2026", "2027"} <= set(compact_second)
    )
    if calarasi_quarter_header:
        year = budget_year or 2024
        columns = {
            0: "name", 1: "code", 2: f"total_{year}",
            3: "trim1", 4: "trim2", 5: "trim3", 6: "trim4",
            7: f"est{year + 1}", 8: f"est{year + 2}",
            9: f"est{year + 3}",
        }
        header_rows = [0, 1]
        first_data = 2
        rows_ = [row for row in grid[first_data:] if any(cell.strip() for cell in row)]

    # The same annual contract is sometimes emitted with a separate blank
    # spacer between the printed code and TOTAL columns.  Header text is
    # partly merged into the code cell, but the eleven-column geometry and
    # complete quarter/estimate band make every role explicit.
    eleven_column_annual = (
        n_cols == 11
        and "cod indicator" in first_two
        and "prevederi anuale" in first_two
        and "prevederi trimestriale" in first_two
        and "estimari" in first_two
    )
    if eleven_column_annual:
        year = budget_year or 2024
        columns = {
            0: "name", 1: "code", 2: f"total_{year}",
            3: "credite_restante", 4: "trim1", 5: "trim2",
            6: "trim3", 7: "trim4", 8: f"est{year + 1}",
            9: f"est{year + 2}", 10: f"est{year + 3}",
        }
        header_rows = [0, 1]
        first_data = 2
        rows_ = [row for row in grid[first_data:] if any(cell.strip() for cell in row)]

    # Some copier PDFs collapse the permanently-empty "credite restante"
    # body column while retaining its multi-row header.  The resulting
    # 10-column grid is: name, code, total, T1..T4, estimate years.  Header
    # text alone otherwise shifts T1 into ``credite_restante`` and can even
    # label T2 as a budget year.  The complete four-trim + three-estimate
    # shape is unambiguous and its row checksum remains independently
    # verifiable.
    full_header = fold(" ".join(
        cell for row in grid[:first_data] for cell in row
    ))
    compressed_quarter_grid = (
        n_cols == 10
        and len(header_rows) >= 2
        and "prevederi trimestriale" in full_header
        and "trim" in full_header
        and any(role.startswith("est") for role in columns.values())
        and columns.get(0) == "name"
        and columns.get(1) == "code"
    )
    if compressed_quarter_grid:
        printed_year = next((
            int(value) for value in compact_second
            if re.fullmatch(r"20\d{2}", value)
        ), None)
        year = budget_year or printed_year or 2026
        columns = {
            0: "name", 1: "code", 2: f"total_{year}",
            3: "trim1", 4: "trim2", 5: "trim3", 6: "trim4",
            7: f"est{year + 1}", 8: f"est{year + 2}", 9: f"est{year + 3}",
        }

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
        candidates = []
        for i in range(min(3, n_cols)):
            if columns.get(i):
                continue
            cells = [r[i].strip() for r in rows_ if len(r) > i and r[i].strip()]
            hits = sum(is_code_cell(cell) for cell in cells)
            structured = sum(
                bool(re.fullmatch(r"\d{1,4}(?:[./ ]\d{1,2}){1,4}", cell))
                for cell in cells
            )
            candidates.append((structured, hits, -i, i))
        if candidates:
            structured, hits, _, index = max(candidates)
            if rows_ and (
                structured >= max(2, len(rows_) * 0.08)
                or hits >= len(rows_) / 3
            ):
                columns[index] = "code"
    if "name" not in columns.values():
        columns[widest_text_col(grid, first_data, n_cols, columns)] = "name"
    # value-column rescue: unmapped majority-numeric columns get positional
    # roles (garbled headers like 'Tatal' or headerless continuation pages)
    value_roles = [r for r in columns.values() if r not in ("name", "code", "rowno")]
    if not value_roles:
        anchored_cols = None
        for row in rows_:
            observed = []
            for i in range(n_cols):
                if columns.get(i):
                    continue
                try:
                    value = parse_ro_number(row[i], ocr=True) if i < len(row) else None
                except NumberParseError:
                    continue
                if value not in (None, "X"):
                    observed.append((i, Decimal(value)))
            if (
                len(observed) == 8
                and observed[0][1] == sum(
                    (item[1] for item in observed[1:5]), Decimal(0)
                )
            ):
                anchored_cols = [item[0] for item in observed]
                break
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
        if anchored_cols is not None:
            numeric_cols = anchored_cols
        year = budget_year or 2026
        tail = [
            f"total_{year}", "trim1", "trim2", "trim3", "trim4",
            *(f"est{year + offset}" for offset in (1, 2, 3)),
        ] if len(numeric_cols) >= 5 else [
            f"total_{year}", *(f"est{year + offset}" for offset in (1, 2, 3))
        ]
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
    explicit_budget_header = bool(re.search(
        r"prevederi\s+(?:anuale|trimestriale)|cod\s+indicator|estimari",
        fold(" ".join(cell for row in grid[:3] for cell in row)),
    ))
    return {
        "family": context.get("family", "table") if inherited and context else "table",
        "n_cols": max(len(row) for row in grid),
        "columns": {str(index): role for index, role in columns.items()},
        "budget_year": budget_year or (context or {}).get("budget_year"),
        "budget_table": bool(
            explicit_budget_header or (context or {}).get("budget_table")
        ),
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
    columns, first_data, inherited = _infer_columns(
        grid, context=context, budget_year=budget_year
    )

    lines: list[dict] = []
    section = next((
        cell.strip()
        for row in grid[:first_data]
        for cell in row
        if "sectiun" in fold(cell)
    ), None)
    for row in grid[first_data:]:
        cells = {i: row[i].strip() if i < len(row) else "" for i in range(n_cols)}
        raw_code, name, values, cell_issues, row_no = None, [], {}, [], None
        func_ctx = None
        identity_columns = {
            index for index, role in columns.items()
            if role in {"name", "code", "func_code", "rowno", "ignore"}
        }
        observed_tokens = [
            match.group(0)
            for index, text in cells.items()
            if index not in identity_columns
            for match in NUMBER_TOKEN.finditer(text)
        ]
        expanded_values = None
        no_code_annual = (
            n_cols == 9
            and columns.get(0) == "name"
            and "code" not in columns.values()
            and set(columns.values()) == set(_positional_roles(9, budget_year) or [])
        )
        if no_code_annual:
            observed_tokens = [
                match.group(0)
                for index, text in cells.items()
                if index != 0
                for match in VALUE_TOKEN.finditer(text)
            ]
        if len(observed_tokens) == 8 or (no_code_annual and observed_tokens):
            roles = [
                f"total_{budget_year or 2026}",
                "trim1", "trim2", "trim3", "trim4",
                *(f"est{(budget_year or 2026) + offset}" for offset in (1, 2, 3)),
            ]
            expanded_values = {}
            expanded_issues = []
            for role, token in zip(roles, observed_tokens, strict=False):
                parse_cell(token, role, expanded_values, expanded_issues)
            if expanded_issues:
                expanded_values = None
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
                if expanded_values is not None:
                    continue
                if "sectiun" in fold(text):
                    continue  # row-span artifact: section text smeared into cells
                tokens = NUMBER_TOKEN.findall(text)
                previous = lines[-1] if lines else None
                if (
                    len(tokens) == 2
                    and previous is not None
                    and role not in (previous.get("values") or {})
                ):
                    prior_issues: list[dict] = []
                    parse_cell(tokens[0], role, previous["values"], prior_issues)
                    parse_cell(tokens[1], role, values, cell_issues)
                    if prior_issues:
                        previous.setdefault("cell_issues", []).extend(prior_issues)
                    continue
                parse_cell(text, role, values, cell_issues)
        if expanded_values is not None:
            values = expanded_values
        name_text = " ".join(name)

        # Printed column-index row, not a budget fact.
        if fold(raw_code or "") == "a" and fold(name_text) == "b":
            continue

        # SECTIUNEA rows sometimes carry the section totals as values
        # (ar-style chapter tables print them inline)
        marker = name_text if "sectiun" in fold(name_text) else (
            raw_code if raw_code and "sectiun" in fold(raw_code) else None
        )
        if marker:
            section = name_text or marker
            normalized_marker = fold(section)
            if raw_code is None and "veniturile sectiun" in normalized_marker:
                raw_code = "00.01 SD" if "dezvoltare" in normalized_marker else "00.01 SF"
            lines.append(mk_line(raw_code, section, section, values, cell_issues, row_no))
            continue

        # full-width context rows ("68020502 Asistenta sociala...", numbered
        # institution headings) set section for the rows that follow
        if not values and name_text and raw_code is None:
            if SECTION_ROW.match(fold(name_text)) or fold(name_text).startswith("sectiun"):
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
    institution, subdocument = _apply_hierarchy(
        lines, context if inherited else None
    )
    mapped_context = mapping_context(grid, context=context, budget_year=budget_year)
    if mapped_context is not None:
        mapped_context["institution"] = institution
        mapped_context["subdocument"] = subdocument
    return lines, mapped_context
