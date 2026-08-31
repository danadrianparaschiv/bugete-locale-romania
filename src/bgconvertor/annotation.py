"""Offline source inventory and independent cell-ground-truth annotation.

The converter can count validation findings in rows it extracted, but only an
independent inventory can expose a row or page that was missed completely.
This module owns that inventory.  Raw PDFs, rendered pages, machine suggestions
and draft annotations live below ``runs/annotations``; only an explicit export
produces a sanitized, reviewable benchmark suitable for Git.

The independence boundary is deliberate:

* source units start as ``unreviewed`` even when extraction artifacts exist;
* machine page suggestions are hidden until the human classification is saved;
* extracted rows are hidden until exhaustive truth is frozen;
* every source value keeps its printed form and a derived ``mii lei`` value.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .config import RunConfig, project_root
from .model import BudgetLine, ConversionResult
from .parsing import NumberParseError, parse_ro_number
from .runstore import file_sha256, store_key

ANNOTATION_SCHEMA_VERSION = 1
GROUND_TRUTH_SCHEMA_VERSION = 1

PageKind = Literal["unreviewed", "budget_table", "other_table", "not_relevant", "uncertain"]
ReviewStatus = Literal["unreviewed", "classified", "draft", "frozen", "needs_review"]
SourceUnit = Literal["unknown", "lei", "mii_lei"]
NumberNotation = Literal["romanian", "canonical"]
BenchmarkScope = Literal["inventory", "full", "sample"]
FactKind = Literal["budget", "annex"]


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _git_commit(root: Path) -> str | None:
    """Read HEAD without invoking Git or requiring network access."""
    head = root / ".git" / "HEAD"
    try:
        value = head.read_text().strip()
        if value.startswith("ref: "):
            ref = root / ".git" / value.removeprefix("ref: ")
            if ref.exists():
                return ref.read_text().strip()
            packed = root / ".git" / "packed-refs"
            if packed.exists():
                target = value.removeprefix("ref: ")
                for line in packed.read_text().splitlines():
                    if line and not line.startswith(("#", "^")):
                        commit, name = line.split(" ", 1)
                        if name == target:
                            return commit
            return None
        return value or None
    except OSError:
        return None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _safe_id(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in folded if not unicodedata.combining(char))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    if not slug:
        raise ValueError(f"cannot derive document id from {value!r}")
    return slug


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_printed_value(
    printed: str,
    *,
    source_unit: SourceUnit,
    notation: NumberNotation,
) -> str:
    """Normalize one human reading to the public ``mii lei`` unit."""
    raw = printed.strip()
    if not raw:
        raise ValueError("valoarea tipărită este goală")
    if source_unit == "unknown":
        raise ValueError("unitatea sursei trebuie confirmată")
    try:
        if notation == "romanian":
            parsed = parse_ro_number(raw)
            if parsed in (None, "X"):
                raise ValueError("numai celulele numerice intră în ground truth")
            value = Decimal(parsed)
        else:
            value = Decimal(raw.replace(" ", ""))
    except (NumberParseError, InvalidOperation) as exc:
        raise ValueError(f"valoare numerică ambiguă: {printed!r}") from exc
    if source_unit == "lei":
        value /= Decimal(1000)
    return _canonical_decimal(value)


class AnnotationValue(BaseModel):
    printed: str
    normalized_mii_lei: str | None = None
    certain: bool = True
    note: str | None = None


class GroundTruthRow(BaseModel):
    id: str
    raw_code: str | None = None
    functional_code: str | None = None
    economic_code: str | None = None
    name: str | None = None
    institution: str | None = None
    form: str | None = None
    subdocument: str | None = None
    section: str | None = None
    row_year: int | None = None
    fact_kind: FactKind = "budget"
    values: dict[str, AnnotationValue] = Field(default_factory=dict)
    note: str | None = None

    @model_validator(mode="after")
    def _identity_and_values(self):
        if not any((self.raw_code, self.functional_code, self.economic_code, self.name)):
            raise ValueError("fiecare rând are nevoie de cod sau denumire")
        if not self.values:
            raise ValueError("fiecare rând are nevoie de cel puțin o valoare numerică")
        return self


class PageReview(BaseModel):
    revision: int = 0
    page_kind: PageKind = "unreviewed"
    status: ReviewStatus = "unreviewed"
    exhaustive: bool = False
    source_unit: SourceUnit = "unknown"
    number_notation: NumberNotation = "romanian"
    columns: list[str] = Field(default_factory=list)
    rows: list[GroundTruthRow] = Field(default_factory=list)
    reviewer: str | None = None
    reviewed_at: str | None = None
    frozen_at: str | None = None
    second_reviewer: str | None = None
    second_reviewed_at: str | None = None
    no_numeric_cells: bool = False
    default_institution: str | None = None
    default_form: str | None = None
    default_subdocument: str | None = None
    default_section: str | None = None
    note: str | None = None


class MachineSuggestion(BaseModel):
    suggested_kind: Literal["budget_table", "other_table", "not_relevant", "uncertain"]
    reason: str
    layout: str | None = None
    numeric_cells: int = 0
    extracted_lines: int = 0
    has_text_layer: bool | None = None
    source_unit: str | None = None
    extraction_stage: str | None = None
    extraction_config_hash: str | None = None


class SourcePage(BaseModel):
    number: int
    label: str
    source_type: Literal["pdf_page", "workbook_sheet"]
    sheet_name: str | None = None
    machine: MachineSuggestion
    review: PageReview


class AnnotationDocument(BaseModel):
    schema_version: int = ANNOTATION_SCHEMA_VERSION
    id: str
    year: int
    municipality: str
    siruta: str
    county_code: str
    county_name: str
    source_path: str
    source_format: str
    source_sha256: str
    expected_sha256: str | None = None
    source_hash_verified: bool
    source_units: int
    observed_strict_line_rate: float | None = None
    benchmark_scope: BenchmarkScope = "inventory"
    pages: list[SourcePage]


class WorkspaceDocument(BaseModel):
    id: str
    municipality: str
    siruta: str
    county_code: str
    county_name: str
    source_format: str
    source_path: str
    source_units: int
    benchmark_scope: BenchmarkScope
    observed_strict_line_rate: float | None = None


class AnnotationWorkspace(BaseModel):
    schema_version: int = ANNOTATION_SCHEMA_VERSION
    year: int
    repository_root: str
    data_root: str
    runs_dir: str
    created_at: str
    updated_at: str
    tool_commit: str | None = None
    documents: list[WorkspaceDocument]
    unavailable_sources: list[dict[str, Any]] = Field(default_factory=list)


class PredictionFact(BaseModel):
    document: str
    context_id: str | None = None
    institution: str | None = None
    budget: str
    page: int
    section: str | None = None
    kind: str
    raw_code: str | None = None
    functional_code: str | None = None
    economic_code: str | None = None
    name: str
    column: str
    value_mii_lei: str
    source: str


def default_workspace(config: RunConfig, year: int) -> Path:
    return config.runs_dir / "annotations" / str(year)


def _workspace_path(workspace: Path) -> Path:
    return workspace / "workspace.json"


def _document_path(workspace: Path, document_id: str) -> Path:
    return workspace / "documents" / f"{document_id}.json"


def load_workspace(workspace: Path) -> AnnotationWorkspace:
    return AnnotationWorkspace.model_validate_json(_workspace_path(workspace).read_text())


def load_document(workspace: Path, document_id: str) -> AnnotationDocument:
    path = _document_path(workspace, document_id)
    if not path.exists():
        raise KeyError(f"document necunoscut: {document_id}")
    return AnnotationDocument.model_validate_json(path.read_text())


def _save_workspace(workspace_path: Path, workspace: AnnotationWorkspace) -> None:
    workspace.updated_at = _now()
    _atomic_json(_workspace_path(workspace_path), workspace.model_dump(mode="json"))


def _save_document(workspace: Path, document: AnnotationDocument) -> None:
    _atomic_json(_document_path(workspace, document.id), document.model_dump(mode="json"))


def _raw_envelope(config: RunConfig, source: Path, stage: str, page: int) -> dict | None:
    path = config.runs_dir / store_key(source) / stage / f"p{page:04d}.json"
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
        return None
    return envelope


def _payload_envelope(config: RunConfig, source: Path, page: int) -> tuple[str, dict] | None:
    llm = _raw_envelope(config, source, "llm_extract", page)
    deterministic = _raw_envelope(config, source, "extract", page)
    if deterministic is not None:
        layout = deterministic["payload"].get("layout")
        if layout in {
            "investment_list", "allocations_annex", "annex_other", "hcl_prose",
            "official_prose_summary",
        }:
            return "extract", deterministic
    if llm is not None:
        return "llm_extract", llm
    return ("extract", deterministic) if deterministic is not None else None


def _pdf_suggestion(config: RunConfig, source: Path, page: int) -> MachineSuggestion:
    selected = _payload_envelope(config, source, page)
    profile = _raw_envelope(config, source, "profile", page)
    has_text = (profile or {}).get("payload", {}).get("has_text_layer")
    if selected is None:
        return MachineSuggestion(
            suggested_kind="uncertain",
            reason="nu există payload de extracție pentru această pagină",
            has_text_layer=has_text,
        )
    stage, envelope = selected
    payload = envelope["payload"]
    lines = payload.get("lines") or []
    numeric = sum(
        1
        for line in lines
        for value in (line.get("values") or {}).values()
        if value != "X"
    )
    layout = payload.get("layout")
    if layout in {"investment_list", "allocations_annex", "annex_other"}:
        suggested = "other_table"
        reason = f"layout în afara tabelului bugetar principal: {layout}"
    elif numeric:
        suggested = "budget_table"
        reason = f"extracția a emis {numeric} celule numerice"
    elif payload.get("text"):
        suggested = "not_relevant"
        reason = "pagina conține text, fără celule numerice extrase"
    else:
        suggested = "uncertain"
        reason = "pagina nu are suficiente semnale structurale"
    return MachineSuggestion(
        suggested_kind=suggested,
        reason=reason,
        layout=layout,
        numeric_cells=numeric,
        extracted_lines=len(lines),
        has_text_layer=has_text,
        source_unit=payload.get("source_unit"),
        extraction_stage=stage,
        extraction_config_hash=envelope.get("config_hash"),
    )


def _workbook_payloads(source: Path, year: int, registry) -> dict[int, dict[str, Any]]:
    from .native_workbook import workbook_payloads

    return workbook_payloads(source, year, registry)


def _workbook_suggestion(payload: dict[str, Any]) -> MachineSuggestion:
    lines = payload.get("lines") or []
    numeric = sum(len(line.get("values") or {}) for line in lines)
    layout = payload.get("layout")
    if numeric:
        kind = "budget_table"
        reason = f"mapperul Excel a emis {numeric} celule numerice"
    elif layout == "native_excel_metadata":
        kind = "not_relevant"
        reason = "foaia nu corespunde unei machete bugetare recunoscute"
    else:
        kind = "uncertain"
        reason = "foaia necesită clasificare manuală"
    return MachineSuggestion(
        suggested_kind=kind,
        reason=reason,
        layout=layout,
        numeric_cells=numeric,
        extracted_lines=len(lines),
        has_text_layer=True,
        source_unit=payload.get("source_unit"),
        extraction_stage="native_workbook",
        extraction_config_hash=None,
    )


def _expected_source_hash(entry: dict[str, Any]) -> str | None:
    artifacts = (entry.get("conversion") or {}).get("artifacts") or {}
    return artifacts.get("source_pdf_sha256") or artifacts.get("source_sha256")


def initialize_workspace(
    *,
    year: int,
    data_root: Path,
    workspace_path: Path,
    config: RunConfig,
    refresh: bool = False,
) -> AnnotationWorkspace:
    """Build or refresh an ignored local inventory without losing reviews."""
    root = project_root(data_root)
    manifest_path = data_root / str(year) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    existing_workspace = load_workspace(workspace_path) if _workspace_path(workspace_path).exists() else None
    if existing_workspace and existing_workspace.year != year:
        raise ValueError(
            f"workspace-ul existent este pentru {existing_workspace.year}, nu {year}"
        )
    if existing_workspace and not refresh:
        return existing_workspace

    from .nomenclator import load_registry_for_year

    registry = load_registry_for_year(config.reference_dir, year)
    documents: list[WorkspaceDocument] = []
    unavailable: list[dict[str, Any]] = []

    for entry in manifest.get("entries", []):
        conversion = entry.get("conversion") or {}
        if conversion.get("status") != "converted":
            unavailable.append({
                "municipality": entry.get("capital_name"),
                "source_path": entry.get("path"),
                "reason": conversion.get("reason") or conversion.get("status") or "not_converted",
            })
            continue
        relative_source = Path(entry["path"])
        source = data_root / str(year) / relative_source
        if not source.exists():
            unavailable.append({
                "municipality": entry.get("capital_name"),
                "source_path": str(relative_source),
                "reason": "source_missing_locally",
            })
            continue
        document_id = _safe_id("-".join(relative_source.parent.parts))
        old_document = None
        old_path = _document_path(workspace_path, document_id)
        if old_path.exists():
            old_document = AnnotationDocument.model_validate_json(old_path.read_text())
        actual_hash = file_sha256(source)
        expected_hash = _expected_source_hash(entry)
        if expected_hash and actual_hash != expected_hash:
            raise ValueError(
                f"hash sursă diferit pentru {source}: {actual_hash} != {expected_hash}"
            )
        quality = conversion.get("quality") or {}
        rate = quality.get("pct_lines_strictly_verified")
        scope: BenchmarkScope = "full" if rate is not None and float(rate) < 70 else "inventory"
        if old_document is not None:
            scope = old_document.benchmark_scope

        pages: list[SourcePage] = []
        if source.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            unit_count = len(PdfReader(source).pages)
            old_pages = {page.number: page for page in old_document.pages} if old_document else {}
            for page in range(1, unit_count + 1):
                old = old_pages.get(page)
                review = old.review if old else PageReview(number_notation="romanian")
                pages.append(SourcePage(
                    number=page,
                    label=f"Pagina {page}",
                    source_type="pdf_page",
                    machine=_pdf_suggestion(config, source, page),
                    review=review,
                ))
        else:
            payloads = _workbook_payloads(source, year, registry)
            from .native_workbook import read_sheets

            sheets = read_sheets(source)
            unit_count = len(sheets)
            old_pages = {page.number: page for page in old_document.pages} if old_document else {}
            for page, (sheet_name, _grid) in enumerate(sheets, 1):
                old = old_pages.get(page)
                review = old.review if old else PageReview(number_notation="canonical")
                pages.append(SourcePage(
                    number=page,
                    label=f"Foaia {sheet_name}",
                    source_type="workbook_sheet",
                    sheet_name=sheet_name,
                    machine=_workbook_suggestion(payloads[page]),
                    review=review,
                ))

        expected_units = (quality.get("scope") or {}).get("pages_expected")
        if expected_units is not None and int(expected_units) != unit_count:
            raise ValueError(
                f"scope public diferit pentru {source}: {expected_units} != {unit_count}"
            )
        annotation_document = AnnotationDocument(
            id=document_id,
            year=year,
            municipality=entry["capital_name"],
            siruta=str(entry["capital_siruta"]),
            county_code=str(entry["county_code"]),
            county_name=entry["county_name"],
            source_path=str(source.resolve().relative_to(root)),
            source_format=entry.get("source_format") or source.suffix.lstrip("."),
            source_sha256=actual_hash,
            expected_sha256=expected_hash,
            source_hash_verified=not expected_hash or actual_hash == expected_hash,
            source_units=unit_count,
            observed_strict_line_rate=float(rate) if rate is not None else None,
            benchmark_scope=scope,
            pages=pages,
        )
        _save_document(workspace_path, annotation_document)
        documents.append(WorkspaceDocument(
            id=document_id,
            municipality=annotation_document.municipality,
            siruta=annotation_document.siruta,
            county_code=annotation_document.county_code,
            county_name=annotation_document.county_name,
            source_format=annotation_document.source_format,
            source_path=annotation_document.source_path,
            source_units=annotation_document.source_units,
            benchmark_scope=annotation_document.benchmark_scope,
            observed_strict_line_rate=annotation_document.observed_strict_line_rate,
        ))

    documents.sort(key=lambda item: (
        item.benchmark_scope != "full",
        item.observed_strict_line_rate if item.observed_strict_line_rate is not None else 101,
        item.county_code,
        item.municipality,
    ))
    workspace = AnnotationWorkspace(
        year=year,
        repository_root=str(root),
        data_root=str(data_root.resolve()),
        runs_dir=str(config.runs_dir.resolve()),
        created_at=existing_workspace.created_at if existing_workspace else _now(),
        updated_at=_now(),
        tool_commit=_git_commit(root),
        documents=documents,
        unavailable_sources=unavailable,
    )
    _save_workspace(workspace_path, workspace)
    return workspace


def _page(document: AnnotationDocument, page_number: int) -> SourcePage:
    if page_number < 1 or page_number > len(document.pages):
        raise KeyError(f"unitate sursă inexistentă: {page_number}")
    page = document.pages[page_number - 1]
    if page.number != page_number:
        raise ValueError(f"inventar neordonat pentru {document.id}")
    return page


def _normalize_review(review: PageReview) -> PageReview:
    normalized_rows = []
    for row in review.rows:
        normalized_values = {}
        for column, value in row.values.items():
            normalized_values[column] = value.model_copy(update={
                "normalized_mii_lei": normalize_printed_value(
                    value.printed,
                    source_unit=review.source_unit,
                    notation=review.number_notation,
                )
            })
        normalized_rows.append(row.model_copy(update={
            "values": normalized_values,
            "institution": row.institution or review.default_institution,
            "form": row.form or review.default_form,
            "subdocument": row.subdocument or review.default_subdocument,
            "section": row.section or review.default_section,
        }))
    return review.model_copy(update={"rows": normalized_rows})


def _validate_frozen_review(review: PageReview) -> list[str]:
    problems = []
    if review.page_kind == "unreviewed":
        problems.append("pagina nu este clasificată")
    if not (review.reviewer or "").strip():
        problems.append("lipsește aliasul annotatorului")
    if review.page_kind == "budget_table":
        if not review.exhaustive:
            problems.append("pagina bugetară nu este marcată exhaustiv")
        if review.source_unit == "unknown" and review.rows:
            problems.append("unitatea sursei nu este confirmată")
        if not review.rows and not review.no_numeric_cells:
            problems.append("pagina bugetară nu are rânduri și nu este marcată fără celule numerice")
    seen: set[tuple[str, str, str, str]] = set()
    for row in review.rows:
        # A code can legitimately repeat on the same printed page for a
        # parent line and one or more detailed lines. Use the complete
        # printed identity so those rows remain distinct while an exact
        # repeated fact is still rejected.
        identity = "|".join(value or "" for value in (
            row.raw_code,
            row.functional_code,
            row.economic_code,
            row.name,
        ))
        context = "|".join(filter(None, (
            row.institution, row.form, row.subdocument, row.section,
        )))
        for column, value in row.values.items():
            key = (context, identity, column, value.normalized_mii_lei or "")
            if key in seen:
                problems.append(f"celulă duplicată: {identity} / {column}")
            seen.add(key)
            if value.normalized_mii_lei is None:
                problems.append(f"valoare nenormalizată: {identity} / {column}")
    return problems


def save_review(
    workspace_path: Path,
    document_id: str,
    page_number: int,
    review_payload: dict[str, Any],
    *,
    freeze: bool = False,
    unfreeze: bool = False,
) -> PageReview:
    document = load_document(workspace_path, document_id)
    source_page = _page(document, page_number)
    expected_revision = int(review_payload.pop("expected_revision", -1))
    if expected_revision != source_page.review.revision:
        raise RuntimeError(
            f"revizie conflictuală: client {expected_revision}, server {source_page.review.revision}"
        )
    if source_page.review.status == "frozen" and not unfreeze:
        raise ValueError("ground truth este înghețat; deblochează explicit înainte de editare")
    if unfreeze:
        review = source_page.review.model_copy(update={
            "revision": source_page.review.revision + 1,
            "status": "needs_review",
            "frozen_at": None,
            "second_reviewer": None,
            "second_reviewed_at": None,
        })
    else:
        review = PageReview.model_validate({
            **review_payload,
            "revision": source_page.review.revision + 1,
        })
        if review.rows:
            review = _normalize_review(review)
        if freeze:
            review = review.model_copy(update={
                "status": "frozen",
                "reviewed_at": review.reviewed_at or _now(),
                "frozen_at": _now(),
            })
            problems = _validate_frozen_review(review)
            if problems:
                raise ValueError("; ".join(problems))
        elif review.page_kind == "unreviewed":
            review = review.model_copy(update={"status": "unreviewed"})
        elif review.rows:
            review = review.model_copy(update={"status": "draft"})
        else:
            review = review.model_copy(update={"status": "classified"})
    source_page.review = review
    _save_document(workspace_path, document)
    workspace = load_workspace(workspace_path)
    _save_workspace(workspace_path, workspace)
    return review


def set_benchmark_scope(
    workspace_path: Path, document_id: str, scope: BenchmarkScope
) -> AnnotationDocument:
    document = load_document(workspace_path, document_id)
    document.benchmark_scope = scope
    _save_document(workspace_path, document)
    workspace = load_workspace(workspace_path)
    for item in workspace.documents:
        if item.id == document_id:
            item.benchmark_scope = scope
            break
    _save_workspace(workspace_path, workspace)
    return document


def complete_second_review(
    workspace_path: Path,
    document_id: str,
    page_number: int,
    *,
    expected_revision: int,
    reviewer: str,
) -> PageReview:
    """Attest a frozen discrepancy/uncertainty without rewriting its truth."""
    document = load_document(workspace_path, document_id)
    source_page = _page(document, page_number)
    review = source_page.review
    if review.revision != expected_revision:
        raise RuntimeError(
            f"revizie conflictuală: client {expected_revision}, server {review.revision}"
        )
    if review.status != "frozen":
        raise ValueError("revizia a doua se aplică numai unui ground truth înghețat")
    alias = reviewer.strip()
    if not alias:
        raise ValueError("lipsește aliasul celui de-al doilea reviewer")
    if alias == (review.reviewer or "").strip():
        raise ValueError("al doilea reviewer trebuie să fie diferit de primul")
    source_page.review = review.model_copy(update={
        "revision": review.revision + 1,
        "second_reviewer": alias,
        "second_reviewed_at": _now(),
    })
    _save_document(workspace_path, document)
    workspace = load_workspace(workspace_path)
    _save_workspace(workspace_path, workspace)
    return source_page.review


def workspace_summary(workspace_path: Path) -> dict[str, Any]:
    workspace = load_workspace(workspace_path)
    status_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    document_rows = []
    total = 0
    for item in workspace.documents:
        document = load_document(workspace_path, item.id)
        counts = Counter(page.review.status for page in document.pages)
        kinds = Counter(page.review.page_kind for page in document.pages)
        total += len(document.pages)
        status_counts.update(counts)
        kind_counts.update(kinds)
        document_rows.append({
            **item.model_dump(mode="json"),
            "status_counts": dict(counts),
            "kind_counts": dict(kinds),
            "classified": len(document.pages) - kinds.get("unreviewed", 0),
            "frozen": counts.get("frozen", 0),
            "first_unreviewed": next((
                page.number for page in document.pages
                if page.review.page_kind == "unreviewed"
            ), None),
        })
    return {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "year": workspace.year,
        "source_units": total,
        "documents": document_rows,
        "status_counts": dict(status_counts),
        "kind_counts": dict(kind_counts),
        "unavailable_sources": workspace.unavailable_sources,
        "tool_commit": workspace.tool_commit,
        "updated_at": workspace.updated_at,
    }


def audit_workspace(workspace_path: Path) -> dict[str, Any]:
    workspace = load_workspace(workspace_path)
    problems: list[dict[str, Any]] = []
    totals = Counter()
    budget_layout_pages: Counter[str] = Counter()
    covered_layout_pages: Counter[str] = Counter()
    scored_discrepancies = {}
    score_path = workspace_path / "score.json"
    if score_path.exists():
        try:
            score = json.loads(score_path.read_text())
            scored_discrepancies = {
                (page["document"], page["page"]): page
                for page in score.get("pages", [])
                if page.get("misses") or page.get("extras")
            }
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            scored_discrepancies = {}
    for item in workspace.documents:
        document = load_document(workspace_path, item.id)
        if not document.source_hash_verified:
            problems.append({"document": item.id, "problem": "source_hash_unverified"})
        for source_page in document.pages:
            review = source_page.review
            totals["source_units"] += 1
            totals[f"kind:{review.page_kind}"] += 1
            totals[f"status:{review.status}"] += 1
            layout = source_page.machine.layout
            if review.page_kind == "budget_table" and layout:
                budget_layout_pages[layout] += 1
                if review.status == "frozen" and review.exhaustive:
                    covered_layout_pages[layout] += 1
            if review.page_kind == "unreviewed":
                problems.append({
                    "document": item.id, "page": source_page.number,
                    "problem": "unreviewed_source_unit",
                })
            elif review.page_kind == "uncertain":
                problems.append({
                    "document": item.id, "page": source_page.number,
                    "problem": "uncertain_source_unit",
                })
            if document.benchmark_scope == "full" and review.page_kind == "budget_table":
                if review.status != "frozen":
                    problems.append({
                        "document": item.id, "page": source_page.number,
                        "problem": "full_benchmark_page_not_frozen",
                    })
                else:
                    for detail in _validate_frozen_review(review):
                        problems.append({
                            "document": item.id, "page": source_page.number,
                            "problem": "invalid_frozen_truth", "detail": detail,
                        })
            uncertain = sum(
                not value.certain
                for row in review.rows
                for value in row.values.values()
            )
            if uncertain and not review.second_reviewed_at:
                problems.append({
                    "document": item.id, "page": source_page.number,
                    "problem": "uncertain_cells_need_second_review", "cells": uncertain,
                })
            discrepancy = scored_discrepancies.get((item.id, source_page.number))
            if (
                discrepancy
                and discrepancy.get("annotation_revision") == review.revision
                and not review.second_reviewed_at
            ):
                problems.append({
                    "document": item.id, "page": source_page.number,
                    "problem": "converter_discrepancy_needs_second_review",
                    "missing_cells": len(discrepancy.get("misses") or []),
                    "extra_cells": len(discrepancy.get("extras") or []),
                })
        if document.benchmark_scope == "sample" and not any(
            page.review.page_kind == "budget_table"
            and page.review.status == "frozen"
            and page.review.exhaustive
            for page in document.pages
        ):
            problems.append({
                "document": item.id,
                "problem": "sample_benchmark_has_no_frozen_page",
            })
    for layout in sorted(budget_layout_pages):
        if not covered_layout_pages[layout]:
            problems.append({
                "layout": layout,
                "problem": "uncovered_layout_family",
                "budget_pages": budget_layout_pages[layout],
            })
    classified = (
        totals["source_units"]
        - totals["kind:unreviewed"]
        - totals["kind:uncertain"]
    )
    return {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "year": workspace.year,
        "complete_page_inventory": classified == totals["source_units"],
        "recall_measurement_ready": not problems,
        "totals": dict(totals),
        "layout_families": {
            layout: {
                "budget_pages": count,
                "frozen_exhaustive_pages": covered_layout_pages[layout],
                "covered": covered_layout_pages[layout] > 0,
            }
            for layout, count in sorted(budget_layout_pages.items())
        },
        "problems": problems,
    }


class _SnapshotStore:
    """Assemble the exact persisted candidate, independent of current env config."""

    def __init__(self, config: RunConfig, source: Path):
        self.config = config
        self.source = source

    def get(self, stage: str, page: int):
        envelope = _raw_envelope(self.config, self.source, stage, page)
        return envelope.get("payload") if envelope else None


def _classification_codes(line: BudgetLine) -> tuple[str | None, str | None]:
    if line.kind == "expense_functional":
        return line.code, None
    if line.kind == "expense_economic":
        return line.func_code, line.code
    if line.kind == "revenue":
        return None, line.code
    return line.func_code, None


def prediction_facts(result: ConversionResult) -> list[PredictionFact]:
    facts = []
    for document in result.documents:
        for line in document.lines:
            if line.kind == "annex":
                continue
            functional_code, economic_code = _classification_codes(line)
            for column, value in line.values.items():
                cell_source = line.value_sources.get(column, line.source)
                # Independently derived analytics are valid output facts, but
                # they are not printed cells and therefore do not belong in
                # source-cell precision or recall.
                if "derived" in cell_source:
                    continue
                facts.append(PredictionFact(
                    document=document.title,
                    context_id=document.context_id,
                    institution=document.institution,
                    budget=document.budget,
                    page=line.page,
                    section=line.section,
                    kind=line.kind,
                    raw_code=line.raw_code,
                    functional_code=functional_code,
                    economic_code=economic_code,
                    name=line.name,
                    column=column,
                    value_mii_lei=_canonical_decimal(value),
                    source=cell_source,
                ))
    return facts


def _fold(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _text_match(expected: str | None, actual: str | None) -> bool:
    if not expected:
        return True
    needle = " ".join(_fold(expected).split())
    haystack = " ".join(_fold(actual).split())
    if needle in haystack:
        return True
    from rapidfuzz import fuzz

    if fuzz.partial_ratio(needle, haystack) >= 85:
        return True
    expected_tokens = _semantic_tokens(needle)
    actual_tokens = _semantic_tokens(haystack)
    return len(expected_tokens) >= 3 and expected_tokens <= actual_tokens


_TOKEN_ALIASES = (
    ("drept", "drepturi"),
    ("asist", "asistenti"),
    ("pers", "persoane"),
    ("hand", "handicap"),
    ("salari", "salarii"),
    ("unit", "unitati"),
    ("ingr", "ingrijire"),
    ("dom", "domiciliu"),
    ("chelt", "cheltuieli"),
    ("mat", "materiale"),
    ("dir", "directie"),
    ("indemn", "indemnizatii"),
    ("insot", "insotitori"),
)
_TOKEN_STOP = {"a", "ale", "cu", "de", "din", "in", "la", "si"}


def _semantic_tokens(value: str) -> set[str]:
    """Normalize common printed abbreviations without weakening identity.

    Comparative summaries often omit a code for local explanatory rows and
    abbreviate labels aggressively. Critical source markers remain distinct:
    ``BL`` expands to ``buget local`` while ``TVA`` stays ``tva``.
    """
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", value):
        if raw in _TOKEN_STOP:
            continue
        if raw == "bl":
            tokens.update(("buget", "local"))
            continue
        canonical = next(
            (target for prefix, target in _TOKEN_ALIASES if raw.startswith(prefix)),
            raw,
        )
        tokens.add(canonical)
    return tokens


def _code(value: str | None) -> str:
    return re.sub(r"[^0-9]", "", value or "")


def _fact_matches(row: GroundTruthRow, fact: PredictionFact) -> bool:
    if row.raw_code and _code(row.raw_code) != _code(fact.raw_code):
        return False
    if row.functional_code and _code(row.functional_code) != _code(fact.functional_code):
        return False
    if row.economic_code and _code(row.economic_code) != _code(fact.economic_code):
        return False
    # Exact printed codes are the stable semantic identity. Names are still
    # required for uncoded rows, but must not turn a correct coded cell into a
    # miss merely because OCR abbreviated or damaged its descriptive label.
    has_code_identity = any((row.raw_code, row.functional_code, row.economic_code))
    if not has_code_identity and not _text_match(row.name, fact.name):
        return False
    if not _text_match(row.institution, fact.institution):
        return False
    if not _text_match(row.form, fact.document):
        return False
    if row.subdocument and not (
        _text_match(row.subdocument, fact.context_id)
        or _text_match(row.subdocument, fact.document)
    ):
        return False
    if not _text_match(row.section, fact.section):
        return False
    return True


def score_page(review: PageReview, facts: list[PredictionFact], page_number: int) -> dict[str, Any]:
    page_facts = [fact for fact in facts if fact.page == page_number]
    consumed: set[int] = set()
    expected = matched = 0
    misses = []
    for row in review.rows:
        if row.fact_kind != "budget":
            continue
        for column, value in row.values.items():
            expected += 1
            key = next((
                index
                for index, fact in enumerate(page_facts)
                if index not in consumed
                and fact.column == column
                and Decimal(fact.value_mii_lei) == Decimal(value.normalized_mii_lei or "NaN")
                and _fact_matches(row, fact)
            ), None)
            if key is None:
                misses.append({
                    "row_id": row.id,
                    "identity": row.raw_code or row.economic_code or row.functional_code or row.name,
                    "column": column,
                    "expected_mii_lei": value.normalized_mii_lei,
                })
            else:
                consumed.add(key)
                matched += 1
    extras = [fact.model_dump(mode="json") for index, fact in enumerate(page_facts) if index not in consumed]
    predicted = len(page_facts)
    return {
        "page": page_number,
        "annotation_revision": review.revision,
        "expected": expected,
        "matched": matched,
        "predicted": predicted,
        "recall_pct": round(100 * matched / expected, 2) if expected else None,
        "precision_pct": round(100 * matched / predicted, 2) if predicted else None,
        "misses": misses,
        "extras": extras,
    }


def _prediction_result(
    config: RunConfig, source: Path, year: int, source_units: int
) -> ConversionResult:
    from .nomenclator import load_registry_for_year

    registry = load_registry_for_year(config.reference_dir, year)
    if source.suffix.lower() in {".xls", ".xlsx"}:
        from .native_workbook import convert_workbook

        return convert_workbook(source, year, registry)
    from .assemble import assemble

    pages = list(range(1, source_units + 1))
    store = _SnapshotStore(config, source)
    processed = [page for page in pages if store.get("extract", page) is not None]
    return ConversionResult(
        pdf=source.name,
        documents=assemble(store, pages, registry),
        pages_expected=source_units,
        pages_selected=pages,
        pages_processed=processed,
    )


def _candidate_metadata(config: RunConfig, source: Path, source_units: int) -> dict[str, Any]:
    if source.suffix.lower() in {".xls", ".xlsx"}:
        return {
            "source_sha256": file_sha256(source),
            "stages": {"native_workbook": {"source_units": source_units}},
        }
    stages = {}
    for stage in ("extract", "llm_extract"):
        digest = hashlib.sha256()
        pages = 0
        config_hashes = set()
        for page in range(1, source_units + 1):
            envelope = _raw_envelope(config, source, stage, page)
            if envelope is None:
                continue
            pages += 1
            if envelope.get("config_hash"):
                config_hashes.add(envelope["config_hash"])
            digest.update(f"{page}:".encode())
            digest.update(json.dumps(
                envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode())
        if pages:
            stages[stage] = {
                "pages": pages,
                "config_hashes": sorted(config_hashes),
                "artifacts_sha256": digest.hexdigest(),
            }
    return {"source_sha256": file_sha256(source), "stages": stages}


def score_workspace(workspace_path: Path) -> dict[str, Any]:
    workspace = load_workspace(workspace_path)
    config = RunConfig(runs_dir=Path(workspace.runs_dir))
    root = Path(workspace.repository_root)
    document_scores = []
    totals = Counter()
    all_pages = []
    pre_audit = audit_workspace(workspace_path)
    for item in workspace.documents:
        document = load_document(workspace_path, item.id)
        source = root / document.source_path
        actual_source_hash = file_sha256(source)
        if actual_source_hash != document.source_sha256:
            raise ValueError(
                f"sursa s-a schimbat pentru {document.id}: "
                f"{actual_source_hash} != {document.source_sha256}"
            )
        result = _prediction_result(config, source, workspace.year, document.source_units)
        facts = prediction_facts(result)
        pages = []
        budget_pages = predicted_budget_pages = predicted_pages = 0
        unresolved_pages = 0
        missing_frozen_truth = 0
        for source_page in document.pages:
            review = source_page.review
            page_has_prediction = any(fact.page == source_page.number for fact in facts)
            if review.page_kind in {"unreviewed", "uncertain"}:
                unresolved_pages += 1
            elif page_has_prediction:
                predicted_pages += 1
            if review.page_kind == "budget_table":
                budget_pages += 1
                if page_has_prediction:
                    predicted_budget_pages += 1
                if document.benchmark_scope == "full" and not (
                    review.status == "frozen" and review.exhaustive
                ):
                    missing_frozen_truth += 1
            if review.status == "frozen" and review.exhaustive and review.page_kind == "budget_table":
                page_score = score_page(review, facts, source_page.number)
                pages.append(page_score)
                all_pages.append({"document": item.id, **page_score})
                totals["expected"] += page_score["expected"]
                totals["matched"] += page_score["matched"]
                totals["predicted"] += page_score["predicted"]
        totals["budget_pages"] += budget_pages
        totals["predicted_budget_pages"] += predicted_budget_pages
        totals["predicted_pages"] += predicted_pages
        document_recall_measured = (
            item.benchmark_scope in {"full", "sample"}
            and unresolved_pages == 0
            and missing_frozen_truth == 0
            and sum(page["expected"] for page in pages) > 0
        )
        document_scores.append({
            "document": item.id,
            "municipality": item.municipality,
            "benchmark_scope": item.benchmark_scope,
            "budget_pages": budget_pages,
            "predicted_budget_pages": predicted_budget_pages,
            "predicted_pages": predicted_pages,
            "budget_page_recall_pct": (
                round(100 * predicted_budget_pages / budget_pages, 2) if budget_pages else None
            ),
            "budget_page_precision_pct": (
                round(100 * predicted_budget_pages / predicted_pages, 2)
                if predicted_pages else None
            ),
            "recall_measured": document_recall_measured,
            "expected": sum(page["expected"] for page in pages),
            "matched": sum(page["matched"] for page in pages),
            "predicted": sum(page["predicted"] for page in pages),
            "pages": pages,
            "candidate": _candidate_metadata(config, source, document.source_units),
        })
    blocking_scope_problems = {
        "source_hash_unverified",
        "unreviewed_source_unit",
        "uncertain_source_unit",
        "full_benchmark_page_not_frozen",
        "invalid_frozen_truth",
        "uncertain_cells_need_second_review",
    }
    annotation_scope_complete = not any(
        problem.get("problem") in blocking_scope_problems
        for problem in pre_audit["problems"]
    )
    report = {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "year": workspace.year,
        "metric_scope": "frozen exhaustive budget-table source units",
        "recall_measured": annotation_scope_complete and totals["expected"] > 0,
        "annotation_scope_complete": annotation_scope_complete,
        "tool_commit": workspace.tool_commit,
        "generated_at": _now(),
        "recall_pct": (
            round(100 * totals["matched"] / totals["expected"], 2)
            if totals["expected"] else None
        ),
        "precision_pct": (
            round(100 * totals["matched"] / totals["predicted"], 2)
            if totals["predicted"] else None
        ),
        "budget_page_recall_pct": (
            round(100 * totals["predicted_budget_pages"] / totals["budget_pages"], 2)
            if totals["budget_pages"] else None
        ),
        "budget_page_precision_pct": (
            round(100 * totals["predicted_budget_pages"] / totals["predicted_pages"], 2)
            if totals["predicted_pages"] else None
        ),
        "totals": dict(totals),
        "documents": document_scores,
        "pages": all_pages,
    }
    _atomic_json(workspace_path / "score.json", report)
    post_audit = audit_workspace(workspace_path)
    report["publication_ready"] = post_audit["recall_measurement_ready"]
    report["audit_problem_count"] = len(post_audit["problems"])
    _atomic_json(workspace_path / "score.json", report)
    return report


def export_ground_truth(
    workspace_path: Path,
    output: Path,
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    audit = audit_workspace(workspace_path)
    if require_complete and not audit["recall_measurement_ready"]:
        raise ValueError(
            f"benchmark-ul nu este gata: {len(audit['problems'])} probleme de audit"
        )
    workspace = load_workspace(workspace_path)
    exported_documents = []
    for item in workspace.documents:
        document = load_document(workspace_path, item.id)
        payload = {
            "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
            "id": document.id,
            "year": document.year,
            "municipality": document.municipality,
            "siruta": document.siruta,
            "county_code": document.county_code,
            "source_path": document.source_path,
            "source_format": document.source_format,
            "source_sha256": document.source_sha256,
            "source_units": document.source_units,
            "benchmark_scope": document.benchmark_scope,
            "pages": [
                {
                    "number": page.number,
                    "label": page.label,
                    "source_type": page.source_type,
                    "sheet_name": page.sheet_name,
                    "review": page.review.model_dump(mode="json"),
                }
                for page in document.pages
                if page.review.page_kind != "unreviewed"
            ],
        }
        _atomic_json(output / "documents" / f"{document.id}.json", payload)
        exported_documents.append({
            "id": document.id,
            "municipality": document.municipality,
            "source_sha256": document.source_sha256,
            "source_units": document.source_units,
            "benchmark_scope": document.benchmark_scope,
        })
    manifest = {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "year": workspace.year,
        "exported_at": _now(),
        "tool_commit": workspace.tool_commit,
        "audit": audit,
        "documents": exported_documents,
        "raw_sources_included": False,
        "machine_suggestions_included": False,
    }
    _atomic_json(output / "manifest.json", manifest)
    return manifest


def source_path_for(workspace_path: Path, document: AnnotationDocument) -> Path:
    workspace = load_workspace(workspace_path)
    root = Path(workspace.repository_root).resolve()
    source = (root / document.source_path).resolve()
    if not source.is_relative_to(root):
        raise ValueError("calea sursei iese din repository")
    return source


def render_pdf_page(
    workspace_path: Path,
    document_id: str,
    page_number: int,
    *,
    scale: float = 1.6,
) -> Path:
    document = load_document(workspace_path, document_id)
    source_page = _page(document, page_number)
    if source_page.source_type != "pdf_page":
        raise ValueError("unitatea sursă nu este o pagină PDF")
    output = workspace_path / "renders" / document_id / f"p{page_number:04d}.png"
    if not output.exists():
        from .profilepdf import render_page

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp.png")
        render_page(source_path_for(workspace_path, document), page_number, scale=scale).save(
            temporary, format="PNG"
        )
        os.replace(temporary, output)
    return output


def workbook_window(
    workspace_path: Path,
    document_id: str,
    page_number: int,
    *,
    start_row: int = 1,
    row_limit: int = 150,
    column_limit: int = 30,
) -> dict[str, Any]:
    document = load_document(workspace_path, document_id)
    source_page = _page(document, page_number)
    if source_page.source_type != "workbook_sheet":
        raise ValueError("unitatea sursă nu este o foaie Excel")
    from .native_workbook import read_sheets

    sheets = read_sheets(source_path_for(workspace_path, document))
    sheet_name, rows = sheets[page_number - 1]
    start = max(0, start_row - 1)
    selected = rows[start:start + row_limit]
    return {
        "sheet_name": sheet_name,
        "start_row": start + 1,
        "end_row": start + len(selected),
        "total_rows": len(rows),
        "total_columns": max((len(row) for row in rows), default=0),
        "rows": [row[:column_limit] for row in selected],
    }


def page_payload(workspace_path: Path, document_id: str, page_number: int) -> dict[str, Any]:
    document = load_document(workspace_path, document_id)
    source_page = _page(document, page_number)
    payload = {
        "document": {
            "id": document.id,
            "municipality": document.municipality,
            "county_name": document.county_name,
            "source_format": document.source_format,
            "source_units": document.source_units,
            "benchmark_scope": document.benchmark_scope,
            "observed_strict_line_rate": document.observed_strict_line_rate,
        },
        "page": {
            "number": source_page.number,
            "label": source_page.label,
            "source_type": source_page.source_type,
            "sheet_name": source_page.sheet_name,
            "review": source_page.review.model_dump(mode="json"),
            "next_unreviewed": next((
                page.number for page in document.pages[page_number:]
                if page.review.page_kind == "unreviewed"
            ), None),
        },
    }
    # Classification suggestions cannot influence the independent first read.
    if source_page.review.page_kind != "unreviewed":
        payload["page"]["machine_suggestion"] = source_page.machine.model_dump(mode="json")
    # Converter facts remain locked until the exhaustive reference is frozen.
    if source_page.review.status == "frozen":
        workspace = load_workspace(workspace_path)
        config = RunConfig(runs_dir=Path(workspace.runs_dir))
        result = _prediction_result(
            config,
            source_path_for(workspace_path, document),
            workspace.year,
            document.source_units,
        )
        facts = prediction_facts(result)
        payload["page"]["comparison"] = score_page(
            source_page.review, facts, source_page.number
        )
    return payload
