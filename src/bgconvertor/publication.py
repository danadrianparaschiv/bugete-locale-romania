"""Versioned, auditable publication of corpus conversion artifacts.

The manifest is the commit point: workbook and analysis files are fully
written first, then the manifest atomically records their hashes and common
bundle id.  A failed manifest update restores the previous artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import write_analysis
from .export import export as export_workbook
from .manifest import CityEntry, Manifest
from .model import ConversionResult
from .runstore import file_sha256

PUBLICATION_SCHEMA_VERSION = 1
MAX_PUBLIC_LLM_COST_USD = 5.0


def _canonical_result(result: ConversionResult) -> bytes:
    payload = result.model_dump(mode="json")
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def publication_identity(result: ConversionResult, pdf: Path) -> dict[str, Any]:
    """Stable identity shared by all artifacts from the same conversion result."""
    source_sha = file_sha256(pdf)
    h = hashlib.sha256()
    h.update(f"publication:{PUBLICATION_SCHEMA_VERSION}:".encode())
    h.update(source_sha.encode())
    h.update(_canonical_result(result))
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "bundle_id": h.hexdigest()[:24],
        "source_pdf_sha256": source_sha,
    }


def _quality_fields(result: ConversionResult) -> dict[str, Any]:
    stats = result.stats()
    return {
        "schema_version": stats["quality_schema_version"],
        "metric": stats["metric"],
        "recall_measured": stats["recall_measured"],
        "documents": stats["documents"],
        "lines": stats["lines"],
        "lines_strictly_verified": stats["lines_strictly_verified"],
        "pct_lines_strictly_verified": stats["pct_lines_strictly_verified"],
        "numeric_cells": stats["numeric_cells"],
        "numeric_cells_strictly_verified": stats["numeric_cells_strictly_verified"],
        "pct_numeric_cells_strictly_verified": stats[
            "pct_numeric_cells_strictly_verified"
        ],
        "scope": stats["scope"],
        "errors": stats["issues"]["error"],
        "warnings": stats["issues"]["warning"],
        "info": stats["issues"]["info"],
    }


def _pending_path(target: Path, bundle_id: str) -> Path:
    if target.suffix == ".xlsx":
        return target.with_name(f".{target.stem}.{bundle_id}.pending.xlsx")
    return target.with_name(f".{target.name}.{bundle_id}.pending")


def _backup_path(target: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".bak", dir=target.parent)
    os.close(fd)
    os.unlink(name)
    return Path(name)


def _restore(targets: list[tuple[Path, Path | None]]) -> None:
    for target, backup in reversed(targets):
        if backup is not None and backup.exists():
            os.replace(backup, target)
        else:
            target.unlink(missing_ok=True)


def publish_corpus_result(
    result: ConversionResult,
    pdf: Path,
    workbook: Path,
    manifest: Manifest,
    *,
    llm_preset: str | None = None,
    llm_cost_usd: float = 0.0,
    llm_lifetime_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Publish workbook + analysis + manifest as one auditable bundle.

    Only a complete-PDF result may enter the public corpus.  The two artifact
    files are staged before any public path changes; the manifest is written
    last and acts as the bundle's commit record.
    """
    stats = result.stats()
    if not stats["scope"]["complete_pdf"]:
        raise ValueError("public corpus publication requires a complete-PDF conversion")
    if not any(document.suffix == "02" for document in result.documents):
        raise ValueError(
            "public corpus publication requires a main local-budget document "
            "with suffix .02"
        )
    if not 0 <= llm_cost_usd <= MAX_PUBLIC_LLM_COST_USD:
        raise ValueError(
            f"public LLM cost must be between $0 and ${MAX_PUBLIC_LLM_COST_USD:.2f}"
        )

    city = manifest.by_pdf(pdf)
    if city is None:
        raise ValueError(f"{pdf} is not governed by {manifest.path}")

    analysis = pdf.with_name("analysis.json")
    identity = publication_identity(result, pdf)
    bundle_id = identity["bundle_id"]
    staged_workbook = _pending_path(workbook, bundle_id)
    staged_analysis = _pending_path(analysis, bundle_id)
    workbook.parent.mkdir(parents=True, exist_ok=True)

    try:
        export_workbook(result, staged_workbook, publication=identity)
        write_analysis(result, staged_analysis, publication=identity)

        artifacts = {
            **identity,
            "workbook": {
                "path": workbook.name,
                "sha256": file_sha256(staged_workbook),
                "bytes": staged_workbook.stat().st_size,
            },
            "analysis": {
                "path": analysis.name,
                "sha256": file_sha256(staged_analysis),
                "bytes": staged_analysis.stat().st_size,
            },
        }
        quality = _quality_fields(result)
        fields: dict[str, Any] = {
            "status": "converted",
            "workbook": workbook.name,
            "analysis": analysis.name,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool_version": __version__,
            "quality": quality,
            "artifacts": artifacts,
            # Compatibility fields for existing site/data consumers.
            "lines": quality["lines"],
            "pct_clean": quality["pct_lines_strictly_verified"],
            "errors": quality["errors"],
            "warnings": quality["warnings"],
        }
        if llm_preset:
            fields["llm_preset"] = llm_preset
        fields["llm_cost_usd"] = round(llm_cost_usd, 4)
        fields["llm_cost_scope"] = "current_run_incremental"
        if llm_lifetime_cost_usd is not None:
            fields["llm_lifetime_cost_usd"] = round(llm_lifetime_cost_usd, 4)

        replaced: list[tuple[Path, Path | None]] = []
        try:
            for target, staged in ((workbook, staged_workbook), (analysis, staged_analysis)):
                backup = _backup_path(target) if target.exists() else None
                if backup is not None:
                    os.replace(target, backup)
                replaced.append((target, backup))
                os.replace(staged, target)
            manifest.set_status(city, **fields)
        except BaseException:
            _restore(replaced)
            raise
        finally:
            for _, backup in replaced:
                if backup is not None:
                    backup.unlink(missing_ok=True)

        return {**fields, "workbook": workbook, "analysis": analysis}
    finally:
        staged_workbook.unlink(missing_ok=True)
        staged_analysis.unlink(missing_ok=True)


