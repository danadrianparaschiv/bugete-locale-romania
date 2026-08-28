"""Offline contract for the 2024 quality and capped-recovery campaign."""

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _campaign() -> dict:
    return json.loads((ROOT / "data/2024/quality-campaign.json").read_text())


def test_2024_quality_campaign_is_budgeted_and_acceptance_gated():
    campaign = _campaign()
    policy = campaign["policy"]
    summary = campaign["summary"]

    assert campaign["schema_version"] == 1
    assert campaign["year"] == 2024
    assert summary["baseline_targets_below_70"] == 10
    assert summary["pilot_files"] == 3
    assert summary["accepted_pilots"] == 3
    assert policy["public_per_file_hard_cap_usd"] == 5.0
    assert policy["pilot_per_file_cap_usd"] == 3.0
    assert summary["actual_spend_usd"] <= policy["authorized_experiment_budget_usd"]

    pilots = [target for target in campaign["targets"] if "recovered" in target["recovery"]]
    assert {target["municipality"] for target in pilots} == {"Brăila", "Deva", "Zalău"}
    for target in pilots:
        recovery = target["recovery"]
        assert recovery["cost_usd"] <= recovery["file_cap_usd"] <= 5.0
        assert recovery["accepted"] is True
        assert recovery["strict_numeric_cell_gain"] > 0
        assert recovery["recovered"]["strict_numeric_cells"] > \
            recovery["deterministic"]["strict_numeric_cells"]


def test_2024_quality_campaign_matches_the_published_manifest():
    campaign = _campaign()
    manifest = json.loads((ROOT / "data/2024/manifest.json").read_text())
    entries = {entry["county_code"]: entry for entry in manifest["entries"]}

    assert len(campaign["targets"]) == 10
    for target in campaign["targets"]:
        conversion = entries[target["county_code"]]["conversion"]
        quality = conversion["quality"]
        final = target["final"]
        assert final["quality_schema_version"] == 3
        assert final["lines"] == quality["lines"]
        assert final["strict_numeric_cells"] == \
            quality["numeric_cells_strictly_verified"]
        assert target["bundle_id"] == conversion["artifacts"]["bundle_id"]


def test_2024_quality_campaign_is_generated_offline():
    result = subprocess.run(
        [sys.executable, str(ROOT / "data/2024/quality_campaign.py"), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "campaign matrix is current" in result.stdout


def test_2024_cross_year_candidates_are_priority_only():
    path = ROOT / "data/2024/recovery-candidates.csv"
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    campaign = _campaign()["cross_year_re_read"]

    assert len(rows) == campaign["rows"] == 792
    assert campaign["target_rows"] == 104
    assert campaign["target_municipalities"] == ["Brăila", "Deva"]
    assert campaign["signature_counts"] == {"decimal_shift": 36, "outlier": 756}
    assert all(row["siruta"].isdigit() and row["pagina_noua"].isdigit() for row in rows)
    assert "overwrite" in campaign["semantics"]
