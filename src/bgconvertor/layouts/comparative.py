"""Comparative BVC/execution/current-budget tables.

Several municipalities publish a compact management table whose three value
columns are the previous approved budget (``BVC YYYY``), execution for that
year (``EXEC.YYYY``), and the current approved budget.  The generic mapper
mistook those columns for the current total plus forecast years, which turns
otherwise correct OCR numbers into semantically wrong facts.

This mapper is header-driven and source-agnostic.  It also carries the column
and section contract across true continuation grids, skips the printed
``0/1/2/3/4/5`` index row, and resets six-column detail tables to ``TOTAL``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..parsing import NumberParseError, normalize_indicator_code, parse_ro_number
from .common import fold, mk_line

CODE_TOKEN = re.compile(
    r"\d{2}\.\d{2}(?:[/.,]\d{2}(?:[.]\d{2})*)?(?:\s+(?:SF|SD))?",
    re.IGNORECASE,
)
YEAR_TOKEN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
NUMBER_TOKEN = re.compile(
    r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{2})?"
)
ECONOMIC_TITLES = {
    "10", "20", "30", "40", "50", "51", "55", "56", "57", "58",
    "59", "60", "61", "65", "70", "71", "72", "79", "80", "81", "85",
}


def _header_contract(
    grid: list[list[str]], budget_year: int | None
) -> tuple[dict[int, str], int] | None:
    n_cols = max(len(row) for row in grid)
    for header_index, row in enumerate(grid[:3]):
        normalized = [fold(cell).replace(" ", "") for cell in row]
        joined = " ".join(normalized)
        if "bvc" not in joined or "exec" not in joined:
            continue
        columns: dict[int, str] = {}
        for index in range(n_cols):
            raw = row[index] if index < len(row) else ""
            header = fold(raw)
            compact = header.replace(" ", "")
            if "denumire" in header:
                columns[index] = "name"
                continue
            if "cod" in header:
                columns[index] = "code"
                continue
            if "crt" in header or re.search(r"\bnr\.?\b", header):
                columns[index] = "rowno"
                continue
            match = YEAR_TOKEN.search(compact)
            if match is None:
                continue
            year = int(match.group(1))
            if "exec" in compact:
                columns[index] = f"executie_{year}"
            elif "bvc" in compact:
                columns[index] = (
                    f"total_{year}"
                    if budget_year is None or year == budget_year
                    else f"buget_{year}"
                )
        roles = set(columns.values())
        value_roles = {
            role for role in roles
            if role.startswith(("buget_", "executie_", "total_"))
        }
        if "name" in roles and "code" in roles and len(value_roles) == 3:
            return columns, header_index + 1
    return None


def _contract(
    grid: list[list[str]],
    budget_year: int | None,
    context: dict | None,
) -> tuple[dict[int, str], int, str | None] | None:
    header = _header_contract(grid, budget_year)
    if header is not None:
        columns, first_data = header
        # Six-column variants have a separate row-number column and are the
        # detailed all-sections view in this family.
        section = "TOTAL" if max(len(row) for row in grid) >= 6 else (
            (context or {}).get("section")
        )
        return columns, first_data, section
    if not context or context.get("family") != "comparative_budget":
        return None
    n_cols = max(len(row) for row in grid)
    if int(context.get("n_cols") or 0) != n_cols:
        return None
    columns = {
        int(index): role
        for index, role in (context.get("columns") or {}).items()
    }
    if not {"name", "code"} <= set(columns.values()):
        return None
    return columns, 0, context.get("section")


def _is_index_row(row: list[str], columns: dict[int, str]) -> bool:
    values = []
    for index, role in columns.items():
        cell = row[index].strip() if index < len(row) else ""
        if role in {"rowno", "code", "name"}:
            values.append(cell)
        elif role.startswith(("buget_", "executie_", "total_")):
            values.append(cell)
    digits = [re.sub(r"\D", "", value) for value in values]
    digits = [value for value in digits if value]
    return len(digits) >= 4 and all(len(value) == 1 for value in digits)


def _clean_numeric(text: str) -> str:
    value = text.strip()
    # Native copier text occasionally drops only the decimal comma while
    # retaining the separating space: ``14.508 80`` -> ``14.508,80``.
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+ \d{2}", value):
        prefix, decimals = value.rsplit(" ", 1)
        return f"{prefix},{decimals}"
    return value


def _parse_value(text: str) -> tuple[str | None, dict | None]:
    if not text.strip():
        return None, None
    cleaned = _clean_numeric(text)
    try:
        parsed = parse_ro_number(cleaned, ocr=True)
    except NumberParseError:
        return None, {"raw": text}
    if parsed in (None, "X"):
        return None, None
    return str(Decimal(parsed)), None


def _parse_value_tokens(text: str) -> tuple[list[str], dict | None]:
    """Parse one or more values collapsed into the same OCR grid cell."""
    raw = text.strip()
    if not raw:
        return [], None
    cleaned = _clean_numeric(raw)
    if cleaned != raw:
        value, issue = _parse_value(cleaned)
        return ([value] if value is not None else []), issue
    tokens = [match.group(0) for match in NUMBER_TOKEN.finditer(raw)]
    if len(tokens) <= 1:
        value, issue = _parse_value(raw)
        return ([value] if value is not None else []), issue
    values = []
    for token in tokens:
        value, issue = _parse_value(token)
        if value is None:
            return [], issue or {"raw": raw}
        values.append(value)
    return values, None


def _printed_codes(code_cell: str) -> list[str]:
    return [match.group(0).strip() for match in CODE_TOKEN.finditer(code_cell)]


def _expanded_names(code_cell: str, name_cell: str, codes: list[str]) -> list[str]:
    """Keep useful labels when Docling collapsed adjacent logical rows."""
    names = [name_cell.strip()] * len(codes)
    matches = list(CODE_TOKEN.finditer(code_cell))
    suffix = code_cell[matches[-1].end():].strip(" -") if matches else ""
    if suffix:
        names[-1] = suffix
    return names


def classification_codes(raw_code: str | None) -> tuple[str | None, str | None]:
    """Return ``(code, func_code)`` for this mixed classification family."""
    printed = (raw_code or "").strip()
    functional = economic = None
    if "/" in printed:
        functional, economic = printed.split("/", 1)
    else:
        match = re.fullmatch(r"(\d{2}\.\d{2})\.(.+)", printed)
        if match and re.match(r"\d{2}", match.group(2)):
            first_tail = re.match(r"\d{2}", match.group(2)).group(0)
            if first_tail in ECONOMIC_TITLES:
                functional, economic = match.groups()
    if functional and economic:
        economic = re.sub(r"\s+S[FD]\s*$", "", economic, flags=re.IGNORECASE)
        normalized_functional = normalize_indicator_code(functional)
        normalized_economic = normalize_indicator_code(economic)
        if normalized_functional and normalized_economic:
            return normalized_economic, normalized_functional
    return normalize_indicator_code(printed), None


def _comparative_line(
    raw_code: str | None,
    name: str,
    section: str | None,
    values: dict[str, str],
    issues: list[dict],
    row_no: int | None,
) -> dict:
    """Build a line and preserve the family's functional/economic split."""
    line = mk_line(raw_code, name, section, values, issues, row_no)
    code, func_code = classification_codes(raw_code)
    line["code"] = code
    line["func_code"] = func_code
    return line


