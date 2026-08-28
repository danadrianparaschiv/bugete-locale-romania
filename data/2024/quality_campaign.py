#!/usr/bin/env python3
"""Generate the auditable 2024 low-quality recovery campaign matrix.

The baseline is the public bundle snapshot before extraction version 48.  The
final side is read from the current manifest, so this file also acts as an
offline drift check after republishing.  Raw PDFs and private run caches are
deliberately not inputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path

from bgconvertor.manifest import Manifest

ROOT = Path(__file__).resolve().parents[2]
EDITION = ROOT / "data/2024"
OUTPUT = EDITION / "quality-campaign.json"
REPORT = EDITION / "QUALITY.md"
RECOVERY_CANDIDATES = EDITION / "recovery-candidates.csv"

BASELINE = {
    "01": ("Alba Iulia", 875, 602, 3432, 2103, 68.8, 61.3),
    "03": ("Pitești", 4065, 2392, 22190, 14312, 58.8, 64.5),
    "06": ("Bistrița", 1747, 632, 4132, 1983, 36.2, 48.0),
    "09": ("Brăila", 8237, 5417, 16943, 11039, 65.8, 65.2),
    "10": ("Buzău", 902, 526, 3284, 1962, 58.3, 59.7),
    "12": ("Călărași", 1266, 672, 4201, 2410, 53.1, 57.4),
    "22": ("Deva", 4885, 2341, 20251, 9463, 47.9, 46.7),
    "33": ("Zalău", 807, 325, 2517, 1264, 40.3, 50.2),
    "34": ("Sibiu", 1069, 616, 3298, 2138, 57.6, 64.8),
    "39": ("Vaslui", 2313, 1165, 4632, 2968, 50.4, 64.1),
}

ISSUE_FAMILIES = {
    "01": "scanned_grid_and_numeric_ocr",
    "03": "continuation_headers_and_functional_context",
    "06": "continuation_sections_and_candidate_selection",
    "09": "dense_scanned_tables_and_merged_rows",
    "10": "scanned_structure_and_numeric_ocr",
    "12": "scanned_structure_and_numeric_ocr",
    "22": "low_contrast_scan_and_merged_cells",
    "33": "collapsed_remainder_column_and_decimal_glyphs",
    "34": "recovered_rows_expand_observed_denominator",
    "39": "scanned_structure_and_numeric_ocr",
}

# These are the isolated deterministic and recovered candidate workbooks from
# the capped pilot.  Successful calls are cached; publishing replays them with
# zero incremental cost while retaining this lifetime spend record.
PILOTS = {
    "09": {
        "model_preset": "google:gemini-3.6-flash",
        "billable_calls": 184,
        "cost_usd": 0.8311,
        "deterministic": {
            "lines": 8489,
            "strict_lines": 5529,
            "numeric_cells": 17017,
            "strict_numeric_cells": 10816,
            "errors": 2987,
        },
        "recovered": {
            "lines": 8520,
            "strict_lines": 5637,
            "numeric_cells": 17355,
            "strict_numeric_cells": 11224,
            "errors": 2881,
        },
    },
    "22": {
        "model_preset": "google:gemini-3.6-flash",
        "billable_calls": 143,
        "cost_usd": 0.9009,
        "deterministic": {
            "lines": 4064,
            "strict_lines": 2126,
            "numeric_cells": 22255,
            "strict_numeric_cells": 13382,
            "errors": 3294,
        },
        "recovered": {
            "lines": 4156,
            "strict_lines": 2186,
            "numeric_cells": 23519,
            "strict_numeric_cells": 14459,
            "errors": 3297,
        },
    },
    "33": {
        "model_preset": "google:gemini-3.6-flash",
        "billable_calls": 111,
        "cost_usd": 0.4810,
        "deterministic": {
            "lines": 810,
            "strict_lines": 443,
            "numeric_cells": 3649,
            "strict_numeric_cells": 2091,
            "errors": 572,
        },
        "recovered": {
            "lines": 777,
            "strict_lines": 447,
            "numeric_cells": 3588,
            "strict_numeric_cells": 2121,
            "errors": 529,
        },
    },
}


def _quality_snapshot(quality: dict) -> dict:
    return {
        "quality_schema_version": quality["schema_version"],
        "lines": quality["lines"],
        "strict_lines": quality["lines_strictly_verified"],
        "line_rate_pct": quality["pct_lines_strictly_verified"],
        "numeric_cells": quality["numeric_cells"],
        "strict_numeric_cells": quality["numeric_cells_strictly_verified"],
        "numeric_cell_rate_pct": quality["pct_numeric_cells_strictly_verified"],
        "annex_lines": quality.get("annex_lines", 0),
        "annex_numeric_cells": quality.get("annex_numeric_cells", 0),
        "errors": quality["errors"],
        "warnings": quality["warnings"],
    }


def build_campaign() -> dict:
    manifest = Manifest(EDITION / "manifest.json")
    cities = {city.entry["county_code"]: city for city in manifest.cities()}
    targets = []
    for county_code, baseline in BASELINE.items():
        name, lines, strict_lines, numeric, strict_numeric, line_rate, numeric_rate = baseline
        city = cities[county_code]
        conversion = city.entry.get("conversion") or {}
        if conversion.get("status") != "converted":
            raise RuntimeError(f"campaign target is not converted: {name}")
        final = _quality_snapshot(conversion["quality"])
        pilot = PILOTS.get(county_code)
        if pilot:
            strict_gain = (
                pilot["recovered"]["strict_numeric_cells"]
                - pilot["deterministic"]["strict_numeric_cells"]
            )
            pilot_record = {
                **pilot,
                "file_cap_usd": 3.0,
                "strict_numeric_cell_gain": strict_gain,
                "gain_per_usd": round(strict_gain / pilot["cost_usd"], 1),
                "accepted": strict_gain > 0,
                "acceptance_reason": "strict_numeric_cells_increased",
            }
            for key in (
                "lines", "strict_lines", "numeric_cells", "strict_numeric_cells", "errors"
            ):
                if final[key] != pilot["recovered"][key]:
                    raise RuntimeError(
                        f"published {name} {key} does not match accepted candidate"
                    )
        else:
            pilot_record = None
        targets.append({
            "county_code": county_code,
            "municipality": name,
            "issue_family": ISSUE_FAMILIES[county_code],
            "baseline": {
                "quality_schema_version": 2,
                "lines": lines,
                "strict_lines": strict_lines,
                "line_rate_pct": line_rate,
                "numeric_cells": numeric,
                "strict_numeric_cells": strict_numeric,
                "numeric_cell_rate_pct": numeric_rate,
            },
            "final": final,
            "change": {
                "lines": final["lines"] - lines,
                "strict_lines": final["strict_lines"] - strict_lines,
                "numeric_cells": final["numeric_cells"] - numeric,
                "strict_numeric_cells": final["strict_numeric_cells"] - strict_numeric,
                "line_rate_points": round(final["line_rate_pct"] - line_rate, 1),
                "numeric_cell_rate_points": round(
                    final["numeric_cell_rate_pct"] - numeric_rate, 1
                ),
            },
            "recovery": pilot_record or {
                "accepted": False,
                "cost_usd": 0.0,
                "mode": "deterministic_only",
            },
            "bundle_id": conversion["artifacts"]["bundle_id"],
        })

    with RECOVERY_CANDIDATES.open(newline="") as handle:
        candidate_rows = list(csv.DictReader(handle))
    expected_columns = {
        "oras", "siruta", "cod", "cod_functional", "sectiune", "coloana",
        "valoare_veche", "valoare_noua", "raport", "raport_relativ",
        "semnatura", "vechi_verificat", "nou_verificat", "pagina_noua",
        "prioritate",
    }
    if not candidate_rows or set(candidate_rows[0]) != expected_columns:
        raise RuntimeError("unexpected 2024 cross-year candidate schema")
    target_candidate_rows = [
        row for row in candidate_rows
        if row["siruta"] in {cities[code].siruta for code in BASELINE}
    ]
    signature_counts = {
        signature: sum(row["semnatura"] == signature for row in candidate_rows)
        for signature in sorted({row["semnatura"] for row in candidate_rows})
    }
    total_spend = round(sum(row["cost_usd"] for row in PILOTS.values()), 4)
    return {
        "schema_version": 1,
        "year": 2024,
        "completed_on": "2026-08-28",
        "objective": "improve every 2024 entry below 70% without overstating recall",
        "policy": {
            "authorized_experiment_budget_usd": 20.0,
            "public_per_file_hard_cap_usd": 5.0,
            "pilot_per_file_cap_usd": 3.0,
            "acceptance_gate": "publish only when strictly verified numeric cells increase",
            "raw_pdf_policy": "excluded_from_git",
        },
        "metric_note": (
            "The baseline used quality schema 2. Final bundles use schema 3, which "
            "excludes annex side-sheet rows from the budget denominator and reports "
            "them separately. Recovered rows can enlarge the denominator, so absolute "
            "verified-cell counts and golden recall are reported beside percentages."
        ),
        "implementation": {
            "extract_version": 48,
            "deterministic_changes": [
                "productive OCR candidates outrank empty high-scoring candidates",
                "ten-column grids with a collapsed remainder column retain all quarters",
                "OCR decimal glyphs are repaired only in numeric suffixes",
                "continuation sections and functional header context propagate safely",
                "annex and investment tables do not pad budget quality",
            ],
            "golden_pages": [
                "Bistrița p30",
                "Brăila p167",
                "Deva p226",
                "Zalău p24",
            ],
        },
        "cross_year_re_read": {
            "path": "recovery-candidates.csv",
            "rows": len(candidate_rows),
            "target_rows": len(target_candidate_rows),
            "target_municipalities": sorted({
                row["oras"] for row in target_candidate_rows
            }),
            "signature_counts": signature_counts,
            "generated_from": "pre-recovery 2024 and 2025 public corpus exports",
            "semantics": (
                "priority-only outlier list; no candidate value is accepted or "
                "used to overwrite an extracted budget fact"
            ),
        },
        "summary": {
            "baseline_targets_below_70": len(BASELINE),
            "final_targets_below_70": sum(
                row["final"]["line_rate_pct"] < 70 for row in targets
            ),
            "pilot_files": len(PILOTS),
            "accepted_pilots": sum(
                bool(row["recovery"].get("accepted")) for row in targets
            ),
            "billable_api_calls": sum(
                row["billable_calls"] for row in PILOTS.values()
            ),
            "actual_spend_usd": total_spend,
            "remaining_experiment_budget_usd": round(20.0 - total_spend, 4),
            "pilot_strict_numeric_cell_gain": sum(
                row["recovered"]["strict_numeric_cells"]
                - row["deterministic"]["strict_numeric_cells"]
                for row in PILOTS.values()
            ),
            "cross_year_candidates": len(candidate_rows),
        },
        "targets": targets,
    }


def _render(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _rate(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _money(value: float) -> str:
    return f"{value:.4f}".replace(".", ",")


def _render_report(payload: dict) -> str:
    summary = payload["summary"]
    target_rows = []
    pilot_rows = []
    for target in payload["targets"]:
        recovery = target["recovery"]
        recovery_label = "P2 acceptat" if recovery.get("accepted") else "determinist"
        target_rows.append(
            f"| {target['municipality']} | `{target['issue_family']}` | "
            f"{_rate(target['baseline']['line_rate_pct'])}% | "
            f"{_rate(target['final']['line_rate_pct'])}% | "
            f"{_number(target['final']['strict_numeric_cells'])} | "
            f"{target['change']['strict_numeric_cells']:+d} | {recovery_label} | "
            f"{_money(float(recovery.get('cost_usd') or 0))} |"
        )
        if "recovered" in recovery:
            pilot_rows.append(
                f"| {target['municipality']} | "
                f"{_number(recovery['deterministic']['strict_numeric_cells'])} | "
                f"{_number(recovery['recovered']['strict_numeric_cells'])} | "
                f"+{_number(recovery['strict_numeric_cell_gain'])} | "
                f"{_money(recovery['cost_usd'])} | "
                f"{_rate(recovery['gain_per_usd'])} |"
            )

    return f"""# Campania de calitate 2024

