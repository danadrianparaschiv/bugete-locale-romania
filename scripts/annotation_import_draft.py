"""Freeze a vision draft only when independent evidence covers every cell."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bgconvertor.annotation import load_document, save_review

CellKey = tuple[int, int, str, str]


def _canonical(raw: str | None) -> str | None:
    if raw is None or raw.strip().lower() in {"", "null"}:
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ValueError(f"valoare draft necanonică: {raw!r}") from exc
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _draft_pages(directory: Path, source_sha256: str) -> dict[int, dict]:
    pages = {}
    for path in sorted((directory / "pages").glob("p*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source_sha256") != source_sha256:
            raise ValueError(f"hash sursă diferit în {path}")
        pages[int(payload["source_page"])] = payload
    return pages


def _draft_cells(pages: dict[int, dict]) -> set[CellKey]:
    cells = set()
    for page, payload in pages.items():
        for row_index, row in enumerate(payload["reading"]["rows"], 1):
            for cell in row.get("cells") or []:
                value = _canonical(cell.get("value"))
                if value is not None:
                    cells.add((page, row_index, str(cell["column"]), value))
    return cells


def _component_evidence(path: Path) -> set[CellKey]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (page["page"], fact["row"], fact["column"], fact["value"])
        for page in payload["pages"]
        for fact in page.get("facts") or []
        if fact["status"] in {"sum_confirmed", "single_source_confirmed"}
    }


def _ocr_evidence(path: Path, draft_cells: set[CellKey]) -> set[CellKey]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected_pages = {page["page"] for page in payload["pages"]}
    disagreements = {
        (
            item["page"],
            item["draft_row"],
            item["column"],
            item["draft_value"],
        )
        for page in payload["pages"]
        for item in page.get("discrepancies") or []
    }
    return {
        cell for cell in draft_cells
        if cell[0] in selected_pages and cell not in disagreements
    }


def _visual_evidence(path: Path) -> set[CellKey]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("reviewer"):
        raise ValueError("deciziile vizuale nu au reviewer")
    return {
        (item["page"], item["row"], item["column"], item["value"])
        for item in payload.get("confirmed") or []
    }


def _review_payload(
    page: int,
    draft: dict,
    evidence: dict[CellKey, str],
    *,
    revision: int,
    reviewer: str,
) -> dict:
    rows = []
    counts = Counter()
    for row_index, row in enumerate(draft["reading"]["rows"], 1):
        values = {}
        for cell in row.get("cells") or []:
            value = _canonical(cell.get("value"))
            if value is None:
                continue
            key = (page, row_index, str(cell["column"]), value)
            method = evidence[key]
            counts[method] += 1
            values[str(cell["column"])] = {
                "printed": str(cell["value"]),
                "certain": True,
                "note": f"confirmat prin {method}",
            }
        if values:
            rows.append({
                "id": f"p{page:04d}-r{row_index:04d}",
                "raw_code": row.get("code"),
                "name": row.get("name"),
                "values": values,
            })
    by_signature: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        signature = (
            row["raw_code"],
            row["name"],
            tuple(
                sorted(
                    (column, value["printed"])
                    for column, value in row["values"].items()
                )
            ),
        )
        by_signature[signature].append(row)
    section_order = ("total", "functionare", "dezvoltare")
    for repeated in by_signature.values():
        if len(repeated) > len(section_order):
            raise ValueError(
                f"mai mult de trei apariții identice pe pagina {page}: "
                f"{repeated[0]['raw_code'] or repeated[0]['name']}"
            )
        if len(repeated) > 1:
            for row, section in zip(repeated, section_order, strict=False):
                row["section"] = section
    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    return {
        "expected_revision": revision,
        "page_kind": "budget_table",
        "exhaustive": True,
        "source_unit": "mii_lei",
        "number_notation": "canonical",
        "columns": draft["columns"],
        "rows": rows,
        "reviewer": reviewer,
        "no_numeric_cells": False,
        "note": f"Transcriere exhaustivă source-only; {summary}.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("document")
    parser.add_argument("draft", type=Path)
    parser.add_argument("--component-report", type=Path, required=True)
    parser.add_argument("--ocr-report", action="append", type=Path, required=True)
    parser.add_argument("--visual-decisions", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()

    document = load_document(args.workspace, args.document)
    pages = _draft_pages(args.draft, document.source_sha256)
    if set(pages) != set(range(1, document.source_units + 1)):
        raise SystemExit("draftul nu acoperă toate unitățile sursei")
    cells = _draft_cells(pages)
    evidence_sets = {
        "componente oficiale": _component_evidence(args.component_report),
        **{
            f"OCR local {index}": _ocr_evidence(path, cells)
            for index, path in enumerate(args.ocr_report, 1)
        },
        "review vizual": _visual_evidence(args.visual_decisions),
    }
    unexpected = set().union(*evidence_sets.values()) - cells
    if unexpected:
        example = sorted(unexpected)[:3]
        raise SystemExit(f"evidence pentru celule inexistente: {example}")
    evidence_by_cell: dict[CellKey, str] = {}
    methods_by_cell: dict[CellKey, list[str]] = defaultdict(list)
    for method, confirmed in evidence_sets.items():
        for cell in confirmed:
            methods_by_cell[cell].append(method)
    for cell, methods in methods_by_cell.items():
        evidence_by_cell[cell] = " + ".join(methods)
    missing = cells - evidence_by_cell.keys()
    summary = Counter(evidence_by_cell.values())
    print(f"celule draft: {len(cells)}")
    print(f"celule confirmate: {len(evidence_by_cell)}")
    print(f"celule fără evidence: {len(missing)}")
    for method, count in sorted(summary.items()):
        print(f"  {method}: {count}")
    if missing:
        for cell in sorted(missing)[:20]:
            print(f"  lipsă: {cell}")
        raise SystemExit("refuz înghețarea: evidence incomplet")
    if not args.freeze:
        print("dry-run reușit; adaugă --freeze pentru a scrie review-urile")
        return
    for source_page in document.pages:
        if source_page.review.status == "frozen":
            print(f"p{source_page.number:04d}: deja înghețată", flush=True)
            continue
        save_review(
            args.workspace,
            args.document,
            source_page.number,
            _review_payload(
                source_page.number,
                pages[source_page.number],
                evidence_by_cell,
                revision=source_page.review.revision,
                reviewer=args.reviewer,
            ),
            freeze=True,
        )
        print(f"p{source_page.number:04d}: înghețată", flush=True)


if __name__ == "__main__":
    main()
