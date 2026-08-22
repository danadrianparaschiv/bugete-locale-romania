"""Stage 0: per-page PDF census.

Answers, per page: is there a text layer, how big is it, page geometry.
This routes every later stage (digital vs OCR path) and is cheap enough
to run on whole files.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pypdfium2 as pdfium

# PDFium is not thread-safe; rendering is cheap (~100ms) vs the network
# calls that run in threads around it, so one lock costs nothing.
_render_lock = threading.Lock()
from pypdf import PdfReader


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(pdf_path).pages)


def profile_page(reader: PdfReader, page_no: int) -> dict:
    """1-based page_no -> profile payload (JSON-serializable)."""
    page = reader.pages[page_no - 1]
    text = page.extract_text() or ""
    box = page.mediabox
    return {
        "page": page_no,
        "text_chars": len(text.strip()),
        "has_text_layer": len(text.strip()) > 20,
        "width_pts": float(box.width),
        "height_pts": float(box.height),
        "pdf_rotation": page.get("/Rotate", 0) or 0,
        "sample": text.strip()[:120],
    }


def render_page(pdf_path: Path, page_no: int, scale: float = 2.0):
    """Render one 1-based page to a PIL image (used by inspect/debug/extract)."""
    with _render_lock:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            return pdf[page_no - 1].render(scale=scale).to_pil()
        finally:
            pdf.close()


def summarize(profiles: list[dict]) -> dict:
    n = len(profiles)
    with_text = sum(1 for p in profiles if p["has_text_layer"])
    return {
        "pages": n,
        "pages_with_text_layer": with_text,
        "pages_scanned": n - with_text,
        "mostly_scanned": with_text < n / 2,
    }