Campania a pornit de la cele zece conversii care aveau sub 70% în snapshot-ul
public anterior. Toate cele 41 de surse disponibile au fost republicate cu
extractorul 48 și schema de calitate 3; numai Brăila, Deva și Zalău au primit
recuperare LLM, iar candidatul a fost acceptat numai dacă a mărit numărul de
celule numerice strict verificate.

| Indicator | Rezultat |
|---|---:|
| Ținte inițiale sub 70% | {summary['baseline_targets_below_70']} |
| Ținte inițiale rămase sub 70% | {summary['final_targets_below_70']} |
| Piloți P2 acceptați | {summary['accepted_pilots']}/{summary['pilot_files']} |
| Celule strict verificate adăugate de P2 | +{_number(summary['pilot_strict_numeric_cell_gain'])} |
| Apeluri API facturabile | {_number(summary['billable_api_calls'])} |
| Candidați cross-year pentru recitire | {_number(summary['cross_year_candidates'])} |
| Cost real / buget autorizat | {_money(summary['actual_spend_usd'])} / 20 USD |
| Buget experimental rămas | {_money(summary['remaining_experiment_budget_usd'])} USD |

## Matricea baseline → rezultat public

| Municipiu | Familie de problemă | Rată veche | Rată finală | Celule stricte finale | Δ celule stricte* | Mod | Cost USD |
|---|---|---:|---:|---:|---:|---|---:|
{chr(10).join(target_rows)}

