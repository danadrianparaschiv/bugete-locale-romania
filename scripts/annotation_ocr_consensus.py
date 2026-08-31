"""Verify an independent vision draft with coordinate-aware local OCR.

Only the source PDF and the draft are read.  Converter workbooks, run-store
artifacts, analysis bundles, and annotation suggestions are deliberately out
of scope.  OCR evidence is cached below the requested output directory so a
reviewer can reproduce every disagreement without another OCR pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
from rapidocr import RapidOCR

from bgconvertor.orchestrator import parse_pages
from bgconvertor.parsing import NumberParseError, parse_ro_number
from bgconvertor.profilepdf import page_count, render_page


@dataclass(frozen=True)
class OcrCell:
    column: str
    printed: str
    value: str
    confidence: float
    box: list[list[float]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(raw: str | None) -> str | None:
    if raw is None or raw.strip().lower() in {"", "null"}:
        return None
    # RapidOCR sometimes joins the numeric total with an adjacent narrow
    # applicability-marker column (for example ``250 x``).  The marker is not
    # part of the number and is excluded from cell ground truth.
    raw = re.sub(r"(?i)(?:^|\s)x(?:\s|$)", " ", raw).strip()
    raw = re.sub(r"(?i)x$", "", raw).strip()
    if not raw:
        return None
    try:
        parsed = parse_ro_number(raw, ocr=True)
    except NumberParseError:
        return None
    if parsed in (None, "X"):
        return None
    value = Decimal(parsed)
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _canonical_column(raw: str) -> str:
    """Normalize printed Romanian headers emitted by a vision provider."""
    compact = re.sub(r"[^a-z0-9]", "", raw.lower())
    aliases = {
        "total2024": "total_2024",
        "dincarecreditebugetaredestinatestingeriiplatilorrestante": (
            "credite_restante"
        ),
        "trimi": "trim1",
        "trimii": "trim2",
        "trimiii": "trim3",
        "trimiv": "trim4",
        "2025": "est2025",
        "2026": "est2026",
        "2027": "est2027",
        "est2027consolidated": "est2027",
    }
    return aliases.get(compact, raw)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_draft_page(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for row in payload["reading"]["rows"]:
        values = {
            _canonical_column(str(cell["column"])): value
            for cell in row.get("cells") or []
            if (value := _canonical(cell.get("value"))) is not None
        }
        if values:
            rows.append(values)
    return rows


def _cluster_rows(cells: list[tuple[float, OcrCell]], tolerance: float) -> list[dict]:
    clusters: list[dict] = []
    for y, cell in sorted(cells, key=lambda item: (item[0], item[1].column)):
        if not clusters or y - clusters[-1]["mean_y"] > tolerance:
            clusters.append({"mean_y": y, "ys": [y], "cells": []})
        cluster = clusters[-1]
        cluster["ys"].append(y)
        cluster["mean_y"] = sum(cluster["ys"]) / len(cluster["ys"])
        cluster["cells"].append(cell)
    rows = []
    for cluster in clusters:
        by_column: dict[str, OcrCell] = {}
        for cell in sorted(cluster["cells"], key=lambda item: item.confidence):
            by_column[cell.column] = cell
        rows.append({
            "y": round(cluster["mean_y"], 2),
            "values": {column: cell.value for column, cell in by_column.items()},
            "cells": {
                column: {
                    "printed": cell.printed,
                    "value": cell.value,
                    "confidence": round(cell.confidence, 4),
                    "box": cell.box,
                }
                for column, cell in by_column.items()
            },
        })
    return rows


def _ocr_page(
    engine: RapidOCR,
    source: Path,
    page: int,
    *,
    scale: float,
    rotation: int,
    centers: dict[str, float],
    max_distance: float,
    top: float,
    bottom: float,
) -> dict:
    image = render_page(source, page, scale=scale)
    if rotation:
        image = image.rotate(rotation, expand=True)
    width, height = image.size
    result = engine(np.array(image.convert("RGB")))
    cells: list[tuple[float, OcrCell]] = []
    if result and result.txts is not None:
        for box, printed, confidence in zip(
            result.boxes, result.txts, result.scores, strict=True
        ):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x = sum(xs) / len(xs) / width
            y = sum(ys) / len(ys) / height
            if not top <= y <= bottom:
                continue
            column, distance = min(
                ((name, abs(x - center)) for name, center in centers.items()),
                key=lambda item: item[1],
            )
            if distance > max_distance:
                continue
            value = _canonical(str(printed))
            if value is None:
                continue
            cells.append((
                sum(ys) / len(ys),
                OcrCell(
                    column=column,
                    printed=str(printed),
                    value=value,
                    confidence=float(confidence),
                    box=[[round(float(v), 2) for v in point] for point in box],
                ),
            ))
    return {
        "page": page,
        "width": width,
        "height": height,
        "rows": _cluster_rows(cells, tolerance=height * 0.012),
    }


def _align(draft: list[dict[str, str]], ocr: list[dict]) -> list[tuple[int, int]]:
    """Maximise exact cell agreements while preserving printed row order."""
    rows, columns = len(draft), len(ocr)
    dp = [[0] * (columns + 1) for _ in range(rows + 1)]
    decision = [["skip_ocr"] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        decision[row][0] = "skip_draft"
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            exact = sum(
                value == ocr[column - 1]["values"].get(name)
                for name, value in draft[row - 1].items()
            )
            options = [
                (dp[row - 1][column], "skip_draft"),
                (dp[row][column - 1], "skip_ocr"),
            ]
            if exact:
                options.append((dp[row - 1][column - 1] + exact, "align"))
            dp[row][column], decision[row][column] = max(
                options, key=lambda item: (item[0], item[1] == "align")
            )
    aligned = []
    row, column = rows, columns
    while row or column:
        action = decision[row][column]
        if row and column and action == "align":
            aligned.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif row and (not column or action == "skip_draft"):
            row -= 1
        else:
            column -= 1
    return list(reversed(aligned))


def _compare_page(draft: list[dict[str, str]], ocr: list[dict], page: int) -> dict:
    alignment = _align(draft, ocr)
    by_draft = {draft_index: ocr_index for draft_index, ocr_index in alignment}
    totals = Counter()
    discrepancies = []
    extra_observations = []
    for draft_index, row in enumerate(draft):
        ocr_index = by_draft.get(draft_index)
        observed = ocr[ocr_index]["values"] if ocr_index is not None else {}
        for column, expected in row.items():
            actual = observed.get(column)
            if actual == expected:
                totals["confirmed"] += 1
            elif actual is None:
                totals["uncovered"] += 1
                discrepancies.append({
                    "page": page,
                    "draft_row": draft_index + 1,
                    "ocr_row": ocr_index + 1 if ocr_index is not None else None,
                    "column": column,
                    "draft_value": expected,
                    "ocr_value": None,
                    "kind": "uncovered",
                })
            else:
                totals["conflicting"] += 1
                cell = ocr[ocr_index]["cells"][column]
                discrepancies.append({
                    "page": page,
                    "draft_row": draft_index + 1,
                    "ocr_row": ocr_index + 1,
                    "column": column,
                    "draft_value": expected,
                    "ocr_value": actual,
                    "ocr_printed": cell["printed"],
                    "ocr_confidence": cell["confidence"],
                    "ocr_box": cell["box"],
                    "kind": "conflict",
                })
        if ocr_index is not None:
            for column, actual in observed.items():
                if column in row:
                    continue
                cell = ocr[ocr_index]["cells"][column]
                extra_observations.append({
                    "page": page,
                    "draft_row": draft_index + 1,
                    "ocr_row": ocr_index + 1,
                    "column": column,
                    "ocr_value": actual,
                    "ocr_printed": cell["printed"],
                    "ocr_confidence": cell["confidence"],
                    "ocr_box": cell["box"],
                })
    expected = sum(totals.values())
    return {
        "page": page,
        "draft_rows": len(draft),
        "ocr_rows": len(ocr),
        "aligned_rows": len(alignment),
        "expected": expected,
        "confirmed": totals["confirmed"],
        "conflicting": totals["conflicting"],
        "uncovered": totals["uncovered"],
        "confirmed_pct": round(100 * totals["confirmed"] / expected, 2) if expected else 100.0,
        "discrepancies": discrepancies,
        "extra_observations": extra_observations,
    }


def _parse_centers(raw: str) -> dict[str, float]:
    centers = {}
    for item in raw.split(","):
        name, separator, value = item.partition("=")
        if not separator:
            raise argparse.ArgumentTypeError(f"centru invalid: {item!r}")
        centers[name.strip()] = float(value)
    if not centers:
        raise argparse.ArgumentTypeError("cel puțin un centru este obligatoriu")
    return centers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("draft", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--centers", type=_parse_centers, required=True)
    parser.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0)
    parser.add_argument("--scale", type=float, default=3.0)
    parser.add_argument("--max-distance", type=float, default=0.035)
    parser.add_argument("--top", type=float, default=0.15)
    parser.add_argument("--bottom", type=float, default=0.94)
    parser.add_argument("--pages", help="Selecție de pagini, de exemplu 1-4,9")
    args = parser.parse_args()

    source = args.source.resolve()
    source_hash = _sha256(source)
    output = args.out.resolve()
    evidence = output.parent / f"{output.stem}-ocr-evidence"
    engine = RapidOCR(params={"Global.log_level": "warning"})
    reports = []
    totals = Counter()
    selected_pages = parse_pages(args.pages, page_count(source))
    for page in selected_pages:
        draft_path = args.draft / "pages" / f"p{page:04d}.json"
        if not draft_path.exists():
            raise SystemExit(f"draft page missing: {draft_path}")
        evidence_path = evidence / f"p{page:04d}.json"
        if evidence_path.exists():
            ocr_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            expected_evidence = {
                "source_sha256": source_hash,
                "rotation": args.rotation,
                "render_scale": args.scale,
                "column_centers": args.centers,
            }
            if any(
                ocr_payload.get(key) != value
                for key, value in expected_evidence.items()
            ):
                raise SystemExit(f"stale OCR evidence: {evidence_path}")
        else:
            ocr_payload = {
                "schema_version": 1,
                "source_sha256": source_hash,
                "rotation": args.rotation,
                "render_scale": args.scale,
                "column_centers": args.centers,
                **_ocr_page(
                    engine, source, page,
                    scale=args.scale,
                    rotation=args.rotation,
                    centers=args.centers,
                    max_distance=args.max_distance,
                    top=args.top,
                    bottom=args.bottom,
                ),
            }
            _write_json(evidence_path, ocr_payload)
        report = _compare_page(
            _load_draft_page(draft_path), ocr_payload["rows"], page
        )
        reports.append(report)
        totals.update({
            key: report.get(key, 0)
            for key in ("expected", "confirmed", "conflicting", "uncovered")
        })
        print(
            f"p{page:04d}: {report['confirmed']}/{report['expected']} "
            f"confirmed ({report['confirmed_pct']:.2f}%)",
            flush=True,
        )
    report = {
        "schema_version": 1,
        "method": "source-only RapidOCR coordinates + monotonic row alignment",
        "source_sha256": source_hash,
        "totals": {
            **dict(totals),
            "confirmed_pct": round(
                100 * totals["confirmed"] / totals["expected"], 2
            ) if totals["expected"] else 100.0,
        },
        "pages": reports,
    }
    _write_json(output, report)
    print(json.dumps(report["totals"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
