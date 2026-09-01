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
    r"sume aferente|salari\w*|dobanzi|proiecte cu finantare|rambursare|"
    r"plata\b|titlul\b|burse\b)",
    re.IGNORECASE,
)
PROJECT_ITEM = re.compile(
    r"^[\"'• -]*(?:achizition|amenajar|asigurar|construir|dezvoltar|"
    r"documentati|executi|extinder|imbunatatir|infiintar|inlocuir|lucrari|"
    r"modernizar|promovar|reabilitar|reducer|reparati)",
    re.IGNORECASE,
)
EDUCATION_CONTEXT = re.compile(
    r"(?:^|[^a-z])(?:gradinita|gimnazi|scoala|lice|colegi|invatamant)\w*\b|"
    r"\b(?:ciufulici|helikon)\b",
    re.IGNORECASE,
)
EXTRABUDGET_TOTAL = re.compile(
    r"^total\s+(?:venituri|cheltuieli)\s+extrabugetare\b",
    re.IGNORECASE,
)
NUMBERED_EDUCATION = re.compile(
    r"^\s*(\d+(?:[,.]\d+)*)\s*(?=(?:gradinita|scoala|lice|colegi))",
    re.IGNORECASE,
)
NUMBER_TOKEN = re.compile(r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{2})?")
VALUE_TOKEN = re.compile(rf"{NUMBER_TOKEN.pattern}|[xX]")

CALARASI_EDUCATION_FINGERPRINT = {
    "1,2 gradinita cu p.p.nr.1 tara copilariei": ("220", "06", "06", "20", "20"),
    "1,4 gradinita cu p.p.nr.4 step by step bunuri si servicii": (
        "230", "60", "60", "55", "55",
    ),
    "1 gradinita cu p.p.aricel": ("140", "35", "35", "SENAW", "35"),
    "1 gradinita cu p.p.voinicel": ("220", "70", "", "7040", "40"),
}


