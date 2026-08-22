"""Shared vocabulary and helpers for layout mappers.

Every mapper consumes a plain text grid (rows of cell strings, as stored in
the OCR payload) and emits extraction-contract lines via mk_line.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from ..parsing import (
    NumberParseError,
    parse_ro_number,
    split_combined_code,
)

HEADER_PATTERNS = [
    # order matters: specific labels must win over their generic substrings
    (re.compile(r"cod\s*rand"), "rowno"),
    (re.compile(r"\bcod\b"), "code"),
    (re.compile(r"denumirea|denumire"), "name"),
    (re.compile(r"2027"), "est2027"),
    (re.compile(r"2028"), "est2028"),
    (re.compile(r"2029"), "est2029"),
    (re.compile(r"credite externe"), "credite_externe"),
    (re.compile(r"credit de? angajam"), "credite_angajament"),
    (re.compile(r"credite interne"), "credite_interne"),
    (re.compile(r"credite"), "credite_restante"),
    (re.compile(r"valoare"), "valoare_an_curent"),
    # buget general (matrix) columns — Arad-style consolidated budget
    (re.compile(r"total buget general|8\s*=\s*6"), "total_general"),
    (re.compile(r"transferuri"), "transferuri"),
    (re.compile(r"integral din venituri|finantate integral"), "inst_integral_venituri_proprii"),
    (re.compile(r"venituri proprii si subventii|subventii din bugetul local"), "inst_venituri_proprii_subventii"),
    (re.compile(r"imprumut"), "imprumuturi"),
    (re.compile(r"fonduri(lor)? externe nerambursabile|nerambursabile"), "fonduri_externe"),
    (re.compile(r"intre bugete"), "transferuri"),
    (re.compile(r"bugetul local"), "bugetul_local"),
    (re.compile(r"buget local"), "buget_local"),
    (re.compile(r"buget an 2026|buget initial"), "buget_2026"),
    (re.compile(r"\bfen\b|08d"), "buget_fen"),
    (re.compile(r"prevederi anuale|buget ?2026"), "total_2026"),
    (re.compile(r"\btotal\b"), "total"),
    (re.compile(r"rand|nr\.?\s*crt"), "rowno"),
    (re.compile(r"trim\.?\s*i\b"), "trim1"),
]
# When a role is already taken, a second column matching it falls back here
# (e.g. the "din care credite..." subcolumn whose header OCR loses).
ROLE_FALLBACK = {"total_2026": "credite_restante"}
HEADER_HINT = re.compile(r"denumirea|cod\b|indicator|prevederi|estimari|buget|mii lei|trim")
CODE_LIKE = re.compile(r"^\d{2}[\d.\s]*\*?\)?$")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
SECTION_ROW = re.compile(
    r"^(\d{6,10}\s+\S.*|sectiunea\s.*|total capitol\b.*|\d{1,3}\.\s+\S.*)$", re.I
)


def fold(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def is_code_cell(text: str) -> bool:
    t = text.strip()
    return (
        bool(CODE_LIKE.match(t))
        and len(re.sub(r"\D", "", t)) >= 2
        and not YEAR_RE.match(t)
    )


def mk_line(raw_code, name, section, values, cell_issues, row_no) -> dict:
    code, func_code = split_combined_code(raw_code)
    line = {
        "raw_code": (raw_code or "").replace(" ", "") or None,
        "code": code,
        "func_code": func_code,
        "name": name,
        "row_no": row_no,
        "section": section,
        "year": None,
        "values": values,
    }
    if cell_issues:
        line["cell_issues"] = cell_issues
    return line


def parse_cell(text: str, role: str, values: dict, cell_issues: list) -> None:
    """OCR-lenient numeric parse into values/{issues} under the given role."""
    try:
        parsed = parse_ro_number(text, ocr=True)
    except NumberParseError:
        cell_issues.append({"column": role, "raw": text})
        return
    if parsed == "X":
        values[role] = "X"
    elif parsed is not None:
        values[role] = str(Decimal(parsed))


FORMULA_HINT = re.compile(r"\(\s*cod")


def split_header(grid) -> tuple[list[int], int]:
    """Return (header row indices, index of first data row)."""
    header_rows = []
    for idx, row in enumerate(grid[:6]):
        joined = fold(" ".join(row))
        # a printed composition formula "(cod 59.01+...)" marks a DATA row,
        # whatever hint words it happens to contain
        if FORMULA_HINT.search(joined):
            break
        if HEADER_HINT.search(joined) and not any(is_code_cell(c) for c in row):
            header_rows.append(idx)
        elif header_rows:
            break
    first_data = (header_rows[-1] + 1) if header_rows else 0
    return header_rows, first_data


def column_semantics(grid, header_rows: list[int], n_cols: int) -> dict[int, str]:
    columns: dict[int, str] = {}
    for i in range(n_cols):
        joined = fold(" ".join(grid[r][i] for r in header_rows if i < len(grid[r])))
        for pattern, role in HEADER_PATTERNS:
            if not pattern.search(joined):
                continue
            if role not in columns.values():
                columns[i] = role
            elif ROLE_FALLBACK.get(role) and ROLE_FALLBACK[role] not in columns.values():
                columns[i] = ROLE_FALLBACK[role]
            break
    return columns


def widest_text_col(grid, first_data: int, n_cols: int, columns: dict) -> int:
    best, best_len = 0, -1
    for i in range(n_cols):
        if columns.get(i):
            continue
        total = sum(len(row[i]) for row in grid[first_data:] if i < len(row))
        if total > best_len:
            best, best_len = i, total
    return best
