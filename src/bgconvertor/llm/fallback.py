"""Full-page LLM extraction for pages docling cannot structure.

Trigger: a scanned page whose mapped table yields almost no (code, values)
lines although OCR found a table (e.g. Arad's per-institution school
budgets, where TableFormer collapses the tight layout). The whole page is
transcribed by Claude into the extraction contract; every line is marked
source="llm" and flows through the same validator as everything else.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from ..years import remap_role, role_year

log = logging.getLogger("bgc.llm.fallback")

COLUMN_ORDER = (
    "bugetul_local",
    "inst_venituri_proprii_subventii",
    "inst_integral_venituri_proprii",
    "imprumuturi",
    "fonduri_externe",
    "total",
    "transferuri",
    "total_general",
    "credite_stinse",
    "credite_restante",
    "total_2026",
    "buget_2026",
    "trim1",
    "trim2",
    "trim3",
    "trim4",
    "est2027",
    "est2028",
    "est2029",
)

LAYOUT_COLUMNS = {
    "scan_general_matrix": (
        "bugetul_local",
        "inst_venituri_proprii_subventii",
        "inst_integral_venituri_proprii",
        "imprumuturi",
        "fonduri_externe",
        "total",
        "transferuri",
        "total_general",
    ),
    "scan_transposed_detail": (
        "total", "credite_stinse", "trim1", "trim2", "trim3", "trim4",
        "est2027", "est2028", "est2029",
    ),
    "scan_revenue_detail": ("buget_2026", "est2027", "est2028", "est2029"),
    "scan_expense_chapter": ("buget_2026", "credite_restante"),
    "scan_annual_total": ("total_2026",),
}


class FallbackCell(BaseModel):
    column: str = Field(description="Numele coloanei, exact ca în lista cerută")
    value: str | None = Field(description="Exact ce e tipărit; 'X' sau null dacă e goală")


class FallbackRow(BaseModel):
    code: str | None = Field(
        description="Codul indicator tipărit (ex. '65.10', '20.01', '33.10.14'); "
        "null pentru rânduri fără cod (titluri, '*', litere)"
    )
    name: str = Field(description="Denumirea indicatorilor, exact ca în tabel")
    section: str | None = Field(
        description="Contextul curent: numele instituției (ex. '16. Liceul Tehnologic "
        "de Stiinte Aplicate Arad') sau secțiunea, dacă pagina are astfel de "
        "subtitluri; altfel null"
    )
    cells: list[FallbackCell] = Field(description="Valorile rândului, una per coloană")


class PageReading(BaseModel):
    rows: list[FallbackRow]
    note: str = Field(default="", description="Observații despre lizibilitate")


FALLBACK_PROMPT = """\
Imaginea este o pagină scanată dintr-un buget local românesc (mii lei, format \
românesc: punct=mii, virgulă=zecimale). Recunoașterea automată a tabelului a \
eșuat pe această pagină, așa că transcrie tu tabelul, rând cu rând, de sus în jos.

Coloanele numerice de transcris: {columns}.

