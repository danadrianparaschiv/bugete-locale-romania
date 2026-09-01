#!/usr/bin/env python3
"""Generate and verify the public 2024 documentation metrics offline.

The raw PDFs are intentionally absent from a clean Git checkout.  This script
therefore derives the documentation snapshot only from committed manifests,
workbooks, analysis files, source verification metadata, and the generated
analytics dataset.  ``audit_data`` still checks every public workbook and
analysis hash; an absent raw PDF is an expected warning, not an audit error.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from collections import Counter
from pathlib import Path

from bgconvertor.manifest import Manifest
from bgconvertor.publication import audit_data, audit_report

ROOT = Path(__file__).resolve().parents[2]
EDITION = ROOT / "data/2024"
METRICS_PATH = EDITION / "metrics.json"


def _integer(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _money(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".").replace(".", ",")


def collect_metrics() -> dict:
    """Return the reproducible snapshot used by every generated doc block."""
    manifest = Manifest(EDITION / "manifest.json")
    verification = json.loads((EDITION / "verification.json").read_text())
    campaign = json.loads((EDITION / "quality-campaign.json").read_text())
    analytics = json.loads((ROOT / "analytics/analytics.json").read_text())
    entries = manifest.cities()
    converted = [
        city for city in entries
        if (city.entry.get("conversion") or {}).get("status") == "converted"
    ]
    unavailable = [city for city in entries if city not in converted]

    qualities = [(city.entry["conversion"]["quality"]) for city in converted]
    analyses = [json.loads(city.analysis.read_text()) for city in converted]
    rates = [float(quality["pct_lines_strictly_verified"]) for quality in qualities]
    formats = Counter(city.source_format for city in converted)

    audits = audit_data(ROOT / "data")
    corpus_audit = audit_report(audits)["summary"]
    edition_audits = [result for result in audits if result.year == 2024]
    converted_audits = [
        result for result in edition_audits if result.status != "not_converted"
    ]
    if any(result.status != "verified" for result in converted_audits):
        raise RuntimeError("2024 contains a converted bundle that is not verified")
    if (
        corpus_audit["inconsistent"]
        or corpus_audit["trusted"] != corpus_audit["converted"]
    ):
        raise RuntimeError("the public corpus audit is not clean")
    if verification["summary"]["verified"] != len(converted):
        raise RuntimeError("source verification and conversion counts disagree")

    coverage = analytics["coverage"]["2024"]
    metrics = {
        "schema_version": 2,
        "year": 2024,
        "generated_on": verification["generated_on"],
        "entries": len(entries),
        "verified_sources": verification["summary"]["verified"],
        "converted_entries": len(converted),
        "unavailable_entries": len(unavailable),
        "unavailable_municipalities": [city.name for city in unavailable],
        "pdf_sources": formats["pdf"],
        "native_excel_sources": formats["xls"] + formats["xlsx"],
        "complete_scopes": sum(
            quality.get("scope", {}).get("complete_pdf") is True
            for quality in qualities
        ),
        "pages_expected": sum(
            int(quality.get("scope", {}).get("pages_expected") or 0)
            for quality in qualities
        ),
        "pdf_pages_expected": sum(
            int(city.entry["conversion"]["quality"].get("scope", {}).get("pages_expected") or 0)
            for city in converted if city.source_format == "pdf"
        ),
        "workbook_sheets_expected": sum(
            int(city.entry["conversion"]["quality"].get("scope", {}).get("pages_expected") or 0)
            for city in converted if city.source_format in {"xls", "xlsx"}
        ),
        "lines": sum(int(quality["lines"]) for quality in qualities),
        "strict_lines": sum(
            int(quality["lines_strictly_verified"]) for quality in qualities
        ),
        "numeric_cells": sum(int(quality["numeric_cells"]) for quality in qualities),
        "strict_numeric_cells": sum(
            int(quality["numeric_cells_strictly_verified"]) for quality in qualities
        ),
        "median_strict_line_rate": round(statistics.median(rates), 1),
        "entries_at_least_90": sum(rate >= 90 for rate in rates),
        "entries_at_least_70": sum(rate >= 70 for rate in rates),
        "entries_below_70": sum(rate < 70 for rate in rates),
        "municipal_pages": len({city.siruta for city in converted}),
        "chapter_tables": sum(bool(analysis.get("top_capitole")) for analysis in analyses),
        "full_chart_blocks": sum(
            analysis.get("infografic") is not None for analysis in analyses
        ),
        "trusted_plan_analyses": coverage["trusted_plan_analyses"],
        "plan_comparison_eligible": coverage["plan_comparison_eligible"],
        "llm_cost_usd": round(sum(
            float(city.entry["conversion"].get("llm_lifetime_cost_usd") or 0)
            for city in converted
        ), 6),
        "llm_current_run_cost_usd": round(sum(
            float(city.entry["conversion"].get("llm_cost_usd") or 0)
            for city in converted
        ), 6),
        "quality_campaign": campaign["summary"],
        "corpus_audit": {
            "entries": corpus_audit["entries"],
            "converted": corpus_audit["converted"],
            "trusted": corpus_audit["trusted"],
            "inconsistent": corpus_audit["inconsistent"],
        },
    }
    return metrics


def _edition_readme(metrics: dict) -> str:
    campaign = metrics["quality_campaign"]
    return f"""Rularea de calitate finalizată la 28 august 2026 a convertit toate cele {metrics['converted_entries']} de intrări