def _code_and_name(code_cell: str, name_cell: str) -> tuple[str | None, str]:
    code_text = code_cell.strip()
    name = name_cell.strip()
    # Composite official identities such as ``42.02/88.01, 88.03`` or
    # ``54.02/50(05)`` must remain whole. Taking the final regex match would
    # silently turn the first example into the unrelated code ``88.03``.
    if code_text and re.fullmatch(r"[\d\s.,/()]+(?:S[FD])?", code_text, re.IGNORECASE):
        return code_text, name
    matches = list(CODE_TOKEN.finditer(code_text))
    if matches:
        match = matches[-1]
        raw_code = match.group(0).strip()
        prefix = code_text[:match.start()].strip(" -")
        suffix = code_text[match.end():].strip(" -")
        additions = [part for part in (prefix, suffix) if part]
        if additions:
            name = " ".join([*additions, name]).strip()
        return raw_code, name
    leading = CODE_TOKEN.match(name)
    if leading:
        return leading.group(0).strip(), name[leading.end():].strip(" -")
    return code_text or None, name


def _section_for(raw_code: str | None, name: str, current: str | None) -> str | None:
    normalized = fold(name)
    pseudo = not raw_code or fold(raw_code).strip(". ") in {"i", "a", "b", "titlu"}
    if "venituri total" in normalized or "cheltuieli total" in normalized \
            or normalized.startswith("total cheltuieli"):
        return "TOTAL"
    if pseudo and normalized.startswith(("veniturile sectiunii de functionare", "sectiunea de functionare")):
        return "FUNCTIONARE"
    if pseudo and normalized.startswith(("veniturile sectiunii de dezvoltare", "sectiunea de dezvoltare")):
        return "DEZVOLTARE"
    return current


