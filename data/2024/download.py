#!/usr/bin/env python3
"""Populate and verify the official 2024 county-seat budget source corpus.

Raw PDFs are reproducible inputs but deliberately stay outside Git history.
When a municipality publishes a native Excel budget, the official workbook is
kept as ``buget_orig.xls`` or ``buget_orig.xlsx`` and committed unchanged.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
USER_AGENT = "bugetclar-public-data/1.0 (+https://github.com/danparaschiv/convertor-buget-local)"
VERIFIED_STATUSES = frozenset(
    {"copied_and_verified", "downloaded_and_verified", "verified_existing"}
)
SOURCE_SUFFIXES = {"pdf": ".pdf", "xls": ".xls", "xlsx": ".xlsx"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", help="Comma-separated county codes, for example 08,13,42"
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    return parser.parse_args()


def load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


def load_expected_checksums() -> dict[str, str]:
    """Committed hashes are an immutable acquisition lock on later reruns."""
    path = ROOT / "checksums.sha256"
    if not path.exists():
        return {}
    expected = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, rel = line.split(maxsplit=1)
        expected[rel.strip()] = digest
    return expected


def run_curl(url: str, destination: Path) -> None:
    subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "3",
            "--connect-timeout",
            "30",
            "--max-time",
            "900",
            "--user-agent",
            USER_AGENT,
            "--output",
            str(destination),
            url,
        ],
        check=True,
    )


def extract_member(archive: Path, member: str, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        member_path = Path(member)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Unsafe archive member: {member}")
        with bundle.open(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _inspect_pdf(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise ValueError("Downloaded content does not have a PDF signature")
    size, digest = _sha256(path)
    return {"bytes": size, "sha256": digest, "pages": len(PdfReader(path).pages)}


def _inspect_xls(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        if stream.read(8) != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise ValueError("Downloaded content does not have an Excel 97-2003 signature")
    import xlrd

    workbook = xlrd.open_workbook(path, on_demand=True)
    try:
        sheets = workbook.nsheets
    finally:
        workbook.release_resources()
    size, digest = _sha256(path)
    return {"bytes": size, "sha256": digest, "sheets": sheets}


def _inspect_xlsx(path: Path) -> dict[str, Any]:
    if not zipfile.is_zipfile(path):
        raise ValueError("Downloaded content is not an OOXML workbook")
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = len(workbook.sheetnames)
    finally:
        workbook.close()
    size, digest = _sha256(path)
    return {"bytes": size, "sha256": digest, "sheets": sheets}


def inspect_source(path: Path, source_format: str) -> dict[str, Any]:
    return {
        "pdf": _inspect_pdf,
        "xls": _inspect_xls,
        "xlsx": _inspect_xlsx,
    }[source_format](path)


def _combine_pdfs(parts: list[Path], destination: Path) -> None:
    writer = PdfWriter()
    for part in parts:
        for page in PdfReader(part).pages:
            writer.add_page(page)
    with destination.open("wb") as stream:
        writer.write(stream)


def _download_candidate(entry: dict[str, Any], temp_root: Path) -> Path:
    source_format = entry.get("source_format", "pdf")
    candidate = temp_root / f"candidate{SOURCE_SUFFIXES[source_format]}"
    parts = entry.get("source_parts") or []
    if parts:
        if source_format != "pdf":
            raise ValueError("source_parts is supported only for PDF bundles")
        downloaded_parts = []
        for index, url in enumerate(parts, 1):
            part = temp_root / f"part-{index}.pdf"
            run_curl(url, part)
            _inspect_pdf(part)
            downloaded_parts.append(part)
        _combine_pdfs(downloaded_parts, candidate)
        return candidate

    downloaded = temp_root / "download"
    run_curl(entry["source_url"], downloaded)
    if member := entry.get("archive_member"):
        extract_member(downloaded, member, candidate)
    else:
        downloaded.replace(candidate)
    return candidate


def process_entry(
    entry: dict[str, Any], overwrite: bool, expected_sha256: str | None = None
) -> dict[str, Any]:
    destination = ROOT / entry["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_format = entry.get("source_format", "pdf")
    expected_suffix = SOURCE_SUFFIXES.get(source_format)
    if expected_suffix is None or destination.suffix.lower() != expected_suffix:
        raise ValueError(
            f'{entry["county_code"]}: path/format disagree ({destination.name}, {source_format})'
        )
    if source_format != "pdf" and destination.name not in {
        "buget_orig.xls",
        "buget_orig.xlsx",
    }:
        raise ValueError("Native Excel sources must be named buget_orig.xls[x]")

    base = {
        "county_code": entry["county_code"],
        "county_name": entry["county_name"],
        "capital_siruta": entry["capital_siruta"],
        "capital_name": entry["capital_name"],
        "path": entry["path"],
        "source_format": source_format,
        "document_status": entry["document_status"],
        "source_url": entry.get("source_url"),
        "source_parts": entry.get("source_parts"),
        "record_url": entry.get("record_url"),
    }
    has_source = bool(entry.get("source_url") or entry.get("source_parts"))
    if not has_source:
        return {
            **base,
            "verification_status": "not_available",
            "notes": entry.get("notes"),
        }

    try:
        if destination.exists() and not overwrite:
            details = inspect_source(destination, source_format)
            if expected_sha256 and details["sha256"] != expected_sha256:
                raise ValueError(
                    f"SHA-256 mismatch for {entry['path']}: "
                    f"expected {expected_sha256}, got {details['sha256']}"
                )
            return {
                **base,
                "verification_status": "verified_existing",
                **details,
            }

        if copy_from := entry.get("copy_from"):
            source = ROOT / copy_from
            if source.exists():
                with tempfile.TemporaryDirectory(prefix="bugetclar-2024-") as temp_dir:
                    candidate = Path(temp_dir) / destination.name
                    shutil.copyfile(source, candidate)
                    details = inspect_source(candidate, source_format)
                    if expected_sha256 and details["sha256"] != expected_sha256:
                        raise ValueError(
                            f"SHA-256 mismatch for {entry['path']}: "
                            f"expected {expected_sha256}, got {details['sha256']}"
                        )
                    candidate.replace(destination)
                return {
                    **base,
                    "verification_status": "copied_and_verified",
                    "copied_from": copy_from,
                    **details,
                }

        with tempfile.TemporaryDirectory(prefix="bugetclar-2024-") as temp_dir:
            candidate = _download_candidate(entry, Path(temp_dir))
            details = inspect_source(candidate, source_format)
            if expected_sha256 and details["sha256"] != expected_sha256:
                raise ValueError(
                    f"SHA-256 mismatch for {entry['path']}: "
                    f"expected {expected_sha256}, got {details['sha256']}"
                )
            candidate.replace(destination)
        return {**base, "verification_status": "downloaded_and_verified", **details}
    except Exception as exc:  # Keep the full batch running and report each failure.
        return {**base, "verification_status": "failed", "error": str(exc)}


def write_outputs(manifest: dict[str, Any], results: list[dict[str, Any]]) -> None:
    ordered = sorted(results, key=lambda item: item["county_code"])
    report = {
        "schema_version": 2,
        "year": manifest["year"],
        "generated_on": date.today().isoformat(),
        "raw_pdf_policy": manifest["raw_pdf_policy"],
        "summary": {
            "entries": len(ordered),
            "verified": sum(
                item["verification_status"] in VERIFIED_STATUSES for item in ordered
            ),
            "native_excel": sum(
                item["verification_status"] in VERIFIED_STATUSES
                and item.get("source_format") in {"xls", "xlsx"}
                for item in ordered
            ),
            "not_available": sum(
                item["verification_status"] == "not_available" for item in ordered
            ),
            "failed": sum(item["verification_status"] == "failed" for item in ordered),
        },
        "entries": ordered,
    }
    (ROOT / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksum_lines = [
        f'{item["sha256"]}  {item["path"]}'
        for item in ordered
        if item["verification_status"] in VERIFIED_STATUSES
    ]
    (ROOT / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + ("\n" if checksum_lines else ""),
        encoding="utf-8",
    )


def write_sources(manifest: dict[str, Any]) -> None:
    lines = [
        "# Surse oficiale pentru bugetele municipale 2024",
        "",
        "| Județ | Municipiu | Format | Stare document | Sursă | Înregistrare |",
        "|---|---|---|---|---|---|",
    ]
    for entry in manifest["entries"]:
        urls = entry.get("source_parts") or (
            [entry["source_url"]] if entry.get("source_url") else []
        )
        source = ", ".join(
            f"[Descărcare {index}]({url})" for index, url in enumerate(urls, 1)
        ) or "—"
        record_url = entry.get("record_url")
        record = f"[Pagina oficială]({record_url})" if record_url else "—"
        lines.append(
            f'| {entry["county_name"]} | {entry["capital_name"]} | '
            f'`{entry.get("source_format", "pdf")}` | '
            f'`{entry["document_status"]}` | {source} | {record} |'
        )
    lines.extend(
        [
            "",
            f'Surse auditate la {manifest["audited_on"]}. PDF-urile brute sunt',
            "descărcate reproductibil, verificate și excluse din istoricul Git.",
            "Fișierele Excel publicate nativ de municipalități sunt păstrate byte-for-byte.",
        ]
    )
    (ROOT / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not 1 <= args.jobs <= 16:
        raise SystemExit("--jobs must be between 1 and 16")
    manifest = load_manifest()
    expected_checksums = load_expected_checksums()
    write_sources(manifest)
    wanted = (
        {part.strip().zfill(2) for part in args.only.split(",")} if args.only else None
    )
    entries = [
        entry
        for entry in manifest["entries"]
        if wanted is None or entry["county_code"] in wanted
    ]
    known_codes = {entry["county_code"] for entry in manifest["entries"]}
    if wanted and (unknown := wanted - known_codes):
        raise SystemExit(f"Unknown county code(s): {', '.join(sorted(unknown))}")

    for entry in manifest["entries"]:
        (ROOT / entry["path"]).parent.mkdir(parents=True, exist_ok=True)

    primary = [entry for entry in entries if not entry.get("copy_from")]
    copied = [entry for entry in entries if entry.get("copy_from")]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(
                process_entry,
                entry,
                args.overwrite,
                expected_checksums.get(entry["path"]),
            )
            for entry in primary
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            marker = "OK" if result["verification_status"] in VERIFIED_STATUSES else "--"
            print(
                f'{marker} {result["county_code"]} {result["capital_name"]}: '
                f'{result["verification_status"]}',
                flush=True,
            )
    for entry in copied:
        result = process_entry(
            entry, args.overwrite, expected_checksums.get(entry["path"])
        )
        results.append(result)
        marker = "OK" if result["verification_status"] in VERIFIED_STATUSES else "--"
        print(
            f'{marker} {result["county_code"]} {result["capital_name"]}: '
            f'{result["verification_status"]}',
            flush=True,
        )

    if wanted and (ROOT / "verification.json").exists():
        previous = json.loads((ROOT / "verification.json").read_text(encoding="utf-8"))
        by_code = {item["county_code"]: item for item in previous.get("entries", [])}
        by_code.update({item["county_code"]: item for item in results})
        results = list(by_code.values())

    write_outputs(manifest, results)
    failures = [item for item in results if item["verification_status"] == "failed"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