**Notă.** Baseline-ul folosește schema 2, iar rezultatul schema 3. Pentru intrările cu
anexe, diferența absolută include eliminarea anexelor din numitor și nu este o
măsură pură a recall-ului. `annex_lines` și `annex_numeric_cells` sunt publicate
separat în Excel, `analysis.json` și manifest. O rată finală mai mică poate
însoți mai multe rânduri recuperate, deoarece numitorul devine mai complet.

## Randamentul pilotului P2

| Municipiu | Celule stricte determinist | Celule stricte recuperat | Câștig | Cost USD | Celule/USD |
|---|---:|---:|---:|---:|---:|
{chr(10).join(pilot_rows)}

Modelul a fost `google:gemini-3.6-flash`, cu plafon 3 USD/fișier și plafon
public absolut 5 USD/fișier. Costul este cel lifetime din ledger, nu doar costul
incremental al ultimei reluări din cache. Matricea machine-readable completă,
inclusiv bundle-urile și metricile deterministe intermediare, este
[`quality-campaign.json`](quality-campaign.json).

Lista [`recovery-candidates.csv`](recovery-candidates.csv) păstrează cele
{_number(summary['cross_year_candidates'])} de diferențe 2024↔2025 care au
prioritizat recitirea înaintea recuperării. Este o listă de outlieri pentru
revizuire, nu o sursă de adevăr: nicio valoare din ea nu este aplicată automat.