Reguli:
- Un rând per linie de tabel, în ordinea din pagină.
- 'code' = codul indicator tipărit în coloana de cod; null dacă rândul nu are \
cod numeric (ex. '*', litere, titluri de instituție).
- Rândurile-titlu (ex. '16. Liceul Tehnologic ...', 'TOTAL VENITURI', \
'SECTIUNEA DE FUNCTIONARE') se transcriu și ele, iar numele instituției/secțiunii \
curente se pune în 'section' pentru toate rândurile care îi aparțin.
- Transcrie STRICT ce este tipărit — nu calcula și nu deduce valori. Celulă \
ilizibilă sau acoperită de ștampilă = null.
"""


@dataclass(frozen=True)
class FallbackBand:
    """One independently transcribed vertical table band on a dense page."""

    index: int
    y0: float
    y1: float
    row_count: int

    @property
    def height_fraction(self) -> float:
        return max(0.05, min(1.0, self.y1 - self.y0))


def fallback_columns(payload: dict, corpus_columns: list[str] | None = None) -> list[str]:
    """Infer the complete schema from this page only.

    The old global top-six rule silently dropped quarterly columns on wide
    tables. A paid page transcription must be asked for the full known schema.
    """
    observed = {
        column
        for line in payload.get("lines", [])
        for column in (line.get("values") or {})
    }
    observed.update(
        issue.get("column")
        for line in payload.get("lines", [])
        for issue in line.get("cell_issues", [])
        if issue.get("column")
    )
    # Mapper header inference is page-local and remains available even when
    # the mapper produced zero rows.  Ignore identity/text columns.
    observed.update(
        column
        for column in ((payload.get("mapping_context") or {}).get("columns") or {}).values()
        if column not in {"name", "code", "func_code", "rowno", "ignore"}
    )
    budget_year = payload.get("budget_year")
    observed.update(
        remap_role(column, budget_year)
        for column in LAYOUT_COLUMNS.get(payload.get("layout"), ())
    )
    # ``corpus_columns`` is retained for API compatibility with older callers,
    # but deliberately ignored: another page's most common columns are not
    # evidence for this page and previously dropped legitimate quarters.
    _ = corpus_columns
    # Fixed semantic columns retain their historical order. Dynamic annual
    # and forecast columns follow chronologically, so a 2025 document asks
    # the provider for buget_2025, est2026..est2028 rather than 2026 labels.
    # Printed annual tables put the current-year total before the quarters.
    # Treating dynamic annual columns as an afterthought used to ask the LLM
    # for trim1..trim4,total_YYYY and shifted every returned value one place.
    quarterly = {"trim1", "trim2", "trim3", "trim4"}
    static_order = [
        column for column in COLUMN_ORDER
        if role_year(column) is None and column not in quarterly
    ]
    ordered = [column for column in static_order if column in observed]
    annual = sorted(
        (column for column in observed if column.startswith(("total_", "buget_"))),
        key=lambda column: (role_year(column) or 0, column),
    )
    forecasts = sorted(
        (column for column in observed if column.startswith("est") and role_year(column)),
        key=lambda column: role_year(column) or 0,
    )
    ordered.extend(column for column in annual if column not in ordered)
    ordered.extend(column for column in COLUMN_ORDER if column in quarterly and column in observed)
    ordered.extend(column for column in forecasts if column not in ordered)
    ordered.extend(sorted(observed - set(ordered)))
    default = f"buget_{budget_year}" if budget_year else "buget_2026"
    return ordered[:12] or [default]


def fallback_bands(ocr_payload: dict | None, max_rows: int = 32) -> list[FallbackBand]:
    """Split OCR tables into bounded vertical bands using stored row boxes.

    A full-page JSON transcription becomes unreliable on 60-100 row tables.
    Docling already stores every row's vertical extent; chunk those extents so
    each paid response is small.  Invalid/legacy coordinates fall back to one
    full-page band, never guessed crops.
    """
    tables = (ocr_payload or {}).get("tables_rows_y") or []
    bands: list[FallbackBand] = []
    index = 0
    for table in tables:
        valid = [
            (max(0.0, float(row[0])), min(1.0, float(row[1])))
            for row in table
            if isinstance(row, (list, tuple))
            and len(row) == 2
            and 0.0 <= float(row[0]) < float(row[1]) <= 1.0
            and float(row[1]) - float(row[0]) < 0.95
        ]
        for start in range(0, len(valid), max_rows):
            chunk = valid[start:start + max_rows]
            if not chunk:
                continue
            bands.append(FallbackBand(
                index=index,
                y0=max(0.0, min(row[0] for row in chunk) - 0.015),
                y1=min(1.0, max(row[1] for row in chunk) + 0.015),
                row_count=len(chunk),
            ))
            index += 1
    return bands or [FallbackBand(index=0, y0=0.0, y1=1.0, row_count=0)]


def crop_fallback_band(image, band: FallbackBand):
    if image is None or band.height_fraction >= 0.99:
        return image
    y0 = max(0, int(band.y0 * image.height))
    y1 = min(image.height, int(band.y1 * image.height))
    if y1 - y0 < 40:
        return image
    return image.crop((0, y0, image.width, y1))


def fallback_max_tokens(
    payload: dict,
    n_columns: int,
    estimated_rows: int | None = None,
) -> int:
    estimated_rows = max(
        estimated_rows or 0,
        len(payload.get("lines", [])) if estimated_rows is None else 0,
        (
            payload.get("n_numeric_cells", 0) // max(1, n_columns)
            if estimated_rows is None else 0
        ),
    )
    return min(24000, max(4096, 768 + estimated_rows * (180 + 28 * n_columns)))


def fallback_benefit(payload: dict) -> float:
    """Expected recovered numeric cells, discounted for transcription uncertainty."""
    already_usable = sum(
        len(line.get("values") or {})
        for line in payload.get("lines", [])
        if line.get("code")
    )
    missing = max(1, payload.get("n_numeric_cells", 0) - already_usable)
    return 0.8 * missing


def extract_page_llm(
    client,
    image,
    columns: list[str],
    page: int,
    max_tokens: int | None = None,
    model: str | None = None,
    band: FallbackBand | None = None,
) -> dict:
    """Full-page transcription -> extraction-contract payload."""
    from ..parsing import NumberParseError, normalize_indicator_code, parse_ro_number

    cfg = client.config.llm
    fb_model = model or cfg.fallback_model or cfg.repair_model
    band_note = (
        f"\nAcesta este segmentul vertical {band.index + 1} al paginii; "
        "transcrie numai rândurile vizibile în acest segment.\n"
        if band is not None else ""
    )
    reading: PageReading = client.structured(
        "fallback_extract",
        FALLBACK_PROMPT.format(columns=", ".join(f'"{c}"' for c in columns))
        + band_note,
        PageReading,
        model=fb_model,
        image=image,
        page=page,
        max_tokens=max_tokens or 24000,
    )
    lines = []
    requested = set(columns)
    for row in reading.rows:
        values, cell_issues = {}, []
        for cell in row.cells:
            if cell.column not in requested:
                cell_issues.append({"column": cell.column, "raw": cell.value})
                continue
            if cell.value is None:
                continue
            try:
                parsed = parse_ro_number(cell.value, ocr=True)
            except NumberParseError:
                cell_issues.append({"column": cell.column, "raw": cell.value})
                continue
            if parsed == "X":
                values[cell.column] = "X"
            elif parsed is not None:
                values[cell.column] = str(parsed)
        line = {
            "raw_code": row.code,
            "code": normalize_indicator_code(row.code) if row.code else None,
            "func_code": None,
            "name": row.name,
            "row_no": None,
            "section": row.section,
            "year": None,
            "values": values,
            "source": f"llm:{fb_model}",
            "value_sources": {
                column: f"llm:{fb_model}" for column in values
            },
        }
        if cell_issues:
            line["cell_issues"] = cell_issues
        lines.append(line)
    return {
        "lines": lines,
        "text": None,
        "layout": "llm_fallback",
        "note": reading.note,
        "band": (
            {"index": band.index, "y0": band.y0, "y1": band.y1}
            if band is not None else None
        ),
    }


def extract_band_with_escalation(
    client,
    image,
    columns: list[str],
    page: int,
    band: FallbackBand,
    *,
    max_tokens: int,
    benefit_units: float,
    primary_model: str,
) -> dict:
    """Run cheap transcription first and premium only after structural failure."""
    cheap_payload = None
    first_error = None
    try:
        cheap_payload = extract_page_llm(
            client,
            image,
            columns,
            page,
            max_tokens=max_tokens,
            model=primary_model,
            band=band,
        )
    except Exception as exc:  # noqa: BLE001 - premium may still recover the band
        first_error = exc

    expected_good = max(1, min(3, (band.row_count or 8) // 8))
    cheap_failed = fallback_payload_good_lines(cheap_payload) < expected_good
    premium_payload = None
    if cheap_failed:
        from .escalation import premium_after_failure

        premium = premium_after_failure(client, primary_model, benefit_units)
        if premium:
            try:
                premium_payload = extract_page_llm(
                    client,
                    image,
                    columns,
                    page,
                    max_tokens=max_tokens,
                    model=premium,
                    band=band,
                )
            except Exception as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc

    best = max(
        (payload for payload in (cheap_payload, premium_payload) if payload),
        key=fallback_payload_good_lines,
        default=None,
    )
    if best is not None:
        return best
    if first_error is not None:
        raise first_error
    return {"lines": [], "text": None, "layout": "llm_fallback"}


def merge_page_payloads(deterministic: dict | None, llm_payloads: list[dict]) -> dict | None:
    """Merge LLM recovery into deterministic output at row and cell level.

    Deterministic values always win conflicts.  LLM values fill absent or
    explicitly unparseable cells, and LLM-only rows are appended.  This avoids
    the old winner-takes-page behavior where a better row count could still
    discard correct deterministic cells.
    """
    if deterministic is None and not llm_payloads:
        return None
    if deterministic is None:
        base = {"lines": [], "text": None, "layout": "llm_fallback"}
    else:
        base = deepcopy(deterministic)
    base.setdefault("lines", [])
    stats = {"filled_cells": 0, "llm_only_rows": 0, "conflicts_ignored": 0}

    by_exact: dict[tuple[str, str], list[dict]] = {}
    by_code: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for line in base["lines"]:
        code = _normalized_code(line)
        section = _fold(line.get("section") or "")
        if code:
            by_exact.setdefault((code, section), []).append(line)
            by_code.setdefault(code, []).append(line)
        name = _identity_name(line.get("name") or "")
        if name:
            by_name.setdefault(name, []).append(line)

    matched_counts: dict[int, int] = {}
    for payload in llm_payloads:
        for recovered in payload.get("lines", []):
            code = _normalized_code(recovered)
            section = _fold(recovered.get("section") or "")
            candidates = by_exact.get((code, section), []) if code else []
            if not candidates and code and len(by_code.get(code, [])) == 1:
                candidates = by_code[code]
            recovered_name = _identity_name(recovered.get("name") or "")
            if not candidates and recovered_name:
                candidates = by_name.get(recovered_name, [])
            if not candidates and len(recovered_name) >= 12:
                fuzzy = [
                    (SequenceMatcher(None, recovered_name, name).ratio(), lines)
                    for name, lines in by_name.items()
                ]
                best_ratio = max((ratio for ratio, _lines in fuzzy), default=0.0)
                best = [
                    lines for ratio, lines in fuzzy
                    if ratio == best_ratio and ratio >= 0.94
                ]
                if len(best) == 1:
                    candidates = best[0]
            target = None
            for candidate in candidates:
                used = matched_counts.get(id(candidate), 0)
                if used == 0:
                    target = candidate
                    matched_counts[id(candidate)] = 1
                    break
            if target is None:
                # Full-page recovery may populate a catastrophic empty page.
                # On an already productive page, however, an unidentifiable
                # no-code row is much more likely to duplicate OCR text than
                # to be a safe new fact.  New coded rows remain admissible.
                if base["lines"] and (not code or code in by_code):
                    continue
                appended = deepcopy(recovered)
                source = appended.get("source") or "llm"
                appended.setdefault(
                    "value_sources",
                    {column: source for column in (appended.get("values") or {})},
                )
                base["lines"].append(appended)
                stats["llm_only_rows"] += 1
                continue

            target_values = target.setdefault("values", {})
            target_issues = list(target.get("cell_issues") or [])
            broken_columns = {issue.get("column") for issue in target_issues}
            llm_source = recovered.get("source") or "llm"
            value_sources = target.setdefault("value_sources", {})
            deterministic_source = target.get("source") or (
                "digital" if base.get("layout") == "digital_detail" else "ocr"
            )
            for column in target_values:
                value_sources.setdefault(column, deterministic_source)
            filled = False
            for column, value in (recovered.get("values") or {}).items():
                if column not in target_values or column in broken_columns:
                    target_values[column] = value
                    value_sources[column] = llm_source
                    target_issues = [
                        issue for issue in target_issues
                        if issue.get("column") != column
                    ]
                    stats["filled_cells"] += 1
                    filled = True
                elif target_values[column] != value:
                    stats["conflicts_ignored"] += 1
            if filled:
                target["source"] = "mixed"
            if target_issues:
                target["cell_issues"] = target_issues
            else:
                target.pop("cell_issues", None)

    base["llm_merge"] = stats
    return base


def fallback_payload_good_lines(payload: dict | None) -> int:
    return sum(
        1
        for line in (payload or {}).get("lines", [])
        if line.get("code") and line.get("values")
    )


def _normalized_code(line: dict) -> str | None:
    from ..parsing import normalize_indicator_code

    return normalize_indicator_code(line.get("code") or line.get("raw_code"))


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return re.sub(
        r"\s+", " ",
        "".join(char for char in normalized if not unicodedata.combining(char)).lower(),
    ).strip()


def _identity_name(value: str) -> str:
    """Normalize OCR/LLM spelling noise without erasing word identity."""
    return re.sub(r"[^a-z0-9]+", " ", _fold(value)).strip()


def needs_fallback(payload: dict | None) -> bool:
    """A table was detected but almost nothing usable was mapped."""
    if not payload or not payload.get("n_tables"):
        return False
    if payload.get("layout") in (
        "investment_list", "hcl_prose", "allocations_annex", "annex_other"
    ):
        return False  # out of nomenclator scope — side-sheet data, not repair
    lines = payload.get("lines", [])
    if not lines:
        # A zero-line table is the catastrophic mapper collapse this fallback
        # exists to inspect.  Even zero OCR numeric tokens may mean OCR, not
        # the printed table, failed; its tiny benefit score keeps it last.
        return True
    numeric_cells = int(payload.get("n_numeric_cells") or 0)
    if numeric_cells < 10:
        return False  # nothing numeric on a partially mapped page worth a paid call
    good = fallback_payload_good_lines(payload)
    if len(lines) < 6 and good > 0:
        return False  # tiny end-of-document tables aren't worth a paid call
    return good < 3
