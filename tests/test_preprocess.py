import pytest
from PIL import Image, ImageDraw

from bgconvertor.extract.preprocess import estimate_deskew_angle
from bgconvertor.extract.scanned import _pipeline_options, choose_best_payload


def test_small_angle_deskew_finds_the_corrective_rotation():
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    for y in range(80, 560, 35):
        draw.line((40, y, 760, y), fill="black", width=2)
    for x in (40, 300, 500, 760):
        draw.line((x, 60, x, 570), fill="black", width=2)
    skewed = image.rotate(1.5, expand=False, fillcolor="white")
    assert estimate_deskew_angle(skewed) == -1.5


@pytest.mark.parametrize("engine,expected", [
    ("auto", "OcrAutoOptions"),
    ("rapidocr", "RapidOcrOptions"),
    ("easyocr", "EasyOcrOptions"),
    ("tesseract", "TesseractOcrOptions"),
    ("tesseract_cli", "TesseractCliOcrOptions"),
])
def test_docling_options_use_the_configured_engine_languages_and_mode(engine, expected):
    options = _pipeline_options(
        cell_matching=False,
        ocr_engine=engine,
        ocr_langs=("ro", "en"),
        tableformer_mode="fast",
    )
    assert type(options.ocr_options).__name__ == expected
    assert options.ocr_options.lang == ["ro", "en"]
    assert options.ocr_options.mode.value == "full_page"
    assert options.table_structure_options.mode.value == "fast"
    assert options.table_structure_options.do_cell_matching is False


def test_candidate_selection_requires_a_structural_improvement():
    baseline = {
        "lines": [{"code": "65.02", "values": {"total": "10"}}],
        "layout": "scan_simple_table",
        "mapping_stats": {
            "source_value_cells": 1, "mapped_value_cells": 1,
            "coded_value_lines": 1, "value_lines": 1, "cell_issues": 0,
        },
    }
    weaker = {
        "lines": [], "layout": "unknown",
        "mapping_stats": {
            "source_value_cells": 1, "mapped_value_cells": 0,
            "coded_value_lines": 0, "value_lines": 0, "cell_issues": 0,
        },
    }
    winner = choose_best_payload([("ocr_baseline", baseline), ("deskew", weaker)])
    assert winner["candidate_selection"]["selected"] == "ocr_baseline"