@dataclass
class AuditIssue:
    severity: str
    code: str
    message: str


@dataclass
class ArtifactAudit:
    year: int | None
    siruta: str
    municipality: str
    pdf: str
    status: str
    issues: list[AuditIssue] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        return self.status in {"verified", "legacy_consistent"}

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["trusted"] = self.trusted
        return out


@dataclass
class MigrationCandidate:
    year: int | None
    siruta: str
    municipality: str
    pdf: Path
    manifest_path: Path
    preset: str | None
    previous_artifact_status: str


def _issue(out: list[AuditIssue], severity: str, code: str, message: str) -> None:
    out.append(AuditIssue(severity=severity, code=code, message=message))


def _read_workbook_summary(path: Path, issues: list[AuditIssue]) -> dict[str, Any]:
    if not path.exists():
        _issue(issues, "error", "missing_workbook", f"missing {path.name}")
        return {}
    try:
        import openpyxl

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb["Sumar calitate"]
            return {
                str(row[0]): row[1]
                for row in ws.iter_rows(values_only=True)
                if row and row[0] is not None
            }
        finally:
            wb.close()
    except Exception as exc:  # noqa: BLE001 - corrupt public artifact is audit data
        _issue(issues, "error", "invalid_workbook", f"cannot read {path.name}: {exc}")
        return {}


def _read_analysis(path: Path, issues: list[AuditIssue]) -> dict[str, Any]:
    if not path.exists():
        _issue(issues, "error", "missing_analysis", f"missing {path.name}")
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _issue(issues, "error", "invalid_analysis", f"cannot read {path.name}: {exc}")
        return {}


def _metrics_from_workbook(rows: dict[str, Any]) -> dict[str, Any]:
    complete = rows.get("PDF complet")
    recall = rows.get("Recall masurat")
    return {
        "schema_version": rows.get("Schema calitate"),
        "metric": rows.get("Metrica de calitate"),
        "recall_measured": (
            recall if isinstance(recall, bool) else False if recall == "nu" else True
            if recall == "da" else None
        ),
        "documents": rows.get("Documente"),
        "lines": rows.get("Linii de date"),
        "lines_strictly_verified": rows.get("Linii strict verificate"),
        "pct": rows.get("% linii strict verificate", rows.get("% curat")),
        "numeric_cells": rows.get("Celule numerice"),
        "numeric_cells_strictly_verified": rows.get("Celule numerice strict verificate"),
        "pct_numeric_cells_strictly_verified": rows.get(
            "% celule numerice strict verificate"
        ),
        "errors": rows.get("Erori"),
        "warnings": rows.get("Avertismente"),
        "info": rows.get("Informatii"),
        "pages_expected": rows.get("Pagini asteptate"),
        "pages_selected": rows.get("Pagini selectate"),
        "pages_processed": rows.get("Pagini procesate"),
        "complete_pdf": (
            complete if isinstance(complete, bool) else False if complete == "nu" else True
            if complete == "da" else None
        ),
    }


