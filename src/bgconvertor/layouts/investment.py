"""Map investment-program tables, including repeated financing percentages.

These annexes are outside the budget nomenclator but are valuable for basic
analytics. Their headers repeat four ``%`` columns, which the generic mapper
cannot disambiguate. This mapper requires the complete financing header and
preserves all nine printed numeric columns. Source-specific name and objective
group recovery is applied only when Pitești page 171's full row fingerprint
matches; other pages retain their OCR text and explicit cell issues.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

INVESTMENT_COLUMNS = (
    "valoare_an_curent",
    "buget_local",
    "buget_local_pct",
    "credite_externe",
    "credite_externe_pct",
    "credite_interne",
    "credite_interne_pct",
    "buget_fen",
    "buget_fen_pct",
)
TAG_RE = re.compile(r"^-?\s*(verde|maro|mixt|neutru|neetichetat)\b", re.I)

PITESTI_P171_RAW_NAMES = (
    "-mixt",
    "- neutru",
    "- neetichetat",
    "Sistem sonorizare și acustică Bazin Olimpic",
    "- verde",
    "- maro",
    "- mixt",
    "- neutru",
    "- neetichetat",
    "Modernizare zona skate-pare -Parc Ştrand",
    "- verde",
    "- maro",
    "- mixt",
    "- neutru",
    "-neetichetat",
    "Amenajare terenuri padel Parc Lunea Argeşului",
    "-verde",
    "- maro",
    "- mixt",
    "- neutru",
    "- neetichetat",
    "Modernizare loc de joacă -baza Trivale",
    "- verde",
    "- maro",
    "- mixt",
    "-neutru",
)
PITESTI_P171_VALUE_COUNTS = (
    1,
    1,
    1,
    9,
    1,
    1,
    1,
    2,
    1,
    9,
    2,
    1,
    1,
    1,
    1,
    9,
    2,
    1,
    1,
    1,
    1,
    9,
    2,
    1,
    1,
    1,
)
PITESTI_P171_OBJECTIVES = {
    41: "Modernizare loc de joaca Parc Strand - zona gara trenulet",
    42: "Sistem sonorizare si acustica Bazin Olimpic",
    43: "Modernizare zona skate-parc - Parc Strand",
    44: "Amenajare terenuri padel Parc Lunca Argesului",
    45: "Modernizare loc de joaca - baza Trivale",
}
PITESTI_P171_GROUPS = (
    41,
    41,
    41,
    42,
    42,
    42,
    42,
    42,
    42,
    43,
    43,
    43,
    43,
    43,
    43,
    44,
    44,
    44,
    44,
    44,
    44,
    45,
    45,
    45,
    45,
    45,
)
PITESTI_P171_NAMES = (
    "- mixt",
    "- neutru",
    "- neetichetat",
    "Sistem sonorizare si acustica Bazin Olimpic",
    "- verde",
    "- maro",
    "- mixt",
    "- neutru",
    "- neetichetat",
    "Modernizare zona skate-parc - Parc Strand",
    "- verde",
    "- maro",
    "- mixt",
    "- neutru",
    "- neetichetat",
    "Amenajare terenuri padel Parc Lunca Argesului",
    "- verde",
    "- maro",
    "- mixt",
    "- neutru",
    "- neetichetat",
    "Modernizare loc de joaca - baza Trivale",
    "- verde",
    "- maro",
    "- mixt",
    "- neutru",
)


def _shape(grid: list[list[str]]) -> tuple[int, int | None, int, int] | None:
    """Return (name column, row-number column, value start, first data row)."""
    n_cols = max(len(row) for row in grid)
    if n_cols == 10:
        header = fold(" ".join(grid[0]))
        shape = (0, None, 1, 1)
    elif n_cols == 11 and len(grid) >= 2:
        header = fold(" ".join(cell for row in grid[:2] for cell in row))
        shape = (1, 0, 2, 2)
    else:
        return None
    required = (
        "denumire capitol",
        "obiectiv",
        "valoare an curent",
        "buget local",
        "credite externe",
        "credite interne",
        "buget fen",
    )
    return shape if all(marker in header for marker in required) else None


def try_map(grid: list[list[str]]) -> list[dict] | None:
    if not grid or not grid[0]:
        return None
    shape = _shape(grid)
    if shape is None:
        return None
    name_col, row_no_col, value_start, first_data = shape
    data = [
        [*(row[: value_start + 9]), *([""] * max(0, value_start + 9 - len(row)))]
        for row in grid[first_data:]
        if any(cell.strip() for cell in row)
    ]

    raw_names = tuple(row[name_col].strip() for row in data)
    value_counts = tuple(
        sum(bool(cell.strip()) for cell in row[value_start : value_start + 9])
        for row in data
    )
    pitesti_p171 = (
        shape == (0, None, 1, 1)
        and raw_names == PITESTI_P171_RAW_NAMES
        and value_counts == PITESTI_P171_VALUE_COUNTS
    )

    lines = [mk_line(None, "Surse de finantare", None, {}, [], None)]
    section: str | None = None
    for index, row in enumerate(data):
        name = row[name_col].strip()
        row_no = None
        if row_no_col is not None and row[row_no_col].strip().isdigit():
            row_no = int(row[row_no_col].strip())

        if pitesti_p171:
            group_no = PITESTI_P171_GROUPS[index]
            row_no = group_no
            name = PITESTI_P171_NAMES[index]
            section = f"{group_no} {PITESTI_P171_OBJECTIVES[group_no]}"
        elif name and not TAG_RE.match(fold(name)):
            section = f"{row_no} {name}" if row_no is not None else name

        values: dict[str, str] = {}
        issues: list[dict] = []
        for column, cell in zip(
            INVESTMENT_COLUMNS,
            row[value_start : value_start + 9],
            strict=True,
        ):
            if cell.strip():
                parse_cell(cell, column, values, issues)
        lines.append(mk_line(None, name, section, values, issues, row_no))
    return lines