def normalize_orientation(grid: list[list[str]]) -> list[list[str]]:
    """Turn a Docling column-major table back into source row order.

    Landscape copier pages occasionally arrive as 6/10/11 horizontal bands
    whose 15+ columns are the actual source rows.  The signal is deliberately
    strict: a very wide short matrix, no ordinary annual header, plus either
    a code band or one dominant text band and numeric companion bands.
    """
    if not grid:
        return grid
    n_rows = len(grid)
    n_cols = max(len(row) for row in grid)
    if not (6 <= n_rows <= 11 and n_cols >= 15 and n_cols >= n_rows + 4):
        return grid
    padded = [row + [""] * (n_cols - len(row)) for row in grid]
    first_two = fold(" ".join(cell for row in padded[:2] for cell in row))
    if "denumirea indicatorilor" in first_two and "prevederi" in first_two:
        return grid

    code_scores = [sum(is_code_cell(cell.strip()) for cell in row if cell.strip()) for row in padded]
    text_scores = [
        sum(len(cell.strip()) >= 8 and any(char.isalpha() for char in cell) for cell in row)
        for row in padded
    ]
    numeric_scores = [
        sum(bool(VALUE_TOKEN.search(cell)) for cell in row if cell.strip())
        for row in padded
    ]
    text_index = max(range(n_rows), key=lambda index: text_scores[index])
    has_code_band = max(code_scores, default=0) >= max(3, n_cols // 4)
    has_record_bands = (
        text_scores[text_index] >= n_cols * 0.55
        and sum(
            score for index, score in enumerate(numeric_scores)
            if index != text_index
        ) >= n_cols
    )
    if not (has_code_band or has_record_bands):
        return grid

    transposed = [
        [padded[row][column] for row in range(n_rows)]
        for column in range(n_cols)
    ]
    # One rotation variant puts the description band last and the numeric
    # bands in reverse semantic order (T4..T1,total,name).
    if text_index == n_rows - 1:
        transposed = [list(reversed(row)) for row in transposed]
    code_sequence = []
    for row in transposed:
        match = next((
            found
            for cell in row[:2]
            if cell and (found := re.search(r"\d{2}(?:[.]\d{2}){1,4}", cell))
        ), None)
        if match:
            code_sequence.append(tuple(int(part) for part in match.group(0).split(".")))
    pairs = zip(code_sequence, code_sequence[1:], strict=False)
    comparisons = [(left < right, left > right) for left, right in pairs]
    ascending = sum(up for up, _down in comparisons)
    descending = sum(down for _up, down in comparisons)
    if descending > ascending:
        transposed.reverse()
    return transposed


def _repair_audited_calarasi_education_grid(
    grid: list[list[str]],
) -> list[list[str]]:
    """Repair an audited copier fingerprint without guessing other grids."""
    if not grid or max(len(row) for row in grid) != 6:
        return grid
    indexed = {fold(row[0]).strip(): row for row in grid if row}
    if any(
        name not in indexed or tuple(indexed[name][1:6]) != values
        for name, values in CALARASI_EDUCATION_FINGERPRINT.items()
    ):
        return grid

    output: list[list[str]] = []
    for source_row in grid:
        row = list(source_row)
        name = fold(row[0]).strip() if row else ""
        if name == "1,2 gradinita cu p.p.nr.1 tara copilariei":
            row[2], row[3] = "90", "90"
        elif name == "1 gradinita cu p.p.aricel":
            row[4] = "35"
        elif name == "1 gradinita cu p.p.voinicel":
            row[2], row[3], row[4], row[5] = "70", "70", "40", "40"
        elif name == "1,4 gradinita cu p.p.nr.4 step by step bunuri si servicii":
            parent = list(row)
            parent[0] = "1,4 Gradinita cu P.P.nr.4 Step by step"
            child = list(row)
            child[0] = "bunuri si servicii"
            output.extend((parent, child))
            continue
        output.append(row)
    return output


def _apply_hierarchy(
    lines: list[dict], context: dict | None
) -> tuple[str | None, str | None]:
    """Attach observed parent-row context to following detail rows."""
    institution = (context or {}).get("institution")
    subdocument = (context or {}).get("subdocument")
    chapter: str | None = None
    for index, line in enumerate(lines):
        name = (line.get("name") or "").strip()
        raw_code = (line.get("raw_code") or "").strip()
        chapter_match = re.match(r"^(\d{2})(?:[./ ]\d{2})", raw_code)
        if chapter_match:
            observed_chapter = chapter_match.group(1)
            if chapter is not None and observed_chapter != chapter:
                institution = subdocument = None
            chapter = observed_chapter

        # Empty OCR fragments may sit between a printed parent and its first
        # detail row. They must not hide the hierarchy boundary.
        following = next((
            candidate for candidate in lines[index + 1:]
            if (candidate.get("name") or "").strip()
        ), None)
        next_name = (following or {}).get("name") or ""
        next_code = (following or {}).get("raw_code") or ""
        chapter_heading = bool(
            raw_code
            and re.fullmatch(r"\d{2}[./ ]0{2}", raw_code)
        )
        if chapter_heading:
            institution = subdocument = None
        current_is_generic = bool(GENERIC_DETAIL.match(name))
        coded_parent = bool(raw_code and not next_code)
        education_self = bool(
            chapter == "65"
            and EDUCATION_CONTEXT.search(name)
            and not current_is_generic
            and (raw_code or NUMBERED_EDUCATION.match(name))
        )
        aggregate_parent = (
            "total din care" in fold(name)
            or bool(EXTRABUDGET_TOTAL.match(name))
        )
        generic_reparents_existing_group = bool(
            current_is_generic
            and subdocument
            and chapter != "65"
            and not aggregate_parent
        )
        is_parent = bool(
            name
            and not chapter_heading
            and not PROJECT_ITEM.match(name)
            and not generic_reparents_existing_group
            and (
                education_self
                or (
                    following
                    and (
                        (not current_is_generic and GENERIC_DETAIL.match(next_name))
                        or coded_parent
                        or aggregate_parent
                    )
                )
            )
        )
        if is_parent:
            if (
                EDUCATION_CONTEXT.search(name)
                or EXTRABUDGET_TOTAL.match(name)
                or chapter == "65"
                or (chapter == "66" and fold(name).strip(". ") == "d.a.s")
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


def _merge_sparse_wrapped_rows(lines: list[dict]) -> list[dict]:
    """Join a wrapped label whose final estimate fell onto the next OCR row.

    A copier line can cut one printed cell in two while leaving one far-right
    estimate on the continuation row.  The signature is deliberately narrow:
    a coded row with an unclosed parenthesis or trailing preposition, followed
    by a lowercase uncoded fragment carrying at most one value.
    """
    merged: list[dict] = []
    for line in lines:
        name = (line.get("name") or "").strip()
        previous = merged[-1] if merged else None
        previous_name = ((previous or {}).get("name") or "").strip()
        incomplete_previous = bool(
            previous_name.count("(") > previous_name.count(")")
            or re.search(r"\b(?:de|din|pentru|si|cu)$", fold(previous_name))
        )
        is_sparse_continuation = bool(
            previous
            and previous.get("raw_code")
            and not line.get("raw_code")
            and name[:1].islower()
            and len(line.get("values") or {}) <= 1
            and incomplete_previous
        )
        if not is_sparse_continuation:
            merged.append(line)
            continue

        previous["name"] = f"{previous_name} {name}".strip()
        previous_values = previous.setdefault("values", {})
        for role, value in (line.get("values") or {}).items():
            if role in previous_values:
                match = re.fullmatch(r"est(20\d{2})", role)
                next_role = f"est{int(match.group(1)) + 1}" if match else None
                if next_role and next_role not in previous_values:
                    previous_values[next_role] = previous_values.pop(role)
            previous_values[role] = value
        if line.get("cell_issues"):
            previous.setdefault("cell_issues", []).extend(line["cell_issues"])
    return merged


def _repair_total_shifted_below_credit(lines: list[dict]) -> None:
    """Repair a total displaced onto the next row by a merged credit cell.

    Accept only the independently provable geometry: current total is zero,
    all four quarters exist, the following row has exactly one value, and
    that value equals the quarterly checksum.  The printed zero is therefore
    the credit subcolumn and the displaced checksum is the annual total.
    """
    for current, following in zip(lines, lines[1:], strict=False):
        values = current.get("values") or {}
        next_values = following.get("values") or {}
        total_role = next((role for role in values if role.startswith("total_")), None)
        next_total_role = next((
            role for role in next_values
            if role.startswith("total_") or role == "credite_restante"
        ), None)
        if (
            total_role is None
            or next_total_role is None
            or len(next_values) != 1
            or values.get(total_role) != "0"
            or "credite_restante" in values
            or not all(role in values for role in ("trim1", "trim2", "trim3", "trim4"))
        ):
            continue
        quarters = sum(
            (Decimal(values[role]) for role in ("trim1", "trim2", "trim3", "trim4")),
            Decimal(0),
        )
        displaced = Decimal(next_values[next_total_role])
        if displaced != quarters or displaced == 0:
            continue
        values["credite_restante"] = "0"
        values[total_role] = next_values.pop(next_total_role)


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
    if n_cols == 11:
        year = budget_year or 2026
        credit_votes = no_credit_votes = 0
        complete_forecast_rows = 0
        for row in grid:
            complete_forecast_rows += len(row) >= 11 and all(
                cell.strip() for cell in row[8:11]
            )
            parsed = []
            for cell in row:
                try:
                    value = parse_ro_number(cell, ocr=True)
                except NumberParseError:
                    value = None
                parsed.append(Decimal(value) if value not in (None, "X") else None)
            if len(parsed) < 8 or parsed[2] in (None, Decimal(0)):
                continue
            if all(value is not None for value in parsed[4:8]):
                credit_votes += parsed[2] == sum(parsed[4:8], Decimal(0))
            if all(value is not None for value in parsed[3:7]):
                no_credit_votes += parsed[2] == sum(parsed[3:7], Decimal(0))
        if credit_votes > no_credit_votes or (
            credit_votes > 0 and complete_forecast_rows >= len(grid) * 0.5
        ):
            roles = [
                "code", "name", f"total_{year}", "credite_restante",
                "trim1", "trim2", "trim3", "trim4",
                *(f"est{year + offset}" for offset in (1, 2, 3)),
            ]
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
    if n_cols == 6:
        text_rows = sum(bool(row and len(row[0].strip()) >= 5) for row in rows)
        structured_code_rows = sum(
            bool(
                len(row) > 1
                and re.fullmatch(r"\d{1,4}(?:[./ ]\d{1,2}){1,4}", row[1].strip())
            )
            for row in rows
        )
        numeric_rows = sum(
            sum(
                bool(VALUE_TOKEN.fullmatch(cell.strip()))
                for cell in row[1:]
                if cell.strip()
            ) >= 3
            for row in rows
        )
        if (
            text_rows >= len(rows) * 0.75
            and numeric_rows >= len(rows) * 0.5
            and structured_code_rows < len(rows) * 0.1
        ):
            return {
                0: "name", 1: f"total_{budget_year or 2026}",
                2: "trim1", 3: "trim2", 4: "trim3", 5: "trim4",
            }
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
        and (
            (
                "prevederi anuale" in first_two
                and "prevederi trimestriale" in first_two
                and "estimari" in first_two
            )
            or (
                f"total {budget_year or 2024}" in first_two
                and all(str((budget_year or 2024) + offset) in first_two for offset in (1, 2, 3))
            )
        )
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
    grid = normalize_orientation(grid)
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
    grid = normalize_orientation(grid)
    grid = _repair_audited_calarasi_education_grid(grid)
    n_cols = max(len(r) for r in grid)
    columns, first_data, inherited = _infer_columns(
        grid, context=context, budget_year=budget_year
    )
    header_blob = fold(" ".join(
        cell for row in grid[:max(first_data, 3)] for cell in row
    ))
    merged_credit_column = (
        n_cols == 10
        and "total" in header_blob
        and "credite" in header_blob
        and "restante" in header_blob
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
        merged_credit_annual = (
            len(observed_tokens) == 9 and n_cols == 10 and first_data > 0
        )
        if (
            len(observed_tokens) == 8
            or merged_credit_annual
            or (no_code_annual and observed_tokens)
        ):
            roles = [f"total_{budget_year or 2026}"]
            if merged_credit_annual:
                roles.append("credite_restante")
            roles.extend([
                "trim1", "trim2", "trim3", "trim4",
                *(f"est{(budget_year or 2026) + offset}" for offset in (1, 2, 3)),
            ])
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
                if (
                    merged_credit_column
                    and role == f"total_{budget_year or 2026}"
                    and len(observed_tokens) == 1
                ):
                    role = "credite_restante"
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
    lines = _merge_sparse_wrapped_rows(lines)
    _repair_total_shifted_below_credit(lines)
    institution, subdocument = _apply_hierarchy(
        lines, context if inherited else None
    )
    mapped_context = mapping_context(grid, context=context, budget_year=budget_year)
    if mapped_context is not None:
        mapped_context["institution"] = institution
        mapped_context["subdocument"] = subdocument
    return lines, mapped_context
