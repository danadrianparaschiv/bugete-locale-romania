"""Scanned-page extraction: rotation fix -> docling -> extraction contract.

ocr_page (expensive) stores raw material: text grids, page text, confidence.
map_payload (cheap) turns stored grids into contract lines via the layout
registry (bgconvertor.layouts) and classifies the page's layout family.
"""

from __future__ import annotations

import io
import logging
import re
from functools import lru_cache
from pathlib import Path

from ..layouts import map_grid
from ..layouts.common import fold, is_code_cell, split_header

log = logging.getLogger("bgc.extract.scanned")

# re-exported for tests and callers
map_table = map_grid


@lru_cache(maxsize=2)
def _converter(cell_matching: bool = True):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, ImageFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = cell_matching
    return DocumentConverter(
        format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)}
    )


@lru_cache(maxsize=1)
def _native_converter():
    """PDF-input pipeline that trusts the embedded text layer (no OCR pass)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def ocr_page_native(pdf_path: Path, page_no: int) -> dict:
    """Copier-PDF path: TableFormer over the embedded text layer.

    ~3x faster than render+OCR (and needs no orientation pass — the text
    layer carries its own coordinates). Parse-quality parity measured on
    Bacau; the validator+repair treat both sources identically.
    """
    result = _native_converter().convert(pdf_path, page_range=(page_no, page_no))
    doc = result.document
    tables_raw = [
        [[(c.text or "") for c in row] for row in t.data.grid] for t in doc.tables
    ]
    text = " ".join(t.text for t in doc.texts if t.text)
    try:
        grade = result.confidence.mean_grade.name
    except Exception:
        grade = None
    return {
        "tables_raw": tables_raw,
        "tables_rows_y": _rows_y(doc),
        "text": text or None,
        "rotation_applied": 0,
        "confidence_grade": grade,
        "native_text": True,
    }


def ocr_page(
    pdf_path: Path, page_no: int, rotation: int, scale: float = 2.0,
    cell_matching: bool = True, stamp_filter: bool = False,
) -> dict:
    """Expensive stage: render + rotate + docling OCR/TableFormer."""
    from docling.datamodel.base_models import DocumentStream

    from ..profilepdf import render_page

    img = render_page(pdf_path, page_no, scale=scale)
    if rotation:
        img = img.rotate(rotation, expand=True)
    if stamp_filter:
        from .preprocess import remove_stamps

        img = remove_stamps(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    stream = DocumentStream(name=f"{pdf_path.stem}_p{page_no:04d}.png", stream=buf)

    result = _converter(cell_matching).convert(stream)
    doc = result.document

    tables_raw = [
        [[(c.text or "") for c in row] for row in t.data.grid] for t in doc.tables
    ]
    text = " ".join(t.text for t in doc.texts if t.text)
    try:
        grade = result.confidence.mean_grade.name
    except Exception:
        grade = None

    return {
        "tables_raw": tables_raw,
        "tables_rows_y": _rows_y(doc),
        "text": text or None,
        "rotation_applied": rotation,
        "confidence_grade": grade,
    }


def _rows_y(doc) -> list[list[list[float]]]:
    """Per table, per grid row: [y0, y1] as fractions of page height.

    Fractions are scale-independent, so repair can crop any render of the
    page to a sum-group's rows (5-10x cheaper vision calls).
    """
    try:
        page = next(iter(doc.pages.values()))
        height = float(page.size.height)
    except Exception:
        return []
    out = []
    for t in doc.tables:
        rows = []
        for row in t.data.grid:
            tops = [c.bbox.t for c in row if getattr(c, "bbox", None)]
            bots = [c.bbox.b for c in row if getattr(c, "bbox", None)]
            rows.append(
                [round(min(tops) / height, 4), round(max(bots) / height, 4)]
                if tops else [0.0, 1.0]
            )
        out.append(rows)
    return out


def map_payload(ocr_payload: dict) -> dict:
    """Cheap stage: stored OCR grids -> extraction-contract payload."""
    lines: list[dict] = []
    header_texts: list[str] = []
    for grid in ocr_payload.get("tables_raw", []):
        lines.extend(map_grid(grid))
        header_rows, _ = split_header(grid)
        seen: set[str] = set()
        for r in header_rows:
            for cell in grid[r]:
                if cell.strip() and cell not in seen:
                    seen.add(cell)
                    header_texts.append(cell.strip())
    text = " ".join(filter(None, [ocr_payload.get("text"), *header_texts])) or None
    # classification sees the first grid rows even when header detection
    # missed them (procurement lists have unrecognized header vocabulary)
    cls_text = " ".join(
        filter(None, [text] + [
            c for grid in ocr_payload.get("tables_raw", [])
            for row in grid[:3] for c in row
        ])
    )
    n_numeric = sum(
        1
        for grid in ocr_payload.get("tables_raw", [])
        for row in grid
        for c in row
        if sum(ch.isdigit() for ch in c) >= 3
    )
    return {
        "lines": lines,
        "text": text,
        "layout": _guess_layout(lines, cls_text),
        "rotation_applied": ocr_payload.get("rotation_applied", 0),
        "confidence_grade": ocr_payload.get("confidence_grade"),
        "n_tables": len(ocr_payload.get("tables_raw", [])),
        "n_numeric_cells": n_numeric,
    }


# -- page-level layout classification ---------------------------------------

INVEST_HINT = re.compile(
    r"valoare actualizata|executat la|rest de executat|credite de angajament|"
    r"surse de finantare|denumire.{0,20}obiectiv|neetichetat|nr\.? si data|"
    r"pret unitar|nr\.?\s*buc|capitol bugetar|studiu de fezabilitate|"
    r"cheltuieli efectuate|achizitie directa|procedura de achizitie|cod cpv"
)
ALLOC_HINT = re.compile(
    r"unitati administrativ|repartizarea pe comune|pe localitati|"
    r"fondul de salarii|numarul de personal"
)
INVEST_TAG = re.compile(r"^-?\s*(verde|maro|mixt|neutru|neetichetat)\b")


def _guess_layout(lines: list[dict], text: str) -> str:
    if INVEST_HINT.search(fold(text or "")):
        return "investment_list"
    # investment objective pages tag rows verde/maro/mixt/neutru
    tags = sum(1 for ln in lines if INVEST_TAG.match(fold(ln.get("name") or "")))
    if tags >= 3:
        return "investment_list"
    cols = {c for ln in lines for c in ln["values"]}
    if not lines:
        t = fold(text or "")
        if "hotarare" in t or "consiliul local" in t:
            return "hcl_prose"
        return "unknown"
    if any(
        re.search(r"\b(?:liceul|colegiul|scoala|gradinita)\b", fold(ln.get("section") or ""))
        for ln in lines
    ):
        return "scan_institution_budget"
    if ALLOC_HINT.search(fold(text or "")):
        return "allocations_annex"
    if "total_general" in cols:
        return "scan_general_matrix"
    if "influente" in cols:
        return "scan_rectification_detail"
    if {"trim1", "trim2", "trim3", "trim4"} <= cols and "total" in cols:
        return "scan_transposed_detail"
    if "credite_restante" in cols:
        return "scan_detail_economic"
    if {"est2027", "est2028", "est2029"} & cols:
        numbered = sum(ln.get("row_no") is not None for ln in lines)
        revenue_like = sum(
            1
            for ln in lines
            if ln.get("code")
            and ln["code"].split(".", 1)[0].isdigit()
            and int(ln["code"].split(".", 1)[0]) <= 48
        )
        code_count = sum(bool(ln.get("code")) for ln in lines)
        if numbered >= len(lines) / 2 and revenue_like >= code_count * 0.8:
            return "scan_revenue_detail"
        return "scan_simple_table"
    # a table with data but essentially no indicator codes is an annex
    # (procurement lists, per-institution allocations, personnel tables) —
    # kept for side sheets, out of nomenclator scope. Codes present in the
    # RAW rows but unmapped mean an unknown coding scheme, not an annex.
    coded = sum(1 for ln in lines if ln.get("code"))
    raw_codeish = sum(
        1 for ln in lines
        if ln.get("raw_code") or is_code_cell((ln.get("name") or "")[:12])
    )
    if len(lines) >= 5 and coded < 2 and raw_codeish < 3:
        return "annex_other"
    return "scan_table_other"
