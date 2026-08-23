"""Full-page LLM extraction for pages docling cannot structure.

Trigger: a scanned page whose mapped table yields almost no (code, values)
lines although OCR found a table (e.g. Arad's per-institution school
budgets, where TableFormer collapses the tight layout). The whole page is
transcribed by Claude into the extraction contract; every line is marked
source="llm" and flows through the same validator as everything else.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

log = logging.getLogger("bgc.llm.fallback")


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


def extract_page_llm(client, image, columns: list[str], page: int) -> dict:
    """Full-page transcription -> extraction-contract payload."""
    from ..parsing import NumberParseError, normalize_indicator_code, parse_ro_number

    cfg = client.config.llm
    fb_model = cfg.fallback_model or cfg.repair_model
    reading: PageReading = client.structured(
        "fallback_extract",
        FALLBACK_PROMPT.format(columns=", ".join(f'"{c}"' for c in columns)),
        PageReading,
        model=fb_model,
        image=image,
        page=page,
        max_tokens=24000,  # dense pages + thinking regularly exceed 16K; >16K streams
    )
    lines = []
    for row in reading.rows:
        values, cell_issues = {}, []
        for cell in row.cells:
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
        }
        if cell_issues:
            line["cell_issues"] = cell_issues
        lines.append(line)
    return {
        "lines": lines,
        "text": None,
        "layout": "llm_fallback",
        "note": reading.note,
    }


def needs_fallback(payload: dict | None) -> bool:
    """A table was detected but almost nothing usable was mapped."""
    if not payload or not payload.get("n_tables"):
        return False
    if payload.get("layout") in (
        "investment_list", "hcl_prose", "allocations_annex", "annex_other"
    ):
        return False  # out of nomenclator scope — side-sheet data, not repair
    if payload.get("n_numeric_cells", 999) < 10:
        return False  # nothing numeric on the page worth a paid transcription
    lines = payload.get("lines", [])
    if len(lines) < 6:
        return False  # tiny end-of-document tables aren't worth a paid call
    good = sum(1 for ln in lines if ln.get("code") and ln.get("values"))
    return good < 3