def _metrics_from_analysis(data: dict[str, Any]) -> dict[str, Any]:
    q = data.get("quality") or {}
    scope = q.get("scope") or {}
    return {
        "schema_version": q.get("schema_version"),
        "metric": q.get("metric"),
        "recall_measured": q.get("recall_measured"),
        "documents": q.get("documents"),
        "lines": q.get("lines"),
        "lines_strictly_verified": q.get("lines_strictly_verified"),
        "pct": q.get("pct_lines_strictly_verified", q.get("pct_clean")),
        "numeric_cells": q.get("numeric_cells"),
        "numeric_cells_strictly_verified": q.get("numeric_cells_strictly_verified"),
        "pct_numeric_cells_strictly_verified": q.get(
            "pct_numeric_cells_strictly_verified"
        ),
        "errors": q.get("errors"),
        "warnings": q.get("warnings"),
        "info": q.get("info"),
        "pages_expected": scope.get("pages_expected"),
        "pages_selected": scope.get("pages_selected"),
        "pages_processed": scope.get("pages_processed"),
        "complete_pdf": scope.get("complete_pdf"),
    }


def _metrics_from_manifest(conv: dict[str, Any]) -> dict[str, Any]:
    q = conv.get("quality") or {}
    scope = q.get("scope") or {}
    return {
        "schema_version": q.get("schema_version"),
        "metric": q.get("metric"),
        "recall_measured": q.get("recall_measured"),
        "documents": q.get("documents"),
        "lines": q.get("lines", conv.get("lines")),
        "lines_strictly_verified": q.get("lines_strictly_verified"),
        "pct": q.get("pct_lines_strictly_verified", conv.get("pct_clean")),
        "numeric_cells": q.get("numeric_cells"),
        "numeric_cells_strictly_verified": q.get("numeric_cells_strictly_verified"),
        "pct_numeric_cells_strictly_verified": q.get(
            "pct_numeric_cells_strictly_verified"
        ),
        "errors": q.get("errors", conv.get("errors")),
        "warnings": q.get("warnings", conv.get("warnings")),
        "info": q.get("info"),
        "pages_expected": scope.get("pages_expected"),
        "pages_selected": scope.get("pages_selected"),
        "pages_processed": scope.get("pages_processed"),
        "complete_pdf": scope.get("complete_pdf"),
    }


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 0.05
    return a == b


def _compare_metrics(
    sources: dict[str, dict[str, Any]], issues: list[AuditIssue], *, modern: bool
) -> None:
    required = {"lines", "pct", "errors", "warnings"}
    keys = set().union(*(values.keys() for values in sources.values()))
    if modern:
        required = keys
    for key in sorted(keys):
        present = {name: values.get(key) for name, values in sources.items()
                   if values.get(key) is not None}
        if key in required and len(present) != len(sources):
            missing = sorted(set(sources) - set(present))
            _issue(
                issues, "error", "missing_metric",
                f"{key} absent from {', '.join(missing)}",
            )
            continue
        if len(present) < 2:
            continue
        first = next(iter(present.values()))
        if not all(_same(first, value) for value in present.values()):
            detail = ", ".join(f"{name}={value}" for name, value in present.items())
            _issue(issues, "error", "metric_mismatch", f"{key}: {detail}")


def _verify_hash(
    path: Path, expected: str | None, label: str, issues: list[AuditIssue]
) -> None:
    if not expected:
        _issue(issues, "error", "missing_hash", f"missing {label} sha256")
    elif path.exists() and file_sha256(path) != expected:
        _issue(issues, "error", "hash_mismatch", f"{label} sha256 does not match")