## Reproducere offline

```bash
uv run python data/2024/quality_campaign.py --check
uv run bgconvertor eval --min-anchors 148 --min-text-assertions 19 \\
  --require-cell-ground-truth 10 --min-layout-cell-recall 90 \\
  --min-layout-cell-precision 99.5
uv run bgconvertor corpus audit data --strict --require-modern
```

Cele patru pagini noi Bistrița p30, Brăila p167, Deva p226 și Zalău p24 sunt
fixture-uri OCR sanitizate și rulează fără PDF-uri brute, chei API sau cache-uri
private. PDF-urile sunt excluse din Git; URL-urile oficiale și SHA-256-urile
rămân în manifestul și verificarea ediției.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build_campaign()
    expected = _render(payload)
    expected_report = _render_report(payload)
    if args.write:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=OUTPUT.parent, delete=False
        ) as handle:
            handle.write(expected)
            temporary = Path(handle.name)
        temporary.replace(OUTPUT)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=REPORT.parent, delete=False
        ) as handle:
            handle.write(expected_report)
            temporary_report = Path(handle.name)
        temporary_report.replace(REPORT)
        print(
            f"updated {OUTPUT.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}"
        )
        return 0
    outdated = []
    if not OUTPUT.exists() or OUTPUT.read_text() != expected:
        outdated.append(str(OUTPUT.relative_to(ROOT)))
    if not REPORT.exists() or REPORT.read_text() != expected_report:
        outdated.append(str(REPORT.relative_to(ROOT)))
    if outdated:
        print("outdated campaign outputs: " + ", ".join(outdated))
        return 1
    print("2024 quality campaign matrix is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
