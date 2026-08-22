"""Driver pentru matricea de evaluare a preseturilor (docs/eval-modele.md).

Captură per iterație, garantată prin construcție:
  - stdout+stderr complet:      evals/logs/<oraș>/<preset>.log
  - felia de ledger a rulării:  evals/logs/<oraș>/<preset>.ledger.jsonl
    (ledger-ul e partajat per oraș, deci rulările aceluiași oraș sunt
    STRICT secvențiale; felierea = liniile adăugate între start și final)
  - rezumat structurat:         evals/logs/<oraș>/<preset>.json
  - rând agregat:               evals/rezultate.csv

Reluabil: o combinație cu rezumat .json existent se sare. Orașele pot
rula în paralel (ledgere separate); preseturile unui oraș, niciodată.

Rulare:  uv run python scripts/eval_matrix.py [--city <slug>] [--dry]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
CAP = "3.00"

CITIES = {  # slug -> (pdf, store_key)
    "suceava": ("data/2026/35-suceava/146263-suceava/budget_file.pdf",
                "2026-35-suceava-146263-suceava"),
    "sfantu-gheorghe": ("data/2026/15-covasna/63394-sfantu-gheorghe/budget_file.pdf",
                        "2026-15-covasna-63394-sfantu-gheorghe"),
    "galati": ("data/2026/18-galati/75098-galati/budget_file.pdf",
               "2026-18-galati-75098-galati"),
    "miercurea-ciuc": ("data/2026/21-harghita/83320-miercurea-ciuc/budget_file.pdf",
                       "2026-21-harghita-83320-miercurea-ciuc"),
    "pitesti": ("data/2026/03-arges/13169-pitesti/budget_file.pdf",
                "2026-03-arges-13169-pitesti"),
}

PRESETS = [
    "anthropic:claude-fable-5",
    "anthropic:claude-opus-5",
    "anthropic:claude-sonnet-5",
    "anthropic:claude-opus-4-5",
    "anthropic:claude-sonnet-4-5",
    "anthropic:claude-haiku-4-5",
    "openai:gpt-5.1",
    "openai:gpt-5-mini",
    "google:gemini-3.1-pro",
    "google:gemini-3.6-flash",
    "mistral:mistral-medium-3",
    "qwen:qwen3-vl-30b",
]
# mistral: limite de rată stricte -> concurență redusă
LOW_CONCURRENCY = {"mistral"}

STATS_RE = re.compile(
    r"(\d+) documente · (\d+) linii de date · ([\d.]+)% complet curate · "
    r"(\d+) erori · (\d+)"
)


def slug(preset: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", preset.lower())


def ledger_path(city: str) -> Path:
    return ROOT / "runs" / CITIES[city][1] / "llm_ledger.jsonl"


def run_one(city: str, preset: str | None, dry: bool) -> dict | None:
    """Un preset (sau baseline --llm off dacă preset=None) pe un oraș."""
    pdf, _ = CITIES[city]
    name = slug(preset) if preset else "baseline"
    logdir = EVALS / "logs" / city
    logdir.mkdir(parents=True, exist_ok=True)
    marker = logdir / f"{name}.json"
    if marker.exists():
        print(f"  · {city}/{name}: gata deja, sar")
        return None
    out_xlsx = EVALS / city / f"{name}.xlsx"
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["uv", "run", "bgconvertor", "convert", pdf,
           "--out", str(out_xlsx), "--workers", "4"]
    env_extra = {}
    if preset:
        cmd += ["--llm", "repair", "--max-llm-cost", CAP, "--model-preset", preset]
        if preset.split(":")[0] in LOW_CONCURRENCY:
            env_extra["BGC_LLM__CONCURRENCY"] = "2"
    else:
        cmd += ["--llm", "off"]
    if dry:
        print(f"  DRY {city}/{name}: {' '.join(cmd)} {env_extra or ''}")
        return None

    led = ledger_path(city)
    lines_before = len(led.read_text().splitlines()) if led.exists() else 0
    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, **env_extra},
    )
    dur = time.time() - t0
    log_file = logdir / f"{name}.log"
    log_file.write_text(proc.stdout + proc.stderr)

    # felia de ledger a acestei rulări
    lines = led.read_text().splitlines() if led.exists() else []
    my_lines = lines[lines_before:]
    (logdir / f"{name}.ledger.jsonl").write_text("\n".join(my_lines))
    paid = [json.loads(ln) for ln in my_lines]
    cached = [r for r in paid if r.get("cached")]
    paid = [r for r in paid if not r.get("cached")]
    by_model: dict[str, dict] = {}
    for r in paid:
        b = by_model.setdefault(f"{r['model']}|{r['purpose']}",
                                {"calls": 0, "cost": 0.0})
        b["calls"] += 1
        b["cost"] += r.get("cost_usd", 0.0)

    m = None
    for m in STATS_RE.finditer(proc.stdout):  # noqa: B007 — ultima potrivire = starea finală
        pass
    schema_errors = proc.stdout.count("parse error (attempt")
    summary = {
        "city": city, "preset": preset or "baseline",
        "ok": proc.returncode == 0 and m is not None,
        "lines": int(m.group(2)) if m else None,
        "pct_clean": float(m.group(3)) if m else None,
        "errors": int(m.group(4)) if m else None,
        "paid_calls": len(paid), "cached_calls": len(cached),
        "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in paid), 4),
        "by_model": by_model,
        "schema_errors": schema_errors,
        "duration_s": round(dur), "ts": dt.datetime.now().isoformat(timespec="seconds"),
    }
    marker.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    csv_path = EVALS / "rezultate.csv"
    new = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["city", "preset", "ok", "lines", "pct_clean", "errors",
                        "paid_calls", "cached_calls", "cost_usd",
                        "schema_errors", "duration_s"])
        w.writerow([summary[k] for k in
                    ("city", "preset", "ok", "lines", "pct_clean", "errors",
                     "paid_calls", "cached_calls", "cost_usd",
                     "schema_errors", "duration_s")])
    status = "OK" if summary["ok"] else "EȘEC"
    print(f"  ✓ {city}/{name}: {status} {summary['pct_clean']}% · "
          f"{summary['errors']} erori · ${summary['cost_usd']} · {summary['duration_s']}s")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", choices=CITIES, help="doar acest oraș")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    cities = [args.city] if args.city else list(CITIES)
    for city in cities:
        print(f"== {city} ==")
        run_one(city, None, args.dry)  # baseline (gratuit; sare dacă există)
        for preset in PRESETS:
            run_one(city, preset, args.dry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