cu sursă disponibilă. Nucleul este determinist; trei recuperări P2 acceptate
(Brăila, Deva și Zalău) au costat în total {_money(metrics['llm_cost_usd'])} USD. București apare în
manifest atât ca reședință pentru Ilfov, cât și în poziția separată a
municipiului București; cele două intrări folosesc aceeași sursă verificată și
au produs bundle-uri identice ca date. Drobeta-Turnu Severin rămâne singura
intrare fără document publicabil.

| Indicator auditat | Rezultat |
|---|---:|
| Intrări în manifest | {metrics['entries']} |
| Surse oficiale verificate și convertite | {metrics['verified_sources']} |
| PDF / Excel nativ | {metrics['pdf_sources']} / {metrics['native_excel_sources']} |
| Scope-uri sursă procesate complet | {metrics['complete_scopes']}/{metrics['converted_entries']} |
| Unități sursă inventariabile | {_integer(metrics['pages_expected'])} |
| Pagini PDF / foi Excel native | {_integer(metrics['pdf_pages_expected'])} / {_integer(metrics['workbook_sheets_expected'])} |
| Linii extrase / strict verificate | {_integer(metrics['lines'])} / {_integer(metrics['strict_lines'])} |
| Celule numerice / strict verificate | {_integer(metrics['numeric_cells'])} / {_integer(metrics['strict_numeric_cells'])} |
| Mediana `observed_strict_line_rate` | {_decimal(metrics['median_strict_line_rate'])}% |
| Intrări cu rată strictă ≥90% / ≥70% | {metrics['entries_at_least_90']} / {metrics['entries_at_least_70']} |
| Pagini municipale cu analiză publică | {metrics['municipal_pages']} |
| Municipiu-ani eligibili pentru comparația planului | {metrics['plan_comparison_eligible']} |
| Pilot P2: fișiere / apeluri API facturabile | {campaign['pilot_files']} / {campaign['billable_api_calls']} |
| Câștig P2 în celule numerice strict verificate | +{_integer(campaign['pilot_strict_numeric_cell_gain'])} |
| Cost API real / buget experimental | {_money(metrics['llm_cost_usd'])} / 20 USD |

Cele {metrics['municipal_pages']} de pagini municipale provin din {metrics['converted_entries']} de intrări convertite deoarece
București este duplicat intenționat în manifest. {metrics['chapter_tables']} de intrări au un tabel de
capitole, iar {metrics['full_chart_blocks']} trec și poarta de acoperire necesară blocului complet de
grafice. Lipsa unui grafic nu este umplută prin estimare: pagina păstrează
tabelul disponibil și avertismentul de acoperire.

Aceste procente sunt rate de consistență pentru liniile și celulele deja
extrase, nu `validated_cell_recall`. Niciun etalon exhaustiv nu există încă
pentru toate cele {metrics['converted_entries']} de documente; prin urmare ediția declară explicit
`recall_measured=false` și nu pretinde 90% recall la nivel de corpus. Poarta
de calitate folosește schema 3: anexele și listele de investiții sunt raportate
separat și nu mai pot umfla numitorul bugetar. Cele patru pagini dificile
Bistrița p30, Brăila p167, Deva p226 și Zalău p24 sunt fixate ca fixture-uri
offline. Matricea completă, inclusiv baseline-ul, câștigul per dolar și
deciziile de publicare, este în `QUALITY.md` și `quality-campaign.json`.

