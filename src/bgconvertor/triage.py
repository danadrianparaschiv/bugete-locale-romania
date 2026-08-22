"""Pre-flight triage: what is this file, what will it cost, will it work?

Profiles every page, samples a handful through the real extraction stack
(the samples land in the run store, so the actual run reuses them), and
reports: layout families found, unknown-layout warnings, scan quality,
time and LLM-cost estimates, and a recommended command.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from .config import RunConfig
from .runstore import RunStore

log = logging.getLogger("bgc.triage")

SCAN_SAMPLES = 5
KNOWN_GOOD_LAYOUTS = {
    "digital_detail", "scan_simple_table", "scan_detail_economic",
    "scan_general_matrix", "scan_transposed_detail", "hcl_prose",
    "investment_list",
}


def run_triage(config: RunConfig, store: RunStore, pdf: Path) -> dict:
    from pypdf import PdfReader

    from . import profilepdf
    from .llm.fallback import needs_fallback

    reader = PdfReader(pdf)
    n = len(reader.pages)
    pages = list(range(1, n + 1))
    for p in pages:
        if store.get("profile", p) is None:
            store.put("profile", p, profilepdf.profile_page(reader, p))
    digital = [p for p in pages if (store.get("profile", p) or {}).get("has_text_layer")]
    scanned = [p for p in pages if p not in set(digital)]

    # digital: does the real grid extractor accept a sample page?
    digital_status = None
    if digital:
        import pdfplumber

        from .extract import digital as dig

        sample = digital[min(1, len(digital) - 1)]
        with pdfplumber.open(pdf) as plumber:
            try:
                dig.extract_page(plumber.pages[sample - 1])
                digital_status = "grid_ok"
            except Exception as exc:
                digital_status = f"no_grid ({str(exc)[:60]}) — va trece prin OCR"
                scanned = sorted(set(scanned) | set(digital))

    # scanned: run a stratified sample through the real stack (cached for later)
    sample_pages = (
        [scanned[round(i * (len(scanned) - 1) / (SCAN_SAMPLES - 1))] for i in range(SCAN_SAMPLES)]
        if len(scanned) >= SCAN_SAMPLES else list(scanned)
    )
    sample_pages = sorted(set(sample_pages))
    layouts: Counter = Counter()
    grades: Counter = Counter()
    fallback_hits = 0
    if sample_pages:
        from .extract import orient, scanned as sc

        for p in sample_pages:
            if store.get("orient", p) is None:
                store.put("orient", p, orient.detect(profilepdf.render_page(pdf, p, scale=0.7)))
            if store.get("ocr", p) is None:
                store.put("ocr", p, sc.ocr_page(
                    pdf, p,
                    rotation=(store.get("orient", p) or {}).get("rotation", 0),
                    scale=config.render_scale,
                    cell_matching=config.docling_cell_matching,
                ))
            if store.get("extract", p) is None:
                store.put("extract", p, sc.map_payload(store.get("ocr", p) or {}))
            pl = store.get("extract", p) or {}
            layouts[pl.get("layout") or "unknown"] += 1
            grades[pl.get("confidence_grade") or "?"] += 1
            if needs_fallback(pl):
                fallback_hits += 1

    # extrapolations
    ocr_todo = len([p for p in scanned if store.get("ocr", p) is None])
    est_minutes = ocr_todo * 9.0 / 60
    est_fallback_pages = (
        round(fallback_hits / max(1, len(sample_pages)) * len(scanned)) if sample_pages else 0
    )
    est_llm_cost = est_fallback_pages * 0.13 + 0.5  # + repair floor
    unknown = [l for l in layouts if l not in KNOWN_GOOD_LAYOUTS]

    result = {
        "pdf": pdf.name,
        "pages": n,
        "digital_pages": len(digital),
        "digital_status": digital_status,
        "scanned_pages": len(scanned),
        "sampled": sample_pages,
        "layouts_sampled": dict(layouts),
        "ocr_grades": dict(grades),
        "unknown_layouts": unknown,
        "est_extraction_minutes": round(est_minutes, 1),
        "est_fallback_pages": est_fallback_pages,
        "est_llm_cost_usd": round(est_llm_cost, 2),
        "recommended": _recommend(pdf, len(scanned), est_llm_cost),
    }
    import json

    (store.root / "triage.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _recommend(pdf: Path, n_scanned: int, est_cost: float) -> str:
    parts = [f"bgconvertor convert {pdf.name}"]
    if n_scanned > 40:
        parts.append("--workers 4")
    parts.append(f"--llm repair --max-llm-cost {max(1.0, round(est_cost * 1.5, 0)):.2f}")
    return " ".join(parts)
