"""Budget-year column semantics shared by extractors, exports and analytics.

Column keys intentionally include the printed year (``total_2027``,
``est2028``).  This keeps a workbook self-describing and avoids silently
labelling a 2025 or 2027 budget as 2026.  Older 2026 keys remain unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
YEAR_ROLE_RE = re.compile(r"^(total|buget)_(\d{4})$|^est(\d{4})$")


def years_in(text: str) -> list[int]:
    """Distinct plausible years in source order."""
    return list(dict.fromkeys(int(match) for match in YEAR_RE.findall(text or "")))


def infer_budget_year(text: str, fallback: int | None = None) -> int | None:
    """Infer the current budget year from a header band.

    Annual tables normally print the current year followed by three forecast
    years.  Choosing the earliest year is robust to OCR losing words such as
    ``Buget`` or ``Estimari`` and ignores repeated occurrences.
    """
    candidates = [year for year in years_in(text) if 2000 <= year <= 2100]
    return min(candidates) if candidates else fallback


def infer_budget_year_from_path(path: Path) -> int | None:
    """Read a corpus year from a ``data/YYYY/...`` path, if present."""
    for part in path.resolve().parts:
        if re.fullmatch(r"20\d{2}", part):
            return int(part)
    return None


def role_for_header(text: str, budget_year: int | None = None) -> str | None:
    """Return a dynamic year role for one normalized header cell."""
    folded = text.lower()
    if "trim" in folded:
        return None
    years = years_in(folded)
    if not years:
        return None
    year = years[0]
    if "exec" in folded:
        return f"executie_{year}"
    # Comparative tables print the previous approved budget next to its
    # execution and the current BVC. A previous-year BVC is historical
    # context, not the current document total and not a forecast.
    if "bvc" in folded:
        return f"total_{year}" if budget_year in (None, year) else f"buget_{year}"
    if "estim" in folded or re.fullmatch(r"\s*(?:19|20)\d{2}\s*", folded):
        return f"est{year}"
    if "initial" in folded:
        return f"buget_{year}"
    if "buget" in folded or "prevederi" in folded or "total" in folded:
        return f"total_{year}"
    return None


def remap_role(role: str, budget_year: int | None, source_year: int = 2026) -> str:
    """Shift legacy fixed-year mapper roles to the detected document year."""
    if budget_year is None or budget_year == source_year:
        return role
    if role == f"total_{source_year}":
        return f"total_{budget_year}"
    if role == f"buget_{source_year}":
        return f"buget_{budget_year}"
    match = re.fullmatch(r"est(\d{4})", role)
    if match and source_year < int(match.group(1)) <= source_year + 3:
        return f"est{budget_year + int(match.group(1)) - source_year}"
    return role


def remap_lines(lines: list[dict], budget_year: int | None) -> list[dict]:
    """Shift fixed 2026-era roles in mapper output, including issue columns."""
    if budget_year is None:
        return lines
    if budget_year != 2026:
        for line in lines:
            values = line.get("values") or {}
            if "total" in values and f"total_{budget_year}" not in values:
                values[f"total_{budget_year}"] = values.pop("total")
            for issue in line.get("cell_issues") or []:
                if issue.get("column") == "total":
                    issue["column"] = f"total_{budget_year}"
    if budget_year == 2026:
        return lines
    roles = {
        role
        for line in lines
        for role in (
            *(line.get("values") or {}),
            *(issue.get("column") for issue in line.get("cell_issues") or []),
        )
        if role
    }
    if f"total_{budget_year}" in roles or f"buget_{budget_year}" in roles:
        return lines
    for line in lines:
        values = line.get("values") or {}
        line["values"] = {remap_role(key, budget_year): value for key, value in values.items()}
        for issue in line.get("cell_issues") or []:
            if issue.get("column"):
                issue["column"] = remap_role(issue["column"], budget_year)
    return lines


def role_year(role: str) -> int | None:
    match = YEAR_ROLE_RE.fullmatch(role)
    if not match:
        return None
    return int(match.group(2) or match.group(3))


def annual_columns(columns: Iterable[str]) -> list[str]:
    """Current-budget columns in deterministic preference order."""
    cols = set(columns)
    out = ["total"] if "total" in cols else []
    out.extend(sorted((c for c in cols if c.startswith("total_")), reverse=True))
    out.extend(sorted((c for c in cols if c.startswith("buget_")), reverse=True))
    return out


def estimate_columns(columns: Iterable[str]) -> list[str]:
    return sorted(
        (column for column in set(columns) if re.fullmatch(r"est\d{4}", column)),
        key=lambda column: int(column[3:]),
    )


def column_label(role: str) -> str | None:
    match = re.fullmatch(r"total_(\d{4})", role)
    if match:
        return f"TOTAL {match.group(1)}"
    match = re.fullmatch(r"buget_(\d{4})", role)
    if match:
        return f"Buget {match.group(1)}"
    match = re.fullmatch(r"est(\d{4})", role)
    if match:
        return f"Estimare {match.group(1)}"
    return None
