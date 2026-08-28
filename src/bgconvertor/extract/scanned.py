"""Scanned-page extraction: rotation fix -> docling -> extraction contract.

ocr_page (expensive) stores raw material: text grids, page text, confidence.
map_payload (cheap) turns stored grids into contract lines via the layout
registry (bgconvertor.layouts) and classifies the page's layout family.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from ..layouts import map_grid, map_grid_with_context
from ..layouts.common import fold, is_code_cell, split_header
from ..years import infer_budget_year, remap_lines

log = logging.getLogger("bgc.extract.scanned")

# re-exported for tests and callers
map_table = map_grid


PROSE_FUNCTIONAL_CHAPTERS = {
    "autoritati publice si actiuni externe": ("51.02", "Autorități publice și acțiuni externe"),
    "tranzactii privind datoria publica si imprumuturi": (
        "55.02",
        "Tranzacții privind datoria publică și împrumuturi",
    ),
    "ordine publica si siguranta nationala": (
        "61.02",
        "Ordine publică și siguranță națională",
    ),
    "invatamant": ("65.02", "Învățământ"),
    "sanatate": ("66.02", "Sănătate"),
    "cultura, recreere, religie": ("67.02", "Cultură, recreere și religie"),
    "asigurari si asistenta sociala": ("68.02", "Asigurări și asistență socială"),
    "locuinte, servicii si dezvoltare publica": (
        "70.02",
        "Locuințe, servicii și dezvoltare publică",
    ),
    "protectia mediului": ("74.02", "Protecția mediului"),
    "combustibil si energie": ("81.02", "Combustibili și energie"),
    "transporturi": ("84.02", "Transporturi"),
}


def _fold_prose(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    ).lower()


def _prose_number(raw: str) -> str:
    from ..parsing import parse_ro_number

    return str(parse_ro_number(raw, ocr=False))


def map_prose_budget_summary(text: str, budget_year: int) -> list[dict]:
    """Map explicit facts in an official Romanian budget prose summary.

    Some HCL publications omit their machine-readable annexes but reproduce
    the approved local-budget totals and functional chapters in the decision
    report.  This deliberately narrow parser accepts only the conventional
    ``in suma de ... mii lei, din care`` sentence and known MF functional
    chapter names.  It never derives the unlisted residual between the
    printed grand total and the printed chapters.
    """
    folded = re.sub(r"\s+", " ", _fold_prose(text or "")).strip()
    if not folded:
        return []

    column = f"total_{budget_year}"
    lines: list[dict] = []

    revenue = re.search(
        rf"veniturile bugetului local pentru anul {budget_year} sunt in "
        r"valoare de (?P<value>[\d.]+(?:,\d+)?) mii lei",
        folded,
    )
    if revenue:
        lines.append({
            "raw_code": "000102",
            "code": "00.01.02",
            "func_code": None,
            "name": "TOTAL VENITURI",
            "row_no": None,
            "section": "TOTAL",
            "year": budget_year,
            "values": {column: _prose_number(revenue.group("value"))},
            "source": "official_prose",
        })

    expense = re.search(
        rf"cheltuielile bugetului local pe anul {budget_year}, in valoare de "
        r"(?P<value>[\d.]+(?:,\d+)?) mii lei",
        folded,
    )
    if expense:
        lines.append({
            "raw_code": "4902",
            "code": "49.02",
            "func_code": None,
            "name": "TOTAL CHELTUIELI",
            "row_no": None,
            "section": "TOTAL",
            "year": budget_year,
            "values": {column: _prose_number(expense.group("value"))},
            "source": "official_prose",
        })

    number = r"[\d.]+(?:,\d+)?"
    for label, (code, official_name) in PROSE_FUNCTIONAL_CHAPTERS.items():
        match = re.search(
            rf"{re.escape(label)} in suma de (?P<total>{number}) mii lei, "
            rf"din care: sectiunea functionare: (?P<functionare>{number}) mii lei; "
            rf"sectiunea dezvoltare: (?P<dezvoltare>{number}) mii lei",
            folded,
        )
        if not match:
            continue
        raw_code = code.replace(".", "")
        for section, group in (
            ("TOTAL", "total"),
            ("FUNCTIONARE", "functionare"),
            ("DEZVOLTARE", "dezvoltare"),
        ):
            lines.append({
                "raw_code": raw_code,
                "code": code,
                "func_code": None,
                "name": official_name,
                "row_no": None,
                "section": section,
                "year": budget_year,
                "values": {column: _prose_number(match.group(group))},
                "source": "official_prose",
            })
    return lines


@lru_cache(maxsize=16)
def _converter(
    cell_matching: bool = True,
    ocr_engine: str = "rapidocr",
    ocr_langs: tuple[str, ...] = ("ro", "en"),
    tableformer_mode: str = "accurate",
):
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, ImageFormatOption

    opts = _pipeline_options(cell_matching, ocr_engine, ocr_langs, tableformer_mode)
    return DocumentConverter(
        format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)}
    )


def _pipeline_options(
    cell_matching: bool = True,
    ocr_engine: str = "rapidocr",
    ocr_langs: tuple[str, ...] = ("ro", "en"),
    tableformer_mode: str = "accurate",
):
    """Build Docling options from RunConfig; kept separate for offline tests."""
    from docling.datamodel import pipeline_options as po

    ocr_classes = {
        "auto": po.OcrAutoOptions,
        "rapidocr": po.RapidOcrOptions,
        "easyocr": po.EasyOcrOptions,
        "tesseract": po.TesseractOcrOptions,
        "tesseract_cli": po.TesseractCliOcrOptions,
    }
    if ocr_engine not in ocr_classes:
        raise ValueError(f"unsupported OCR engine: {ocr_engine}")
    opts = po.PdfPipelineOptions()
    opts.do_ocr = True
    opts.ocr_options = ocr_classes[ocr_engine](
        lang=list(ocr_langs), mode=po.OcrMode.FULL_PAGE
    )
    opts.do_table_structure = True
    opts.table_structure_options.mode = po.TableFormerMode(tableformer_mode)
    opts.table_structure_options.do_cell_matching = cell_matching
    return opts


@lru_cache(maxsize=4)
def _native_converter(
    cell_matching: bool = True, tableformer_mode: str = "accurate"
):
    """PDF-input pipeline that trusts the embedded text layer (no OCR pass)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode(tableformer_mode)
    opts.table_structure_options.do_cell_matching = cell_matching
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def ocr_page_native(
    pdf_path: Path,
    page_no: int,
    cell_matching: bool = True,
    tableformer_mode: str = "accurate",
) -> dict:
    """Copier-PDF path: TableFormer over the embedded text layer.

    ~3x faster than render+OCR (and needs no orientation pass — the text
    layer carries its own coordinates). Parse-quality parity measured on
    Bacau; the validator+repair treat both sources identically.
    """
    result = _native_converter(cell_matching, tableformer_mode).convert(
        pdf_path, page_range=(page_no, page_no)
    )
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
    ocr_engine: str = "rapidocr", ocr_langs: tuple[str, ...] = ("ro", "en"),
    tableformer_mode: str = "accurate",
    adaptive_preprocessing: bool = False,
    max_deskew_degrees: float = 2.0,
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
    preprocessing = {
        "stamp_removed": bool(stamp_filter),
        "colored_ink_fraction": None,
        "deskew_angle": 0.0,
    }
    if adaptive_preprocessing:
        from .preprocess import adaptive_preprocess

        img, preprocessing = adaptive_preprocess(
            img, max_deskew_degrees=max_deskew_degrees
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    stream = DocumentStream(name=f"{pdf_path.stem}_p{page_no:04d}.png", stream=buf)

    result = _converter(
        cell_matching, ocr_engine, tuple(ocr_langs), tableformer_mode
    ).convert(stream)
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
        "ocr_engine": ocr_engine,
        "ocr_langs": list(ocr_langs),
        "tableformer_mode": tableformer_mode,
        "preprocessing": preprocessing,
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


def map_payload(
    ocr_payload: dict,
    budget_year: int | None = None,
    context: dict | None = None,
) -> dict:
    """Cheap stage: stored OCR grids -> extraction-contract payload."""
    lines: list[dict] = []
    header_texts: list[str] = []
    mapping_context = context if ocr_payload.get("tables_raw") else None
    source_value_cells = 0
    for grid in ocr_payload.get("tables_raw", []):
        mapped, mapped_context = map_grid_with_context(
            grid, context=mapping_context, budget_year=budget_year
        )
        lines.extend(mapped)
        mapping_context = mapped_context or mapping_context
        if mapped_context:
            columns = {
                int(index): role
                for index, role in (mapped_context.get("columns") or {}).items()
            }
            _, first_data = split_header(grid)
            value_columns = {
                index
                for index, role in columns.items()
                if role not in ("name", "code", "func_code", "rowno", "ignore")
            }
            source_value_cells += sum(
                1
                for row in grid[first_data:]
                for index in value_columns
                if index < len(row)
                and row[index].strip()
                and (
                    any(character.isdigit() for character in row[index])
                    or fold(row[index]).strip() == "x"
                )
            )
        header_rows, _ = split_header(grid)
        seen: set[str] = set()
        for r in header_rows:
            for cell in grid[r]:
                if cell.strip() and cell not in seen:
                    seen.add(cell)
                    header_texts.append(cell.strip())
    text = " ".join(filter(None, [ocr_payload.get("text"), *header_texts])) or None
    prose_lines = (
        map_prose_budget_summary(text, budget_year)
        if text and budget_year and not lines and not ocr_payload.get("tables_raw")
        else []
    )
    if prose_lines:
        lines = prose_lines
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
    resolved_year = budget_year or infer_budget_year(" ".join(header_texts))
    remap_lines(lines, resolved_year)
    mapped_value_cells = sum(
        len(line.get("values") or {}) + len(line.get("cell_issues") or [])
        for line in lines
    )
    if prose_lines:
        source_value_cells = mapped_value_cells
    return {
        "lines": lines,
        "text": text,
        "layout": (
            "official_prose_summary"
            if prose_lines else _guess_layout(lines, cls_text, mapping_context)
        ),
        "rotation_applied": ocr_payload.get("rotation_applied", 0),
        "confidence_grade": ocr_payload.get("confidence_grade"),
        "n_tables": len(ocr_payload.get("tables_raw", [])),
        "n_numeric_cells": n_numeric,
        "budget_year": resolved_year,
        "mapping_context": mapping_context,
        "mapping_stats": {
            "source_value_cells": source_value_cells,
            "mapped_value_cells": mapped_value_cells,
            "coded_value_lines": sum(
                1 for line in lines if line.get("code") and line.get("values")
            ),
            "value_lines": sum(1 for line in lines if line.get("values")),
            "cell_issues": sum(len(line.get("cell_issues") or []) for line in lines),
        },
    }


def structural_score(payload: dict) -> float:
    """Page-local score used only to decide whether a second OCR pass is useful."""
    stats = payload.get("mapping_stats") or {}
    expected = int(stats.get("source_value_cells") or 0)
    mapped = int(stats.get("mapped_value_cells") or 0)
    value_lines = int(stats.get("value_lines") or 0)
    coded = int(stats.get("coded_value_lines") or 0)
    issues = int(stats.get("cell_issues") or 0)
    layout = payload.get("layout") or "unknown"
    if expected == 0:
        if int(payload.get("n_numeric_cells") or 0) <= 1:
            return 1.0
        if layout == "hcl_prose" or (payload.get("text") and not payload.get("n_tables")):
            return 1.0
        return 0.0 if not payload.get("lines") else 0.7
    coverage = min(1.0, mapped / expected)
    identity = coded / value_lines if value_lines else 0.0
    if layout in (
        "investment_list", "allocations_annex", "annex_other",
        "scan_annual_total", "scan_general_matrix",
    ):
        identity = 1.0
    hygiene = max(0.0, 1.0 - issues / expected)
    score = 0.7 * coverage + 0.2 * identity + 0.1 * hygiene
    if layout == "unknown":
        score *= 0.75
    return round(score, 4)


def choose_best_payload(candidates: list[tuple[str, dict]]) -> dict:
    """Select a deterministic/native OCR candidate by mapped-cell quality."""
    if not candidates:
        raise ValueError("at least one OCR candidate is required")
    # A genuinely blank/prose page scores 1.0 because no table extraction is
    # required.  That same score must not let an empty adaptive OCR candidate
    # defeat a baseline that mapped real budget cells on the *same* page.
    productive = [
        candidate for candidate in candidates
        if (candidate[1].get("lines") or int(
            (candidate[1].get("mapping_stats") or {}).get("mapped_value_cells") or 0
        ))
    ]
    ranked_candidates = productive or candidates
    ranked = [
        (
            structural_score(payload),
            int((payload.get("mapping_stats") or {}).get("mapped_value_cells") or 0),
            -index,
            name,
            payload,
        )
        for index, (name, payload) in enumerate(ranked_candidates)
    ]
    score, _, _, name, winner = max(ranked)
    winner["candidate_selection"] = {
        "selected": name,
        "score": score,
        "candidates": [
            {"name": candidate_name, "score": structural_score(candidate_payload)}
            for candidate_name, candidate_payload in candidates
        ],
    }
    return winner


# -- page-level layout classification ---------------------------------------

INVEST_HINT = re.compile(
    r"valoare actualizata|executat la|rest de executat|"
    r"surse de finantare|denumire.{0,20}obiectiv|neetichetat|nr\.? si data|"
    r"pret unitar|nr\.?\s*buc|capitol bugetar|studiu de fezabilitate|"
    r"cheltuieli efectuate|achizitie directa|procedura de achizitie|cod cpv|"
    r"n[aeo]mi\w*.{0,20}(?:obiect|ebiact|osiect).{0,30}(?:invest|imvest|invet|irvoet)"
)
COMMITMENT_CREDIT_HINT = re.compile(r"credite de angajament")
ALLOC_HINT = re.compile(
    r"unitati administrativ|repartizarea pe comune|pe localitati|"
    r"fondul de salarii|numarul de personal|finantare\s*burse|"
    r"unitatea d[eo].{0,15}invatamant.{0,80}(?:burse|buget|bufet)|"
    r"(?:unitat|unltat).{0,100}(?:particular|confesional).{0,80}(?:burse|buget|bufet)"
)
INVEST_TAG = re.compile(r"^-?\s*(verde|maro|mixt|neutru|neetichetat)\b")
BUDGET_GRID_HINT = re.compile(
    r"cod\s*(?:ul\s*)?indicator|prevederi\s*(?:anuale|trimestriale)|"
    r"\btrim\.?\s*(?:i|ii|iii|iv|1|2|3|4)\b|estimari|"
    r"buget\w*\s+(?:local\w*\s+)?detaliat"
)


def _guess_layout(lines: list[dict], text: str, context: dict | None = None) -> str:
    folded_text = fold(text or "")
    # Ordinary budget forms may include a "Credite de angajament" column.
    # Explicit budget-grid vocabulary (especially Cod indicator + DETALIAT)
    # is stronger evidence than that single investment-list phrase.
    if INVEST_HINT.search(folded_text):
        return "investment_list"
    if (
        COMMITMENT_CREDIT_HINT.search(folded_text)
        and not BUDGET_GRID_HINT.search(folded_text)
    ):
        return "investment_list"
    if (
        context
        and int(context.get("n_cols") or 0) >= 12
        and not BUDGET_GRID_HINT.search(folded_text)
    ):
        # Budget nomenclator tables top out at ten columns. Cluj's 15-column
        # programme pages are investment side sheets even when OCR destroys
        # every recognisable word in the heading.  Some ordinary forms do
        # have 12 columns, however, so explicit budget-table vocabulary wins.
        return "investment_list"
    if context and context.get("family") == "annual_total":
        return "scan_annual_total"
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
