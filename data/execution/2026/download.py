#!/usr/bin/env python3
"""Populate and verify the official Forexebug 2026 quarterly execution corpus."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent
USER_AGENT = "bugetclar-public-data/1.0 (+https://github.com/danparaschiv/convertor-buget-local)"
VERIFIED_STATUSES = frozenset(
    {"copied_and_verified", "downloaded_and_verified", "verified_existing"}
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quarter",
        type=int,
        choices=(1, 2),
        default=2,
        help="Quarter to process (default: 2)",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated county codes to process, for example 01,13,42",
    )
    parser.add_argument("--overwrite", action="store_true", help="Download existing files again")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel downloads (default: 4)")
    return parser.parse_args()


def load_manifest(quarter: int) -> dict[str, Any]:
    with (ROOT / f"q{quarter}" / "manifest.json").open(encoding="utf-8") as stream:
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


def workbook_text(archive: zipfile.ZipFile) -> str:
    parts: list[str] = []
    for name in archive.namelist():
        if not name.startswith("xl/") or not name.endswith(".xml"):
            continue
        try:
            root = ElementTree.fromstring(archive.read(name))
        except ElementTree.ParseError:
            continue
        parts.extend(text.strip() for text in root.itertext() if text.strip())
    return " ".join(parts)


def inspect_xlsx(path: Path, entry: dict[str, Any], expected_date: str) -> dict[str, Any]:
    if not zipfile.is_zipfile(path):
        raise ValueError("Downloaded content is not an OOXML workbook")

    with zipfile.ZipFile(path) as archive:
        required = {"[Content_Types].xml", "xl/workbook.xml"}
        missing = required.difference(archive.namelist())
        if missing:
            raise ValueError(f"Workbook is missing required parts: {', '.join(sorted(missing))}")

        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheet_count = sum(1 for element in workbook.iter() if element.tag.endswith("}sheet"))
        worksheet_names = sorted(
            name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml", name)
        )
        if not worksheet_names:
            raise ValueError("Workbook contains no worksheets")

        text = workbook_text(archive)
        normalized_text = text.upper()
        expected_fragments = (
            "RAPORT DE EXECUTIE BUGETARA COFOG3",
            "AGREGAT LA NIVEL DE ORDONATOR PRINCIPAL DE CREDITE",
            expected_date,
            entry["entity_cif"],
            entry["entity_name"],
        )
        absent = [
            fragment
            for fragment in expected_fragments
            if fragment.upper() not in normalized_text
        ]
        if absent:
            raise ValueError(f"Workbook identity check failed; missing: {absent}")

        first_sheet = ElementTree.fromstring(archive.read(worksheet_names[0]))
        rows = [element for element in first_sheet.iter() if element.tag.endswith("}row")]
        max_row = max((int(row.attrib.get("r", "0")) for row in rows), default=0)

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)

    return {
        "bytes": size,
        "sha256": digest.hexdigest(),
        "sheets": sheet_count,
        "first_sheet_rows": max_row,
    }


def process_entry(
    entry: dict[str, Any], overwrite: bool, expected_date: str
) -> dict[str, Any]:
    destination = ROOT / entry["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = {
        key: entry[key]
        for key in (
            "county_code",
            "county_name",
            "capital_siruta",
            "capital_name",
            "entity_cif",
            "entity_name",
            "path",
            "source_url",
            "report_type",
            "reporting_period",
            "report_date",
        )
    }

    try:
        if destination.exists() and not overwrite:
            return {
                **base,
                "verification_status": "verified_existing",
                **inspect_xlsx(destination, entry, expected_date),
            }

        if copy_from := entry.get("copy_from"):
            source = ROOT / copy_from
            with tempfile.TemporaryDirectory(prefix="bugetclar-execution-2026-") as temp_dir:
                candidate = Path(temp_dir) / "candidate.xlsx"
                if source.exists():
                    shutil.copyfile(source, candidate)
                    status = "copied_and_verified"
                else:
                    # A partial `--only 25` run may not have materialized the
                    # canonical Bucharest entry yet. Keep that mode usable by
                    # downloading the same official source directly.
                    run_curl(entry["source_url"], candidate)
                    status = "downloaded_and_verified"
                details = inspect_xlsx(candidate, entry, expected_date)
                candidate.replace(destination)
            return {
                **base,
                "verification_status": status,
                "copied_from": copy_from,
                **details,
            }

        with tempfile.TemporaryDirectory(prefix="bugetclar-execution-2026-") as temp_dir:
            candidate = Path(temp_dir) / "candidate.xlsx"
            run_curl(entry["source_url"], candidate)
            details = inspect_xlsx(candidate, entry, expected_date)
            candidate.replace(destination)
        return {**base, "verification_status": "downloaded_and_verified", **details}
    except Exception as exc:  # Keep the full batch running and report every failure.
        return {**base, "verification_status": "failed", "error": str(exc)}


def write_outputs(manifest: dict[str, Any], results: list[dict[str, Any]]) -> None:
    ordered = sorted(results, key=lambda item: item["county_code"])
    verified = sum(item["verification_status"] in VERIFIED_STATUSES for item in ordered)
    report = {
        "schema_version": 1,
        "year": manifest["year"],
        "quarter": manifest["quarter"],
        "report_date": manifest["report_date"],
        "report_type": manifest["report_type"],
        "generated_on": date.today().isoformat(),
        "summary": {
            "entries": len(ordered),
            "unique_entities": len({item["entity_cif"] for item in ordered}),
            "verified": verified,
            "failed": sum(item["verification_status"] == "failed" for item in ordered),
        },
        "entries": ordered,
    }
    metadata_root = ROOT / f'q{manifest["quarter"]}'
    metadata_root.mkdir(parents=True, exist_ok=True)
    (metadata_root / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    checksum_lines = [
        f'{item["sha256"]}  {item["path"]}'
        for item in ordered
        if item["verification_status"] in VERIFIED_STATUSES
    ]
    (metadata_root / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = parse_args()
    if args.jobs < 1 or args.jobs > 16:
        raise SystemExit("--jobs must be between 1 and 16")

    manifest = load_manifest(args.quarter)
    expected_date = date.fromisoformat(manifest["report_date"]).strftime("%d-%b-%y").upper()
    wanted = {part.strip().zfill(2) for part in args.only.split(",")} if args.only else None
    known_codes = {entry["county_code"] for entry in manifest["entries"]}
    if wanted:
        unknown = wanted - known_codes
        if unknown:
            raise SystemExit(f"Unknown county code(s): {', '.join(sorted(unknown))}")

    entries = [
        entry for entry in manifest["entries"] if wanted is None or entry["county_code"] in wanted
    ]
    for entry in manifest["entries"]:
        (ROOT / entry["path"]).parent.mkdir(parents=True, exist_ok=True)

    primary_entries = [entry for entry in entries if not entry.get("copy_from")]
    copy_entries = [entry for entry in entries if entry.get("copy_from")]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(process_entry, entry, args.overwrite, expected_date)
            for entry in primary_entries
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            marker = "OK" if result["verification_status"] in VERIFIED_STATUSES else "!!"
            print(
                f'{marker} {result["county_code"]} {result["capital_name"]}: '
                f'{result["verification_status"]}',
                flush=True,
            )

    for entry in copy_entries:
        result = process_entry(entry, args.overwrite, expected_date)
        results.append(result)
        marker = "OK" if result["verification_status"] in VERIFIED_STATUSES else "!!"
        print(
            f'{marker} {result["county_code"]} {result["capital_name"]}: '
            f'{result["verification_status"]}',
            flush=True,
        )

    if wanted:
        verification_path = ROOT / f'q{manifest["quarter"]}' / "verification.json"
        if verification_path.exists():
            previous = json.loads(verification_path.read_text(encoding="utf-8"))
            by_code = {item["county_code"]: item for item in previous.get("entries", [])}
            by_code.update({item["county_code"]: item for item in results})
            results = list(by_code.values())

    write_outputs(manifest, results)
    return 1 if any(item["verification_status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