Poarta
`corpus audit data --strict --require-modern` verifică însă că toate cele {metrics['corpus_audit']['converted']}
conversii existente din corpusul 2024–2026 sunt bundle-uri moderne coerente,
fără nicio neconcordanță între Excel, analiză și manifest."""


def _root_readme(metrics: dict) -> str:
    return f"""Ediția 2024 este procesată cap-coadă pentru toate cele {metrics['converted_entries']} de intrări cu sursă
oficială disponibilă: {_integer(metrics['lines'])} de linii extrase, mediană strictă observată de
{_decimal(metrics['median_strict_line_rate'])}%, {metrics['municipal_pages']} de pagini municipale de analiză, {metrics['plan_comparison_eligible']} de municipiu-ani eligibili
pentru comparația planului și {_money(metrics['llm_cost_usd'])} USD cost API real pentru cele trei
recuperări P2 acceptate. Drobeta-Turnu Severin este
singura lipsă declarată. Aceste cifre măsoară ieșirea și consistența ei, nu
recall exhaustiv; tabelul auditat și limitele sunt în README-ul ediției."""


def _quality(metrics: dict) -> str:
    campaign = metrics["quality_campaign"]
    return f"""Conversia și campania de calitate finalizate la 28 august 2026 acoperă toate cele {metrics['converted_entries']} de intrări cu
sursă oficială disponibilă din manifestul 2024, cu toate scope-urile procesate
complet și {_money(metrics['llm_cost_usd'])} USD cost API real. A produs {_integer(metrics['lines'])} de linii, dintre care {_integer(metrics['strict_lines'])} strict
verificate, și {_integer(metrics['numeric_cells'])} de celule numerice, dintre care {_integer(metrics['strict_numeric_cells'])} strict
verificate. Mediana ratei stricte pe intrare este {_decimal(metrics['median_strict_line_rate'])}%; {metrics['entries_at_least_90']}/{metrics['converted_entries']} intrări sunt la
cel puțin 90%, {metrics['entries_at_least_70']}/{metrics['converted_entries']} la cel puțin 70%, iar {metrics['entries_below_70']} rămân sub 70%.

Schema de calitate 3 elimină din numitor anexele și listele de investiții, pe
care le raportează separat. Recuperarea paginilor anterior omise poate mări
numitorul și micșora procentul chiar când apar mai multe celule corecte; de
aceea matricea publică urmărește și numărul absolut de celule strict verificate.
Pilotul P2 a acceptat {campaign['accepted_pilots']} fișiere și a adăugat
{_integer(campaign['pilot_strict_numeric_cell_gain'])} astfel de celule pentru
{_money(campaign['actual_spend_usd'])} USD, sub plafonul de 3 USD/fișier și
bugetul experimental de 20 USD.

Toate bundle-urile publică `recall_measured=false`, iar cifrele nu
pot fi prezentate drept recall complet. Pe partea analitică, ediția produce
{metrics['municipal_pages']} de pagini municipale, {metrics['plan_comparison_eligible']} de municipiu-ani eligibili pentru comparația
planului, {metrics['chapter_tables']} de tabele de capitole și {metrics['full_chart_blocks']} blocuri complete de grafice. Graficele
rămase sunt retrase când capitolele strict verificate nu acoperă 90–110% din
totalul tipărit, în loc să fie completate prin estimare.