def _map(
    grid: list[list[str]],
    *,
    budget_year: int | None,
    context: dict | None,
) -> tuple[list[dict], dict] | None:
    contract = _contract(grid, budget_year, context)
    if contract is None:
        return None
    columns, first_data, section = contract
    n_cols = max(len(row) for row in grid)
    code_col = next(index for index, role in columns.items() if role == "code")
    name_col = next(index for index, role in columns.items() if role == "name")
    rowno_col = next((index for index, role in columns.items() if role == "rowno"), None)
    value_columns = [
        (index, role)
        for index, role in sorted(columns.items())
        if role.startswith(("buget_", "executie_", "total_"))
    ]
    lines = []
    for row in grid[first_data:]:
        cells = [row[index].strip() if index < len(row) else "" for index in range(n_cols)]
        if _is_index_row(cells, columns):
            continue
        code_cell = cells[code_col]
        name_cell = cells[name_col]
        printed_codes = _printed_codes(code_cell)
        row_raw = cells[rowno_col] if rowno_col is not None else ""
        row_no = int(row_raw) if row_raw.isdigit() else None
        value_tokens: dict[str, list[str]] = {}
        token_issues = []
        for index, role in value_columns:
            parsed, issue = _parse_value_tokens(cells[index])
            if parsed:
                value_tokens[role] = parsed
            if issue is not None:
                token_issues.append({"column": role, **issue})

        # Another common collapse shape leaves the first logical row's values
        # in an anonymous grid row and both identities in the following row.
        # The one-to-one value shape on both rows makes the repair independent
        # of names or arithmetic guesses.
        previous = lines[-1] if lines else None
        if (
            len(printed_codes) == 2
            and value_tokens
            and not token_issues
            and all(len(items) == 1 for items in value_tokens.values())
            and previous is not None
            and not previous.get("raw_code")
            and not (previous.get("name") or "").strip()
            and previous.get("values")
            and set(previous["values"]) == set(value_tokens)
        ):
            lines.pop()
            names = _expanded_names(code_cell, name_cell, printed_codes)
            lines.append(_comparative_line(
                printed_codes[0], names[0], section, previous["values"], [], None
            ))
            lines.append(_comparative_line(
                printed_codes[1], names[1], section,
                {role: items[0] for role, items in value_tokens.items()},
                [], row_no,
            ))
            continue

        # Docling occasionally collapses two adjacent ruled rows into one.
        # When every populated value column contains exactly one value per
        # printed code, the split is fully observed and therefore safe.
        if (
            len(printed_codes) >= 2
            and value_tokens
            and not token_issues
            and all(len(items) == len(printed_codes) for items in value_tokens.values())
        ):
            names = _expanded_names(code_cell, name_cell, printed_codes)
            for logical_index, printed_code in enumerate(printed_codes):
                logical_values = {
                    role: items[logical_index]
                    for role, items in value_tokens.items()
                }
                logical_row_no = row_no if logical_index == len(printed_codes) - 1 else None
                lines.append(_comparative_line(
                    printed_code,
                    names[logical_index],
                    section,
                    logical_values,
                    [],
                    logical_row_no,
                ))
            continue

        values = {
            role: items[0]
            for role, items in value_tokens.items()
            if len(items) == 1
        }
        issues = list(token_issues)
        for role, items in value_tokens.items():
            if len(items) > 1:
                issues.append({"column": role, "raw": cells[next(
                    index for index, column_role in value_columns if column_role == role
                )]})
        raw_code, name = _code_and_name(code_cell, name_cell)
        if not raw_code and not name and not values and not issues:
            continue
        section = _section_for(raw_code, name, section)
        lines.append(_comparative_line(
            raw_code, name, section, values, issues, row_no
        ))
    mapped_context = {
        "family": "comparative_budget",
        "n_cols": n_cols,
        "columns": {str(index): role for index, role in columns.items()},
        "budget_year": budget_year,
        "section": section,
    }
    return lines, mapped_context


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
