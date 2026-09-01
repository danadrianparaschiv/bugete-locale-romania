"""Freeze a vision draft only when independent evidence covers every cell."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from bgconvertor.annotation import load_document, save_review
from bgconvertor.orchestrator import parse_pages
from bgconvertor.parsing import NumberParseError, parse_ro_number

CellKey = tuple[int, int, str, str]


def _fold_text(raw: str) -> str:
    """Fold Romanian diacritics for semantic label classification."""
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(character for character in normalized if not unicodedata.combining(character)).lower()


def _canonical(raw: str | None) -> str | None:
    if raw is None or raw.strip().lower() in {"", "null", "x", "-", "–", "—"}:
        return None
    raw = re.sub(r"(?i)x$", "", raw).strip()
    if not raw:
        return None
    try:
        parsed = parse_ro_number(raw, ocr=True)
    except NumberParseError as exc:
        raise ValueError(f"valoare draft necanonică: {raw!r}") from exc
    if parsed in (None, "X"):
        return None
    value = parsed
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _canonical_column(raw: str) -> str:
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


def _draft_pages(directory: Path, source_sha256: str) -> dict[int, dict]:
    pages = {}
    for path in sorted((directory / "pages").glob("p*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source_sha256") != source_sha256:
            raise ValueError(f"hash sursă diferit în {path}")
        pages[int(payload["source_page"])] = payload
    return pages


def _corrected_key(
    key: CellKey,
    corrections: dict[CellKey, CellKey] | None,
) -> CellKey:
    return (corrections or {}).get(key, key)


def _draft_cells(
    pages: dict[int, dict],
    corrections: dict[CellKey, CellKey] | None = None,
    row_overrides: dict[tuple[int, int], dict[str, str]] | None = None,
    additions: set[CellKey] | None = None,
    deleted_rows: set[tuple[int, int]] | None = None,
) -> set[CellKey]:
    cells = set()
    for page, payload in pages.items():
        for row_index, row in enumerate(payload["reading"]["rows"], 1):
            if (page, row_index) in (deleted_rows or set()):
                continue
            if (page, row_index) in (row_overrides or {}):
                cells.update(
                    (page, row_index, column, value)
                    for column, value in row_overrides[page, row_index].items()
                )
                continue
            for cell in row.get("cells") or []:
                value = _canonical(cell.get("value"))
                if value is not None:
                    cells.add(_corrected_key((
                        page,
                        row_index,
                        _canonical_column(str(cell["column"])),
                        value,
                    ), corrections))
    overlap = cells & (additions or set())
    if overlap:
        raise ValueError(f"adăugare pentru celulă deja existentă: {sorted(overlap)[:3]}")
    cells.update(additions or set())
    return cells


def _classification_decisions(
    path: Path,
    source_sha256: str,
    source_units: int,
) -> tuple[str, dict[int, dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    reviewer = str(payload.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("clasificările nu au reviewer")
    if payload.get("source_sha256") != source_sha256:
        raise ValueError("hash sursă diferit în clasificări")
    decisions = {}
    for group in payload.get("classifications") or []:
        kind = group.get("page_kind")
        if kind not in {"budget_table", "other_table", "not_relevant"}:
            raise ValueError(f"clasificare invalidă: {kind!r}")
        for page in parse_pages(str(group["pages"]), source_units):
            if page in decisions:
                raise ValueError(f"pagina {page} este clasificată de două ori")
            decisions[page] = group
    expected = set(range(1, source_units + 1))
    if set(decisions) != expected:
        missing = sorted(expected - decisions.keys())
        raise ValueError(f"clasificările nu acoperă toate paginile: {missing[:10]}")
    return reviewer, decisions


def _correction_map(payload: dict, pages: dict[int, dict]) -> dict[CellKey, CellKey]:
    corrections = {}
    for item in payload.get("corrections") or []:
        page = int(item["page"])
        if page not in pages:
            raise ValueError(f"corecție pentru pagina fără draft {page}")
        rows = pages[page]["reading"]["rows"]
        for row in parse_pages(str(item["rows"]), len(rows)):
            source = item["from"]
            target = item["to"]
            old = (
                page,
                row,
                _canonical_column(str(source["column"])),
                _canonical(str(source["value"])),
            )
            new = (
                page,
                row,
                _canonical_column(str(target["column"])),
                _canonical(str(target["value"])),
            )
            if old[3] is None or new[3] is None:
                raise ValueError("corecțiile trebuie să fie numerice")
            if old in corrections:
                raise ValueError(f"corecție duplicată: {old}")
            corrections[old] = new
    original = _draft_cells(pages)
    missing = corrections.keys() - original
    if missing:
        raise ValueError(f"corecție pentru celulă inexistentă: {sorted(missing)[:3]}")
    return corrections


def _row_overrides(
    payload: dict,
    pages: dict[int, dict],
) -> dict[tuple[int, int], dict[str, str]]:
    overrides = {}
    for item in payload.get("row_replacements") or []:
        page, row = int(item["page"]), int(item["row"])
        if page not in pages or not 1 <= row <= len(pages[page]["reading"]["rows"]):
            raise ValueError(f"înlocuire pentru rând inexistent: {(page, row)}")
        values = {}
        for column, raw in item["values"].items():
            value = _canonical(str(raw))
            if value is None:
                raise ValueError("înlocuirile de rând trebuie să fie numerice")
            values[_canonical_column(str(column))] = value
        if not values:
            raise ValueError("înlocuirea de rând nu poate fi goală")
        if (page, row) in overrides:
            raise ValueError(f"înlocuire de rând duplicată: {(page, row)}")
        overrides[page, row] = values
    return overrides


def _additions(payload: dict, pages: dict[int, dict]) -> set[CellKey]:
    additions = set()
    for item in payload.get("additions") or []:
        page, row = int(item["page"]), int(item["row"])
        if page not in pages or not 1 <= row <= len(pages[page]["reading"]["rows"]):
            raise ValueError(f"adăugare pentru rând inexistent: {(page, row)}")
        value = _canonical(str(item["value"]))
        if value is None:
            raise ValueError("adăugările trebuie să fie numerice")
        additions.add((
            page, row, _canonical_column(str(item["column"])), value
        ))
    return additions


def _deleted_rows(payload: dict, pages: dict[int, dict]) -> set[tuple[int, int]]:
    deleted = set()
    for item in payload.get("deleted_rows") or []:
        page = int(item["page"])
        if page not in pages:
            raise ValueError(f"ștergere pentru pagina fără draft {page}")
        for row in parse_pages(str(item["rows"]), len(pages[page]["reading"]["rows"])):
            deleted.add((page, row))
    return deleted


def _row_contexts(
    payload: dict,
    pages: dict[int, dict],
) -> dict[tuple[int, int], dict[str, str | None]]:
    """Load source-reviewed hierarchy without guessing it from row labels."""
    allowed = {"institution", "form", "subdocument", "section"}
    contexts = {}
    for item in payload.get("row_contexts") or []:
        page = int(item["page"])
        if page not in pages:
            raise ValueError(f"context pentru pagina fără draft {page}")
        context = {
            key: str(value).strip()
            for key, value in item.items()
            if key in allowed and str(value).strip()
        }
        clear = item.get("clear") or []
        if not isinstance(clear, list) or any(key not in allowed for key in clear):
            raise ValueError("clear trebuie să enumere numai câmpuri semantice")
        context.update({key: None for key in clear})
        if not context:
            raise ValueError("contextul de rând trebuie să conțină un câmp semantic")
        for row in parse_pages(str(item["rows"]), len(pages[page]["reading"]["rows"])):
            key = (page, row)
            if key in contexts:
                raise ValueError(f"context de rând duplicat: {key}")
            contexts[key] = context
    return contexts


def _row_values(row: dict, *, strict: bool = True) -> dict[str, str]:
    values = {}
    for cell in row.get("cells") or []:
        try:
            value = _canonical(cell.get("value"))
        except ValueError:
            if strict:
                raise
            continue
        if value is not None:
            values[_canonical_column(str(cell["column"]))] = value
    return values


def _row_identity(row: dict) -> str:
    raw = str(row.get("code") or row.get("name") or "").lower()
    return re.sub(r"[^a-z0-9]", "", raw)


def _align_draft_rows(primary: list[dict], independent: list[dict]) -> list[tuple[int, int]]:
    """Align source-only readings monotonically using exact numeric agreements."""
    rows, columns = len(primary), len(independent)
    scores = [[0] * (columns + 1) for _ in range(rows + 1)]
    decisions = [["skip_independent"] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        decisions[row][0] = "skip_primary"
    for row in range(1, rows + 1):
        expected = _row_values(primary[row - 1])
        for column in range(1, columns + 1):
            observed = _row_values(independent[column - 1], strict=False)
            exact = sum(value == observed.get(name) for name, value in expected.items())
            same_identity = bool(
                _row_identity(primary[row - 1])
                and _row_identity(primary[row - 1])
                == _row_identity(independent[column - 1])
            )
            options = [
                (scores[row - 1][column], "skip_primary"),
                (scores[row][column - 1], "skip_independent"),
            ]
            if exact:
                # One exact cell outweighs every possible identity tie-break
                # on a page; identity only chooses between equal numeric paths.
                weight = exact * 100 + int(same_identity)
                options.append((scores[row - 1][column - 1] + weight, "align"))
            scores[row][column], decisions[row][column] = max(
                options, key=lambda item: (item[0], item[1] == "align")
            )
    aligned = []
    row, column = rows, columns
    while row or column:
        action = decisions[row][column]
        if row and column and action == "align":
            aligned.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif row and (not column or action == "skip_primary"):
            row -= 1
        else:
            column -= 1
    return list(reversed(aligned))


def _independent_draft_evidence(
    directory: Path,
    primary_pages: dict[int, dict],
    source_sha256: str,
) -> set[CellKey]:
    independent_pages = _draft_pages(directory, source_sha256)
    confirmed = set()
    for page in primary_pages.keys() & independent_pages.keys():
        primary_rows = primary_pages[page]["reading"]["rows"]
        independent_rows = independent_pages[page]["reading"]["rows"]
        for primary_index, independent_index in _align_draft_rows(
            primary_rows, independent_rows
        ):
            expected = _row_values(primary_rows[primary_index])
            observed = _row_values(independent_rows[independent_index], strict=False)
            for column, value in expected.items():
                if observed.get(column) == value:
                    confirmed.add((page, primary_index + 1, column, value))
    return confirmed


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


def _visual_evidence(
    path: Path,
    corrections: dict[CellKey, CellKey] | None = None,
    row_overrides: dict[tuple[int, int], dict[str, str]] | None = None,
    additions: set[CellKey] | None = None,
) -> set[CellKey]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("reviewer"):
        raise ValueError("deciziile vizuale nu au reviewer")
    confirmed = {
        (item["page"], item["row"], item["column"], item["value"])
        for item in payload.get("confirmed") or []
    }
    confirmed.update((corrections or {}).values())
    for (page, row), values in (row_overrides or {}).items():
        confirmed.update((page, row, column, value) for column, value in values.items())
    confirmed.update(additions or set())
    return confirmed


def _review_payload(
    page: int,
    draft: dict,
    evidence: dict[CellKey, str],
    *,
    revision: int,
    reviewer: str,
    corrections: dict[CellKey, CellKey] | None = None,
    row_overrides: dict[tuple[int, int], dict[str, str]] | None = None,
    additions: set[CellKey] | None = None,
    deleted_rows: set[tuple[int, int]] | None = None,
    row_contexts: dict[tuple[int, int], dict[str, str | None]] | None = None,
) -> dict:
    rows = []
    counts = Counter()
    for row_index, row in enumerate(draft["reading"]["rows"], 1):
        if (page, row_index) in (deleted_rows or set()):
            continue
        values = {}
        if (page, row_index) in (row_overrides or {}):
            source_values = row_overrides[page, row_index].items()
        else:
            source_values = (
                (_canonical_column(str(cell["column"])), value)
                for cell in row.get("cells") or []
                if (value := _canonical(cell.get("value"))) is not None
            )
        row_cells = []
        for column, value in source_values:
            key = _corrected_key((page, row_index, column, value), corrections)
            row_cells.append((key[2], key[3]))
        row_cells.extend(
            (cell[2], cell[3]) for cell in (additions or set())
            if cell[:2] == (page, row_index)
        )
        for column, value in row_cells:
            key = (page, row_index, column, value)
            method = evidence[key]
            counts[method] += 1
            values[column] = {
                "printed": value,
                "certain": True,
                "note": f"confirmat prin {method}",
            }
        if values:
            context = str(row.get("section") or "").strip() or None
            review_row = {
                "id": f"p{page:04d}-r{row_index:04d}",
                "raw_code": row.get("code"),
                "name": row.get("name"),
                "values": values,
            }
            reviewed_context = (row_contexts or {}).get((page, row_index))
            if reviewed_context:
                review_row.update(reviewed_context)
            elif context:
                if "sectiun" in _fold_text(context):
                    review_row["section"] = context
                else:
                    review_row["institution"] = context
            rows.append(review_row)
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
        "no_numeric_cells": not rows,
        "note": f"Transcriere exhaustivă source-only; {summary}.",
    }


def _classification_payload(
    decision: dict,
    *,
    revision: int,
    reviewer: str,
) -> dict:
    return {
        "expected_revision": revision,
        "page_kind": decision["page_kind"],
        "exhaustive": False,
        "source_unit": decision.get("source_unit", "unknown"),
        "number_notation": "canonical",
        "columns": [],
        "rows": [],
        "reviewer": reviewer,
        "no_numeric_cells": False,
        "note": decision.get("note") or "Clasificare source-only revizuită.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("document")
    parser.add_argument("draft", type=Path)
    parser.add_argument("--component-report", type=Path)
    parser.add_argument("--independent-draft", action="append", type=Path)
    parser.add_argument("--ocr-report", action="append", type=Path, required=True)
    parser.add_argument("--visual-decisions", type=Path, required=True)
    parser.add_argument("--classification-decisions", type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument(
        "--replace-frozen",
        action="store_true",
        help="deblochează explicit și reconstruiește review-urile înghețate",
    )
    args = parser.parse_args()

    document = load_document(args.workspace, args.document)
    pages = _draft_pages(args.draft, document.source_sha256)
    classification_reviewer = None
    classifications = None
    if args.classification_decisions:
        classification_reviewer, classifications = _classification_decisions(
            args.classification_decisions,
            document.source_sha256,
            document.source_units,
        )
        budget_pages = {
            page for page, decision in classifications.items()
            if decision["page_kind"] == "budget_table"
        }
        if set(pages) != budget_pages:
            raise SystemExit("draftul nu corespunde paginilor bugetare clasificate")
    elif set(pages) != set(range(1, document.source_units + 1)):
        raise SystemExit("draftul nu acoperă toate unitățile sursei")
    visual_payload = json.loads(args.visual_decisions.read_text(encoding="utf-8"))
    if not visual_payload.get("reviewer"):
        raise ValueError("deciziile vizuale nu au reviewer")
    corrections = _correction_map(visual_payload, pages)
    row_overrides = _row_overrides(visual_payload, pages)
    additions = _additions(visual_payload, pages)
    deleted_rows = _deleted_rows(visual_payload, pages)
    row_contexts = _row_contexts(visual_payload, pages)
    cells = _draft_cells(
        pages,
        corrections,
        row_overrides,
        additions,
        deleted_rows,
    )
    visual_evidence = _visual_evidence(
        args.visual_decisions,
        corrections,
        row_overrides,
        additions,
    )
    unexpected_visual = visual_evidence - cells
    if unexpected_visual:
        raise SystemExit(
            "decizie vizuală pentru celulă inexistentă: "
            f"{sorted(unexpected_visual)[:3]}"
        )
    evidence_sets = {
        **(
            {"componente oficiale": _component_evidence(args.component_report)}
            if args.component_report else {}
        ),
        **{
            f"draft independent {index}": _independent_draft_evidence(
                path, pages, document.source_sha256
            )
            for index, path in enumerate(args.independent_draft or [], 1)
        },
        **{
            f"OCR local {index}": _ocr_evidence(path, cells)
            for index, path in enumerate(args.ocr_report, 1)
        },
        "review vizual": visual_evidence,
    }
    # Machine evidence was produced against the immutable source-only draft.
    # Reviewed replacements legitimately supersede some of those old keys.
    evidence_sets = {
        method: confirmed & cells for method, confirmed in evidence_sets.items()
    }
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
        if source_page.review.status == "frozen" and not args.replace_frozen:
            print(f"p{source_page.number:04d}: deja înghețată", flush=True)
            continue
        if source_page.review.status == "frozen":
            source_page.review = save_review(
                args.workspace,
                args.document,
                source_page.number,
                {"expected_revision": source_page.review.revision},
                unfreeze=True,
            )
        if classifications and source_page.number not in pages:
            payload = _classification_payload(
                classifications[source_page.number],
                revision=source_page.review.revision,
                reviewer=classification_reviewer or args.reviewer,
            )
        else:
            payload = _review_payload(
                source_page.number,
                pages[source_page.number],
                evidence_by_cell,
                revision=source_page.review.revision,
                reviewer=args.reviewer,
                corrections=corrections,
                row_overrides=row_overrides,
                additions=additions,
                deleted_rows=deleted_rows,
                row_contexts=row_contexts,
            )
        save_review(
            args.workspace,
            args.document,
            source_page.number,
            payload,
            freeze=True,
        )
        print(f"p{source_page.number:04d}: înghețată", flush=True)


if __name__ == "__main__":
    main()
