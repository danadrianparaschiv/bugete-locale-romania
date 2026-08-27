"""Public, offline contract for the historical 2024 acquisition round."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from bgconvertor.manifest import Manifest
from bgconvertor.publication import audit_city

ROOT = Path(__file__).parents[1]


def test_2024_manifest_covers_every_county_seat_with_explicit_source_state():
    manifest = json.loads((ROOT / "data/2024/manifest.json").read_text())
    entries = manifest["entries"]

    assert manifest["schema_version"] == 2
    assert manifest["year"] == 2024
    assert len(entries) == 42
    assert {entry["county_code"] for entry in entries} == {
        f"{code:02d}" for code in range(1, 43)
    }
    assert all(entry.get("capital_siruta") for entry in entries)
    assert all(entry.get("source_format") in {"pdf", "xls", "xlsx"} for entry in entries)

    unavailable = [
        entry for entry in entries
        if (entry.get("conversion") or {}).get("status") == "missing_pdf"
    ]
    assert {entry["county_code"] for entry in unavailable} == {"27"}
    available = [entry for entry in entries if entry not in unavailable]
    assert all(entry.get("source_url") or entry.get("source_parts") for entry in available)
    assert all(entry.get("record_url") and entry.get("notes") for entry in unavailable)


def test_2024_prefers_detailed_official_annexes_over_summary_documents():
    manifest = json.loads((ROOT / "data/2024/manifest.json").read_text())
    by_county = {entry["county_code"]: entry for entry in manifest["entries"]}

    assert by_county["06"]["archive_member"].endswith(
        "Anexa 2 la PH rectificare.pdf"
    )
    assert "HCL_63_2024-2.pdf" in by_county["07"]["source_url"]
    assert "id_fisier=5427" in by_county["08"]["source_url"]
    assert "Anexa nr. 1" in by_county["08"]["notes"]
    assert "anexa%2001%20buget%2002.pdf" in by_county["21"]["source_url"]
    assert len(by_county["24"]["source_parts"]) == 2
    assert any("anunt.pdf" in url for url in by_county["24"]["source_parts"])
    assert "h_18_070224.pdf" in by_county["30"]["source_url"]
    assert "DownloadBinaryFileOnTenantOrHost" in by_county["38"]["source_url"]
    assert "HCL-NR.-51_28.03.2024" in by_county["39"]["source_url"]


def test_2024_native_excel_sources_are_preserved_and_hash_verified():
    manifest = json.loads((ROOT / "data/2024/manifest.json").read_text())
    verification = json.loads((ROOT / "data/2024/verification.json").read_text())
    verified_by_path = {entry["path"]: entry for entry in verification["entries"]}

    native = [entry for entry in manifest["entries"] if entry["source_format"] != "pdf"]
    assert len(native) == 3
    for entry in native:
        rel = Path(entry["path"])
        assert rel.name in {"buget_orig.xls", "buget_orig.xlsx"}
        source = ROOT / "data/2024" / rel
        assert source.exists()
        assert verified_by_path[entry["path"]]["verification_status"] in {
            "copied_and_verified", "downloaded_and_verified", "verified_existing"
        }
        assert hashlib.sha256(source.read_bytes()).hexdigest() == \
            verified_by_path[entry["path"]]["sha256"]

    checksum_rows = {
        rel: digest
        for digest, rel in (
            line.split(maxsplit=1)
            for line in (ROOT / "data/2024/checksums.sha256").read_text().splitlines()
        )
    }
    verified = {
        entry["path"]: entry["sha256"]
        for entry in verification["entries"]
        if entry["verification_status"] in {
            "copied_and_verified", "downloaded_and_verified", "verified_existing"
        }
    }
    assert checksum_rows == verified


def test_2024_uses_a_hash_pinned_historical_nomenclator():
    manifest = json.loads((ROOT / "data/2024/manifest.json").read_text())
    registry = json.loads(
        (ROOT / "reference/nomenclator/2024/registry.json").read_text()
    )

    assert registry["effective_year"] == 2024
    assert len(registry["entries"]) == 2106
    assert registry["sources"] == manifest["nomenclator"]["sources"]
    for name, expected in registry["sources"].items():
        payload = (ROOT / "reference/nomenclator/2024" / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected


def test_2024_raw_pdfs_are_excluded_from_git_policy():
    ignore = (ROOT / ".gitignore").read_text()
    assert "data/2024/**/budget_file.pdf" in ignore
    assert json.loads((ROOT / "data/2024/manifest.json").read_text())["raw_pdf_policy"] == \
        "excluded_from_git_with_committed_urls_checksums_and_derived_bundles"


def test_2024_native_sources_publish_complete_analytics_and_verified_bundles():
    manifest = Manifest(ROOT / "data/2024/manifest.json")
    native = [city for city in manifest.cities() if city.source_format != "pdf"]

    assert len(native) == 3
    for city in native:
        conversion = city.entry.get("conversion") or {}
        assert conversion.get("status") == "converted"
        assert conversion["artifacts"]["source_format"] in {"xls", "xlsx"}
        assert audit_city(manifest, city).status == "verified"

        analysis = json.loads(city.analysis.read_text())
        assert analysis["totals_mii_lei"]["venituri"] is not None
        assert analysis["totals_mii_lei"]["cheltuieli"] is not None
        assert analysis["infografic"] is not None
        assert analysis["infografic"]["capitole"]


def test_2024_all_available_sources_publish_complete_zero_cost_scopes():
    manifest = Manifest(ROOT / "data/2024/manifest.json")
    converted = [
        city for city in manifest.cities()
        if (city.entry.get("conversion") or {}).get("status") == "converted"
    ]

    assert len(converted) == 41
    for city in converted:
        conversion = city.entry["conversion"]
        quality = conversion["quality"]
        assert city.workbook.exists()
        assert city.analysis.exists()
        assert quality["scope"]["complete_pdf"] is True
        assert quality["recall_measured"] is False
        assert conversion["llm_cost_usd"] == 0
        assert conversion["llm_cost_scope"] == "current_run_incremental"
        assert conversion["artifacts"]["bundle_id"]


def test_2024_documentation_metrics_are_generated_from_public_artifacts():
    result = subprocess.run(
        [sys.executable, str(ROOT / "data/2024/metrics.py"), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "documentation metrics are current" in result.stdout
