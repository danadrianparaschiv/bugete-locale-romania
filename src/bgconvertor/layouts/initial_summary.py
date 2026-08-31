"""Two-column initial-budget summaries with numbered indicator rows.

The source prints a single current-year ``Buget initial`` amount beside a
free-text indicator.  Numbering is embedded at the start of the indicator
cell and continuation pages omit the header.  Treating the amount as a
historical ``buget_YYYY`` column loses the current budget total semantics.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

NUMBERED_ROW = re.compile(
    r"^\s*(\d+(?:[.,]\d+)*\.?)\s*(?=[A-Za-zĂÂÎȘŞȚŢ•])(.*)$",
    re.IGNORECASE,
)
ROMAN_TOTAL = re.compile(
    r"^\s*((?:I|II))\.?\s+(?=(?:venituri|cheltuieli)\b)(.*)$",
    re.IGNORECASE,
)


def _header(grid: list[list[str]]) -> tuple[int, int, str | None] | None:
    if not grid or max(len(row) for row in grid) != 2:
        return None
    joined = fold(" ".join(cell for row in grid[:3] for cell in row))
    if "buget initial" not in joined or "denumire" not in joined:
        return None
    first_data = 1
    year = None
    section = None
    if len(grid) > 1:
        second = " ".join(grid[1]).strip()
        section = _section(second, None)
        match = re.search(r"(?<!\d)(20\d{2})(?!\d)", second)
        if match:
            year = int(match.group(1))
            first_data = 2
    return first_data, year or 2026, section


def _identity(text: str) -> tuple[str | None, str]:
    raw = text.strip()
    match = ROMAN_TOTAL.match(raw) or NUMBERED_ROW.match(raw)
    if not match:
        return None, raw
    code = match.group(1).replace(",", ".")
    return code, match.group(2).strip()


def _section(name: str, current: str | None) -> str | None:
    normalized = fold(name)
    if "sectiunea de functionare" in normalized:
        return "SECTIUNEA DE FUNCTIONARE"
    if "pentru dezvoltare" in normalized or "cheltuieli de dezvoltare" in normalized:
        return "SECTIUNEA DE DEZVOLTARE"
    return current


def _contract(
    grid: list[list[str]],
    budget_year: int | None,
    context: dict | None,
) -> tuple[int, int, str | None] | None:
    header = _header(grid)
    if header is not None:
        first_data, printed_year, section = header
        return first_data, budget_year or printed_year, section
    if (
        context
        and context.get("family") == "initial_summary"
        and max(len(row) for row in grid) == 2
    ):
        return 0, int(context.get("budget_year") or budget_year or 2026), context.get("section")
    return None


def _map(
    grid: list[list[str]],
    *,
    budget_year: int | None,
    context: dict | None,
) -> tuple[list[dict], dict] | None:
    contract = _contract(grid, budget_year, context)
    if contract is None:
        return None
    first_data, year, section = contract
    role = f"total_{year}"
    lines = []
    subdocument = (context or {}).get("subdocument")
    for row in grid[first_data:]:
        name_cell = row[0].strip() if row else ""
        value_cell = row[1].strip() if len(row) > 1 else ""
        if not name_cell and not value_cell:
            continue
        raw_code, name = _identity(name_cell)
        section = _section(name, section)
        values: dict[str, str] = {}
        issues: list[dict] = []
        if value_cell:
            parse_cell(value_cell, role, values, issues)
        line = mk_line(raw_code, name, section, values, issues, None)
        if raw_code:
            subdocument = name or subdocument
        elif subdocument:
            line["subdocument"] = subdocument
        lines.append(line)
    return lines, {
        "family": "initial_summary",
        "n_cols": 2,
        "columns": {"0": "name", "1": role},
        "budget_year": year,
        "budget_table": True,
        "section": section,
        "subdocument": subdocument,
    }


def try_map(
    grid: list[list[str]],
    budget_year: int | None = None,
    context: dict | None = None,
) -> list[dict] | None:
    mapped = _map(grid, budget_year=budget_year, context=context)
    return mapped[0] if mapped is not None else None


def mapping_context(
    grid: list[list[str]],
    budget_year: int | None = None,
    context: dict | None = None,
) -> dict | None:
    mapped = _map(grid, budget_year=budget_year, context=context)
    return mapped[1] if mapped is not None else context
