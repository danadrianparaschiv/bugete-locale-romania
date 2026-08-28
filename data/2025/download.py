#!/usr/bin/env python3
"""Populate and verify the official 2025 county-seat budget PDF corpus."""

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

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
USER_AGENT = "bugetclar-public-data/1.0 (+https://github.com/danparaschiv/convertor-buget-local)"
VERIFIED_STATUSES = frozenset(
    {"copied_and_verified", "downloaded_and_verified", "verified_existing"}
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        help="Comma-separated county codes to process, for example 08,13,42",
    )
    parser.add_argument("--overwrite", action="store_true", help="Download existing PDFs again")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel downloads (default: 4)")
    return parser.parse_args()


def load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


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
            "600",
            "--user-agent",
            USER_AGENT,
            "--output",
            str(destination),
            url,
        ],
        check=True,
    )


def extract_pdf(archive: Path, member: str, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        member_path = Path(member)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Unsafe archive member: {member}")
        with bundle.open(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)


def pdf_page_count(path: Path) -> int | None:
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        return None
    result = subprocess.run(
        [pdfinfo, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.partition(":")[2].strip())
    return None


def inspect_pdf(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        prefix = stream.read(1024)
    if b"%PDF-" not in prefix:
        raise ValueError("Downloaded content does not have a PDF signature")

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)

    return {
        "bytes": size,
        "sha256": digest.hexdigest(),
        "pages": pdf_page_count(path),
    }


def process_entry(entry: dict[str, Any], overwrite: bool) -> dict[str, Any]:
    destination = ROOT / entry["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = {
        "county_code": entry["county_code"],
        "county_name": entry["county_name"],
        "capital_siruta": entry["capital_siruta"],
        "capital_name": entry["capital_name"],
        "path": entry["path"],
        "document_status": entry["document_status"],
        "source_url": entry.get("source_url"),
        "record_url": entry.get("record_url"),
    }

    if not entry.get("source_url"):
        return {**base, "verification_status": "not_available", "notes": entry.get("notes")}

    try:
        if destination.exists() and not overwrite:
            return {**base, "verification_status": "verified_existing", **inspect_pdf(destination)}

        if copy_from := entry.get("copy_from"):
            source = ROOT / copy_from
            if source.exists():
                with tempfile.TemporaryDirectory(prefix="bugetclar-2025-") as temp_dir:
                    candidate = Path(temp_dir) / "candidate.pdf"
                    shutil.copyfile(source, candidate)
                    details = inspect_pdf(candidate)
                    candidate.replace(destination)
                return {
                    **base,
                    "verification_status": "copied_and_verified",
                    "copied_from": copy_from,
                    **details,
                }

        with tempfile.TemporaryDirectory(prefix="bugetclar-2025-") as temp_dir:
            downloaded = Path(temp_dir) / "download"
            candidate = Path(temp_dir) / "candidate.pdf"
            run_curl(entry["source_url"], downloaded)
            if member := entry.get("archive_member"):
                extract_pdf(downloaded, member, candidate)
            else:
                downloaded.replace(candidate)
            details = inspect_pdf(candidate)
            candidate.replace(destination)
        return {**base, "verification_status": "downloaded_and_verified", **details}
    except Exception as exc:  # Keep the full batch running and report each source failure.
        return {**base, "verification_status": "failed", "error": str(exc)}


def write_outputs(manifest: dict[str, Any], results: list[dict[str, Any]]) -> None:
    ordered = sorted(results, key=lambda item: item["county_code"])
    report = {
        "schema_version": 1,
        "year": manifest["year"],
        "generated_on": date.today().isoformat(),
        "summary": {
            "entries": len(ordered),
            "verified": sum(
                item["verification_status"] in VERIFIED_STATUSES for item in ordered
            ),
            "not_available": sum(
                item["verification_status"] == "not_available" for item in ordered
            ),
            "failed": sum(item["verification_status"] == "failed" for item in ordered),
        },
        "entries": ordered,
    }
    (ROOT / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    checksum_lines = [
        f'{item["sha256"]}  {item["path"]}'
        for item in ordered
        if item["verification_status"] in VERIFIED_STATUSES
    ]
    (ROOT / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def write_sources(manifest: dict[str, Any]) -> None:
    lines = [
        "# Official 2025 municipal budget sources",
        "",
        "| County | City | Status | Source URL | Record URL |",
        "|---|---|---|---|---|",
    ]
    for entry in manifest["entries"]:
        source_url = entry.get("source_url")
        record_url = entry.get("record_url")
        source = f"[Download]({source_url})" if source_url else "—"
        record = f"[Official record]({record_url})" if record_url else "—"
        lines.append(
            f'| {entry["county_name"]} | {entry["capital_name"]} | '
            f'`{entry["document_status"]}` | {source} | {record} |'
        )
    lines.extend(
        [
            "",
            "Sources were audited on 2026-08-23 and are restricted to official ",
            "municipal or local-council domains. See `manifest.json` for notes.",
        ]
    )
    (ROOT / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.jobs < 1 or args.jobs > 16:
        raise SystemExit("--jobs must be between 1 and 16")

    manifest = load_manifest()
    write_sources(manifest)
    wanted = (
        {part.strip().zfill(2) for part in args.only.split(",")} if args.only else None
    )
    entries = [
        entry for entry in manifest["entries"] if wanted is None or entry["county_code"] in wanted
    ]
    known_codes = {entry["county_code"] for entry in manifest["entries"]}
    if wanted:
        unknown = wanted - known_codes
        if unknown:
            raise SystemExit(f"Unknown county code(s): {', '.join(sorted(unknown))}")

    # Always materialize the complete 42-directory structure.
    for entry in manifest["entries"]:
        (ROOT / entry["path"]).parent.mkdir(parents=True, exist_ok=True)

    primary_entries = [entry for entry in entries if not entry.get("copy_from")]
    copy_entries = [entry for entry in entries if entry.get("copy_from")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(process_entry, entry, args.overwrite)
            for entry in primary_entries
        ]
        results: list[dict[str, Any]] = []
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            marker = "OK" if result["verification_status"] in VERIFIED_STATUSES else "--"
            print(
                f'{marker} {result["county_code"]} {result["capital_name"]}: '
                f'{result["verification_status"]}',
                flush=True,
            )

    # Copy-derived entries run after their canonical source so a fresh full
    # download never fetches the same large public document twice.
    for entry in copy_entries:
        result = process_entry(entry, args.overwrite)
        results.append(result)
        marker = "OK" if result["verification_status"] in VERIFIED_STATUSES else "--"
        print(
            f'{marker} {result["county_code"]} {result["capital_name"]}: '
            f'{result["verification_status"]}',
            flush=True,
        )

    if wanted:
        # Preserve verification data for unselected entries when doing a partial refresh.
        verification_path = ROOT / "verification.json"
        if verification_path.exists():
            previous = json.loads(verification_path.read_text(encoding="utf-8"))
            by_code = {item["county_code"]: item for item in previous.get("entries", [])}
            by_code.update({item["county_code"]: item for item in results})
            results = list(by_code.values())

    write_outputs(manifest, results)
    failures = [item for item in results if item["verification_status"] == "failed"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