def audit_city(manifest: Manifest, city: CityEntry) -> ArtifactAudit:
    conv = city.entry.get("conversion") or {}
    if conv.get("status") != "converted":
        return ArtifactAudit(
            manifest.year, city.siruta, city.name, str(city.pdf), "not_converted", []
        )

    issues: list[AuditIssue] = []
    workbook = city.pdf.with_name(conv.get("workbook") or city.pdf.with_suffix(".xlsx").name)
    analysis_path = city.pdf.with_name(conv.get("analysis") or "analysis.json")
    workbook_rows = _read_workbook_summary(workbook, issues)
    analysis = _read_analysis(analysis_path, issues)
    sources = {
        "workbook": _metrics_from_workbook(workbook_rows),
        "analysis": _metrics_from_analysis(analysis),
        "manifest": _metrics_from_manifest(conv),
    }
    artifacts = conv.get("artifacts") or {}
    modern = bool(artifacts)
    _compare_metrics(sources, issues, modern=modern)

    if modern:
        if artifacts.get("schema_version") != PUBLICATION_SCHEMA_VERSION:
            _issue(issues, "error", "publication_schema", "unsupported publication schema")
        cost = conv.get("llm_cost_usd")
        if not isinstance(cost, (int, float)):
            _issue(issues, "error", "missing_cost", "public bundle has no LLM cost")
        elif not 0 <= cost <= MAX_PUBLIC_LLM_COST_USD:
            _issue(
                issues, "error", "cost_limit",
                f"LLM cost ${cost:.4f} exceeds public limit ${MAX_PUBLIC_LLM_COST_USD:.2f}",
            )
        if conv.get("llm_cost_scope") != "current_run_incremental":
            _issue(issues, "error", "cost_scope", "LLM cost scope is missing or unknown")
        bundle_id = artifacts.get("bundle_id")
        if not bundle_id:
            _issue(issues, "error", "missing_bundle", "manifest has no bundle id")
        if analysis.get("publication", {}).get("bundle_id") != bundle_id:
            _issue(issues, "error", "bundle_mismatch", "analysis bundle id does not match")
        if workbook_rows.get("Bundle conversie") != bundle_id:
            _issue(issues, "error", "bundle_mismatch", "workbook bundle id does not match")

        source_sha = artifacts.get("source_pdf_sha256")
        if analysis.get("publication", {}).get("source_pdf_sha256") != source_sha:
            _issue(issues, "error", "source_hash_mismatch", "analysis source hash differs")
        if workbook_rows.get("SHA-256 PDF sursa") != source_sha:
            _issue(issues, "error", "source_hash_mismatch", "workbook source hash differs")

        for label, path, record in (
            ("workbook", workbook, artifacts.get("workbook") or {}),
            ("analysis", analysis_path, artifacts.get("analysis") or {}),
        ):
            if record.get("path") != path.name:
                _issue(issues, "error", "artifact_path_mismatch", f"{label} path differs")
            if path.exists() and record.get("bytes") != path.stat().st_size:
                _issue(issues, "error", "artifact_size_mismatch", f"{label} size differs")

        _verify_hash(workbook, (artifacts.get("workbook") or {}).get("sha256"),
                     "workbook", issues)
        _verify_hash(analysis_path, (artifacts.get("analysis") or {}).get("sha256"),
                     "analysis", issues)
        if city.pdf.exists():
            _verify_hash(city.pdf, source_sha, "source PDF", issues)
        elif source_sha:
            _issue(
                issues, "warning", "source_unavailable",
                "source PDF is not checked out; recorded hash could not be verified",
            )
        else:
            _issue(issues, "error", "missing_hash", "missing source PDF sha256")
    else:
        _issue(
            issues, "warning", "legacy_metadata",
            "legacy artifacts have no bundle id or sha256 provenance",
        )

    has_errors = any(i.severity == "error" for i in issues)
    status = "inconsistent" if has_errors else "verified" if modern else "legacy_consistent"
    return ArtifactAudit(
        manifest.year, city.siruta, city.name, str(city.pdf), status, issues
    )


def audit_data(data_root: Path) -> list[ArtifactAudit]:
    """Audit every unique converted PDF under data/<year>/manifest.json."""
    results = []
    for path in sorted(data_root.glob("[0-9][0-9][0-9][0-9]/manifest.json")):
        manifest = Manifest(path)
        results.extend(audit_city(manifest, city) for city in manifest.cities())
    return results


def migration_candidates(
    data_root: Path, *, include_verified: bool = False
) -> list[MigrationCandidate]:
    """Converted entries which are not yet verified modern bundles."""
    candidates = []
    for path in sorted(data_root.glob("[0-9][0-9][0-9][0-9]/manifest.json")):
        manifest = Manifest(path)
        for city in manifest.cities():
            conv = city.entry.get("conversion") or {}
            if conv.get("status") != "converted":
                continue
            audit = audit_city(manifest, city)
            if audit.status == "verified" and not include_verified:
                continue
            candidates.append(MigrationCandidate(
                year=manifest.year,
                siruta=city.siruta,
                municipality=city.name,
                pdf=city.pdf,
                manifest_path=manifest.path,
                preset=conv.get("llm_preset"),
                previous_artifact_status=audit.status,
            ))
    return candidates


def audit_report(results: list[ArtifactAudit]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    converted = [result for result in results if result.status != "not_converted"]
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "summary": {
            "entries": len(results),
            "converted": len(converted),
            "trusted": sum(result.trusted for result in converted),
            "inconsistent": sum(result.status == "inconsistent" for result in converted),
            "by_status": counts,
        },
        "files": [result.to_dict() for result in results],
    }