Auditul final `corpus audit data --strict --require-modern` trece pentru
{metrics['corpus_audit']['trusted']}/{metrics['corpus_audit']['converted']} conversii existente din edițiile 2024–2026 și găsește zero bundle-uri
inconsistente. Achiziția și rezultatele detaliate sunt documentate în
[`data/2024/README.md`](../data/2024/README.md)."""


def _lessons(metrics: dict) -> str:
    campaign = metrics["quality_campaign"]
    return f"""- **Achiziția completă nu înseamnă automat calitate uniformă.** Conversia
  deterministă a tuturor celor {metrics['converted_entries']} de intrări disponibile a produs {_integer(metrics['lines'])} de
  linii, iar recuperarea P2 selectivă a adăugat {_integer(campaign['pilot_strict_numeric_cell_gain'])} celule strict
  verificate pentru {_money(metrics['llm_cost_usd'])} USD. Mediana ratei stricte observate este {_decimal(metrics['median_strict_line_rate'])}% și {metrics['entries_below_70']}
  intrări rămân sub 70%. Schema 3 separă anexele și investițiile, iar procentul
  rămâne consistență pe ieșirea extrasă, nu recall complet; numărul absolut de
  celule și fixture-urile exhaustive trebuie citite împreună cu el.
- **Absența graficului este și ea un rezultat de calitate.** Ediția publică {metrics['municipal_pages']}
  de pagini municipale și {metrics['chapter_tables']} de tabele de capitole, dar numai {metrics['full_chart_blocks']} blocuri
  complete de grafice trec poarta de acoperire 90–110% față de totalul
  tipărit. Analiticele transversale păstrează {metrics['plan_comparison_eligible']} de municipiu-ani eligibili
  pentru comparația planului; restul rămân vizibili cu motivul excluderii, fără
  estimări care să umple golurile."""


def _lessons_footer(metrics: dict) -> str:
    return f"""*Document viu — se actualizează pe măsură ce corpusul crește. Ultima
actualizare: 28 august 2026, după campania de calitate a ediției 2024:
{metrics['converted_entries']}/{metrics['entries']} intrări convertite, {metrics['municipal_pages']} de pagini municipale de analiză, {_integer(metrics['lines'])} de linii,
{_money(metrics['llm_cost_usd'])} USD cost API real și o singură sursă indisponibilă declarată. Auditul public trece
pentru toate cele {metrics['corpus_audit']['trusted']} conversii existente din 2024–2026, fără neconcordanțe de
bundle; rezultatele detaliate și limitele metricilor sunt în
[`data/2024/README.md`](../data/2024/README.md).*"""


def rendered_blocks(metrics: dict) -> dict[Path, dict[str, str]]:
    return {
        EDITION / "README.md": {"2024_EDITION_METRICS": _edition_readme(metrics)},
        ROOT / "README.md": {"2024_ROOT_METRICS": _root_readme(metrics)},
        ROOT / "docs/quality.md": {"2024_QUALITY_METRICS": _quality(metrics)},
        ROOT / "docs/lectii-invatate.md": {
            "2024_LESSONS_METRICS": _lessons(metrics),
            "2024_LESSONS_FOOTER": _lessons_footer(metrics),
        },
    }


def _replace_block(text: str, name: str, body: str) -> str:
    start = f"<!-- BEGIN GENERATED:{name} -->"
    end = f"<!-- END GENERATED:{name} -->"
    if start not in text or end not in text:
        raise ValueError(f"missing generated block markers: {name}")
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return f"{before}{start}\n{body.rstrip()}\n{end}{after}"


def _render_document(path: Path, blocks: dict[str, str]) -> str:
    text = path.read_text()
    for name, body in blocks.items():
        text = _replace_block(text, name, body)
    return text


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_outputs(metrics: dict) -> None:
    _atomic_write(
        METRICS_PATH,
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    for path, blocks in rendered_blocks(metrics).items():
        _atomic_write(path, _render_document(path, blocks))


def check_outputs(metrics: dict) -> list[str]:
    problems = []
    expected_json = json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not METRICS_PATH.exists() or METRICS_PATH.read_text() != expected_json:
        problems.append(str(METRICS_PATH.relative_to(ROOT)))
    for path, blocks in rendered_blocks(metrics).items():
        if _render_document(path, blocks) != path.read_text():
            problems.append(str(path.relative_to(ROOT)))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate metrics and docs")
    mode.add_argument("--check", action="store_true", help="fail when generated outputs drift")
    args = parser.parse_args()

    metrics = collect_metrics()
    if args.write:
        write_outputs(metrics)
        print(f"updated {METRICS_PATH.relative_to(ROOT)} and documentation blocks")
        return 0
    problems = check_outputs(metrics)
    if problems:
        print("outdated generated 2024 metrics: " + ", ".join(problems))
        return 1
    print("2024 documentation metrics are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
