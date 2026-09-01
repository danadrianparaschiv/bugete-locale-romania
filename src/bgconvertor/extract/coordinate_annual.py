"""Coordinate OCR candidate for Buzău's rotated InfoSoft annual tables."""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from ..layouts.common import fold, mk_line
from ..parsing import NumberParseError, normalize_indicator_code, parse_ro_number
from ..profilepdf import render_page

COLUMN_CENTERS = {
    "total_2024": 0.459,
    "credite_restante": 0.521,
    "trim1": 0.583,
    "trim2": 0.644,
    "trim3": 0.706,
    "trim4": 0.768,
    "est2025": 0.829,
    "est2026": 0.891,
    "est2027": 0.953,
}
NAME_RIGHT = 0.335
CODE_RIGHT = 0.415
VALUE_LEFT = 0.42
MAX_COLUMN_DISTANCE = 0.035
DATA_TOP = 0.15
DATA_BOTTOM = 0.94


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


def is_candidate(ocr_payload: dict) -> bool:
    text = fold(ocr_payload.get("text") or "")
    return "buget local detaliat" in text and "buzau" in text


def _canonical(raw: str) -> str | None:
    try:
        parsed = parse_ro_number(raw, ocr=True)
    except NumberParseError:
        return None
    if parsed in (None, "X"):
        return None
    return str(Decimal(parsed))


def _semantic_centers(budget_year: int) -> dict[str, float]:
    return {
        role.replace("2024", str(budget_year))
        .replace("2025", str(budget_year + 1))
        .replace("2026", str(budget_year + 2))
        .replace("2027", str(budget_year + 3)): center
        for role, center in COLUMN_CENTERS.items()
    }


def _contextualize_repeats(lines: list[dict]) -> None:
    """Keep printed TOTAL/functionare repetitions distinct and code-complete."""
    grouped: dict[tuple, list[dict]] = {}
    for line in lines:
        signature = (
            fold(line.get("name") or ""),
            tuple(
                sorted(
                    (column, Decimal(value))
                    for column, value in (line.get("values") or {}).items()
                )
            ),
        )
        grouped.setdefault(signature, []).append(line)
    for repeated in grouped.values():
        if len(repeated) != 2:
            continue
        valid_code = next(
            (
                line.get("raw_code")
                for line in repeated
                if normalize_indicator_code(line.get("raw_code"))
            ),
            None,
        )
        if valid_code:
            for line in repeated:
                if not normalize_indicator_code(line.get("raw_code")):
                    line["raw_code"] = valid_code
                    line["code"] = normalize_indicator_code(valid_code)
        repeated[0]["section"] = "TOTAL"
        repeated[1]["section"] = "FUNCTIONARE"


def map_tokens(
    tokens: list[dict],
    *,
    width: int,
    height: int,
    budget_year: int,
    page_text: str,
) -> dict:
    """Map RapidOCR boxes; exposed separately so unit tests stay offline."""
    centers = _semantic_centers(budget_year)
    items = []
    source_value_cells = 0
    for token in tokens:
        box = token["box"]
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        x = sum(xs) / len(xs) / width
        y = sum(ys) / len(ys) / height
        if not DATA_TOP <= y <= DATA_BOTTOM:
            continue
        text = str(token["text"]).strip()
        kind = None
        value = None
        if x < NAME_RIGHT:
            kind = "name"
        elif x < CODE_RIGHT:
            kind = "code"
        elif x >= VALUE_LEFT:
            role, distance = min(
                ((name, abs(x - center)) for name, center in centers.items()),
                key=lambda item: item[1],
            )
            if distance <= MAX_COLUMN_DISTANCE:
                source_value_cells += int(any(character.isdigit() for character in text))
                value = _canonical(text)
                if value is not None:
                    kind = role
        if kind:
            items.append({
                "y": sum(ys) / len(ys),
                "x": sum(xs) / len(xs),
                "kind": kind,
                "text": text,
                "value": value,
            })

    clusters = []
    for item in sorted(items, key=lambda candidate: (candidate["y"], candidate["x"])):
        if not clusters or item["y"] - clusters[-1]["mean_y"] > height * 0.012:
            clusters.append({"mean_y": item["y"], "ys": [], "items": []})
        cluster = clusters[-1]
        cluster["ys"].append(item["y"])
        cluster["mean_y"] = sum(cluster["ys"]) / len(cluster["ys"])
        cluster["items"].append(item)

    lines = []
    for cluster in clusters:
        name_parts = []
        code_parts = []
        values = {}
        for item in sorted(cluster["items"], key=lambda candidate: candidate["x"]):
            if item["kind"] == "name":
                name_parts.append(item["text"])
            elif item["kind"] == "code":
                code_parts.append(item["text"])
            else:
                values[item["kind"]] = item["value"]
        if not values:
            continue
        raw_code = " ".join(code_parts).strip()
        raw_code = re.sub(r"^\*+\s*|\s*\*+$", "", raw_code).strip()
        name = " ".join(name_parts).strip()
        if not raw_code and not name:
            continue
        lines.append(mk_line(raw_code or None, name, None, values, [], None))

    _contextualize_repeats(lines)

    mapped = sum(len(line["values"]) for line in lines)
    coded = sum(bool(line.get("code")) for line in lines if line["values"])
    return {
        "lines": lines,
        "text": page_text or None,
        "layout": "scan_buzau_infosft_coordinate",
        "rotation_applied": 270,
        "confidence_grade": None,
        "n_tables": 1 if lines else 0,
        "n_numeric_cells": source_value_cells,
        "budget_year": budget_year,
        "mapping_context": {
            "family": "buzau_infosft_coordinate",
            "budget_year": budget_year,
        },
        "mapping_stats": {
            "source_value_cells": source_value_cells,
            "mapped_value_cells": mapped,
            "coded_value_lines": coded,
            "value_lines": len(lines),
            "cell_issues": max(0, source_value_cells - mapped),
        },
    }


def extract_page(
    pdf_path: Path,
    page_no: int,
    *,
    budget_year: int,
    scale: float = 3.0,
) -> dict:
    import numpy as np

    image = render_page(pdf_path, page_no, scale=scale).rotate(270, expand=True)
    result = _engine()(np.array(image.convert("RGB")))
    texts = list(result.txts or []) if result else []
    page_text = " ".join(texts)
    folded = fold(page_text)
    if "buget local detaliat" not in folded or "buzau" not in folded:
        raise ValueError("pagina nu aparține familiei Buzău/InfoSoft așteptate")
    tokens = [
        {"box": box, "text": text, "confidence": float(confidence)}
        for box, text, confidence in zip(
            result.boxes, result.txts, result.scores, strict=True
        )
    ]
    return map_tokens(
        tokens,
        width=image.width,
        height=image.height,
        budget_year=budget_year,
        page_text=page_text,
    )
