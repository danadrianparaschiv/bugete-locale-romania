"""bgconvertor CLI.

Phase 0 commands:
    profile      run the page-census stage (cached, resumable)
    inspect      render one page + dump its stored artifacts
    runs         show cache/failure state for a PDF
    nomenclator  build / info / update the code registry
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import nomenclator as nom
from .config import RunConfig
from .logging_setup import setup_logging
from .orchestrator import parse_pages, run_stage
from .runstore import RunStore

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
nom_app = typer.Typer(no_args_is_help=True)
app.add_typer(nom_app, name="nomenclator", help="Gestionează registrul de coduri din Ordinul 1954/2005")

console = Console()
_state: dict = {}


@app.callback()
def main(
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="-v progres, -vv detalii"),
    runs_dir: Path | None = typer.Option(None, help="Rădăcina depozitului de artefacte (implicit: ./runs)"),
    fail_fast: bool = typer.Option(False, help="Oprește la prima pagină eșuată"),
    debug: bool = typer.Option(False, help="Scrie artefacte de depanare (PNG-uri de pagini, overlay-uri)"),
):
    setup_logging(verbose)
    # load .env (ANTHROPIC_API_KEY etc.) so `--llm repair` works out of the box
    from .config import project_root

    env_file = project_root() / ".env"
    if env_file.exists():
        import os

        for raw in env_file.read_text().splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                k, _, v = raw.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    config = RunConfig()
    if runs_dir:
        config.runs_dir = runs_dir
    config.fail_fast = fail_fast
    config.debug_artifacts = debug
    _state["config"] = config


def _config() -> RunConfig:
    return _state["config"]


@app.command()
def profile(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    pages: str | None = typer.Option(None, "--pages", help="ex. 1-10 sau 9,31,151"),
):
    """Rulează etapa de recensământ al paginilor: strat de text, geometrie, rutare per pagină."""
    from pypdf import PdfReader

    from . import profilepdf

    config = _config()
    store = RunStore(config, pdf)
    reader = PdfReader(pdf)
    total = len(reader.pages)
    selected = parse_pages(pages, total)

    summary = run_stage(store, "profile", selected, lambda p: profilepdf.profile_page(reader, p))

    profiles = [store.get("profile", p) for p in selected]
    doc = profilepdf.summarize([p for p in profiles if p])
    console.print(summary.line())
    console.print(
        f"[bold]{pdf.name}[/bold]: {doc['pages']} pages profiled, "
        f"{doc['pages_with_text_layer']} with text layer, {doc['pages_scanned']} scanned"
    )


@app.command()
def inspect(
    pdf: Path = typer.Argument(..., exists=True),
    page: int = typer.Argument(..., min=1),
):
    """Randează o pagină în directorul de depanare și afișează toate artefactele stocate pentru ea."""
    from . import profilepdf

    config = _config()
    store = RunStore(config, pdf)

    out = store.debug_dir("inspect") / f"p{page:04d}.png"
    profilepdf.render_page(pdf, page, scale=config.render_scale).save(out)
    console.print(f"rendered [bold]{out}[/bold]")

    found = False
    for stage_dir in sorted(d for d in store.root.iterdir() if d.is_dir() and d.name != "debug"):
        payload = store.get(stage_dir.name, page)
        if payload is not None:
            found = True
            console.rule(f"{stage_dir.name} p{page}")
            console.print_json(json.dumps(payload, ensure_ascii=False))
    for f in store.failures():
        if f["page"] == page:
            found = True
            console.rule(f"[red]FAILURE {f['stage']} p{page}")
            console.print(f["traceback"])
    if not found:
        console.print("[dim]no stored artifacts for this page yet[/dim]")


@app.command()
def runs(pdf: Path = typer.Argument(..., exists=True)):
    """Afișează starea cache-ului și a eșecurilor per etapă pentru un PDF."""
    config = _config()
    store = RunStore(config, pdf)
    table = Table(title=f"run store: {store.root}")
    table.add_column("stage")
    table.add_column("pages cached", justify="right")
    table.add_column("failures", justify="right")
    stages = sorted(
        d.name for d in store.root.iterdir() if d.is_dir() and d.name != "debug"
    )
    if not stages:
        console.print("[dim]empty run store[/dim]")
        return
    for stage in stages:
        table.add_row(
            stage, str(len(store.pages_done(stage))), str(len(store.failures(stage)))
        )
    console.print(table)
    for f in store.failures():
        console.print(f"[red]{f['stage']} p{f['page']}[/red]: {f['error']}")


# conservative per-page planning rates (seconds), from measured runs
STAGE_RATES = {"orient": 5.0, "ocr": 9.0, "extract_digital": 0.1, "extract_map": 0.05}


def _stage_banner(title: str, detail: str = "") -> None:
    console.print(f"\n[bold cyan]▶ {title}[/bold cyan]" + (f" — {detail}" if detail else ""))


def _fmt_minutes(seconds: float) -> str:
    if seconds < 90:
        return f"~{int(seconds)}s"
    return f"~{seconds / 60:.0f} min"


def _print_plan(store: RunStore, pdf: Path, digital: list[int], scanned: list[int]) -> None:
    """Upfront summary: what will run, what is already cached, rough ETA."""
    todo_orient = [p for p in scanned if store.get("orient", p) is None]
    todo_ocr = [p for p in scanned if store.get("ocr", p) is None]
    todo_map = [p for p in scanned if store.get("extract", p) is None]
    todo_dig = [p for p in digital if store.get("extract", p) is None]
    r_orient = store.timing_rate("orient") or STAGE_RATES["orient"]
    r_ocr = store.timing_rate("ocr") or STAGE_RATES["ocr"]
    eta = (
        len(todo_orient) * r_orient
        + len(todo_ocr) * r_ocr
        + len(todo_dig) * STAGE_RATES["extract_digital"]
        + len(todo_map) * STAGE_RATES["extract_map"]
    )
    table = Table(title=f"plan: {pdf.name}", show_header=True)
    for col in ("", "pagini", "de procesat", "din cache"):
        table.add_column(col, justify="right")
    table.add_row("text nativ (rapid)", str(len(digital)), str(len(todo_dig)),
                  str(len(digital) - len(todo_dig)))
    table.add_row("scanate (OCR, lent)", str(len(scanned)), str(len(todo_ocr)),
                  str(len(scanned) - len(todo_ocr)))
    console.print(table)
    if eta > 5:
        console.print(
            f"[bold]durata estimata a extractiei: {_fmt_minutes(eta)}[/bold] "
            "(reluabil oricand — paginile terminate se sar automat)"
        )
        if len(todo_ocr) > 40:
            console.print(
                f"[dim]hint: `bgconvertor extract {pdf.name} --workers 4` "
                "ruleaza OCR-ul in paralel si scade timpul de ~3x[/dim]"
            )
    else:
        console.print("[dim]totul e in cache — extractia va fi instantanee[/dim]")


def _spawn_extract_workers(store: RunStore, pdf: Path, pages: list[int], workers: int) -> None:
    """Run orient+OCR for `pages` across separate processes (CPU-bound docling).

    Workers cooperate through the shared run store (each page = one artifact);
    the parent polls the store and renders live progress while they run."""
    import subprocess
    import sys
    import time as _time

    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    chunks = [pages[i::workers] for i in range(workers)]
    console.print(f"[bold]pornesc {workers} procese de extractie in paralel[/bold]")
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "bgconvertor.cli", "extract", str(pdf),
             "--pages", ",".join(map(str, chunk))],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for chunk in chunks if chunk
    ]
    target = set(pages)

    def _done(stage: str) -> int:
        return len(target & set(store.pages_done(stage)))

    with Progress(
        TextColumn("[bold]{task.description}"), BarColumn(), MofNCompleteColumn(),
        TimeElapsedColumn(), transient=True,
    ) as progress:
        t_orient = progress.add_task("orientare", total=len(pages))
        t_ocr = progress.add_task("OCR", total=len(pages))
        while any(p.poll() is None for p in procs):
            progress.update(t_orient, completed=_done("orient"))
            progress.update(t_ocr, completed=_done("ocr"))
            _time.sleep(3)
        progress.update(t_orient, completed=_done("orient"))
        progress.update(t_ocr, completed=_done("ocr"))
    console.print(
        f"workeri terminati: orientare {_done('orient')}/{len(pages)}, "
        f"OCR {_done('ocr')}/{len(pages)}"
    )
    if any(p.returncode for p in procs):
        console.print("[red]unii workeri au esuat — vezi `bgconvertor runs`[/red]")


def _run_extraction(
    config: RunConfig, store: RunStore, pdf: Path, selected: list[int], workers: int = 1
) -> None:
    """Shared pipeline front half: profile -> (digital | orient+ocr) -> extract."""
    from pypdf import PdfReader

    from . import profilepdf
    from .years import infer_budget_year_from_path

    budget_year = infer_budget_year_from_path(pdf)

    _stage_banner("Profilare pagini", "detectam care pagini au text nativ vs. scanate")
    reader = PdfReader(pdf)
    run_stage(store, "profile", selected, lambda p: profilepdf.profile_page(reader, p))
    digital_pages = [
        p for p in selected if (store.get("profile", p) or {}).get("has_text_layer")
    ]
    scanned_pages = sorted(set(selected) - set(digital_pages))
    _print_plan(store, pdf, digital_pages, scanned_pages)

    if digital_pages:
        import pdfplumber

        from .extract import digital

        _stage_banner("Extractie digitala", f"{len(digital_pages)} pagini cu text nativ")
        with pdfplumber.open(pdf) as plumber:
            summary = run_stage(
                store, "extract", digital_pages,
                lambda p: digital.extract_page(
                    plumber.pages[p - 1], budget_year=budget_year
                ),
            )
        console.print("digital " + summary.line())
        # Pages with a text layer but no ruled grid (copier-embedded OCR text)
        # are not 'detaliat' digital pages — route them through the OCR path.
        rerouted = [
            f["page"] for f in store.failures("extract")
            if f["page"] in digital_pages and any(
                marker in f.get("error", "")
                for marker in ("ruling lines", "grid header", "header row containing")
            )
        ]
        if rerouted:
            from .extract import scanned as sc

            _stage_banner("Extractie din stratul de text (nativ)",
                          "prima incercare pentru pagini digitale fara caroiaj")
            run_stage(
                store, "ocr_native", rerouted,
                lambda p: sc.ocr_page_native(
                    pdf, p,
                    cell_matching=config.docling_cell_matching,
                    tableformer_mode=config.tableformer_mode,
                ),
            )
            native_good = []
            native_threshold = 0.0 if config.prefer_native_text else config.structural_score_threshold
            for page in rerouted:
                mapped = sc.map_payload(
                    store.get("ocr_native", page) or {}, budget_year=budget_year
                )
                score = sc.structural_score(mapped)
                if mapped.get("lines") and score >= native_threshold:
                    mapped["candidate_selection"] = {
                        "selected": "native_text",
                        "score": score,
                        "candidates": [{"name": "native_text", "score": score}],
                    }
                    store.put("extract", page, mapped)
                    native_good.append(page)
            raster_pages = sorted(set(rerouted) - set(native_good))
            if raster_pages:
                console.print(
                    f"[yellow]{len(raster_pages)} pagini native au scor structural slab; "
                    "continua cu OCR raster si selectie pe validare[/yellow]"
                )
                scanned_pages = sorted(set(scanned_pages) | set(raster_pages))
            if native_good:
                console.print(
                    f"native: {len(native_good)} pagini acceptate fara OCR raster"
                )

    if scanned_pages:
        from .extract import orient, scanned

        todo = [p for p in scanned_pages if store.get("ocr", p) is None]
        if workers > 1 and len(todo) > workers:
            _stage_banner(
                "Orientare + OCR in paralel",
                f"{len(todo)} pagini pe {workers} procese",
            )
            _spawn_extract_workers(store, pdf, todo, workers)

        _stage_banner(
            "Detectare orientare",
            f"{len(scanned_pages)} pagini scanate — unele pot fi rotite 90/180/270°",
        )
        adaptive = orient.AdaptiveOrient()
        run_stage(
            store, "orient", scanned_pages,
            lambda p: adaptive.detect(profilepdf.render_page(pdf, p, scale=0.7)),
        )
        _stage_banner(
            "OCR + structura tabel (docling)",
            "cea mai lenta etapa; progresul se salveaza pagina cu pagina",
        )
        run_stage(
            store, "ocr", scanned_pages,
            lambda p: scanned.ocr_page(
                pdf, p,
                rotation=(store.get("orient", p) or {}).get("rotation", 0),
                scale=config.render_scale,
                cell_matching=config.docling_cell_matching,
                stamp_filter=config.stamp_filter,
                ocr_engine=config.ocr_engine,
                ocr_langs=tuple(config.ocr_langs),
                tableformer_mode=config.tableformer_mode,
            ),
        )

        # Score the first pass cheaply. Good pages stay single-pass; only
        # structurally weak pages receive one alternative OCR/render attempt.
        preliminary_context = None
        preliminary_page = None
        poor_pages: list[int] = []
        for page in scanned_pages:
            if preliminary_page != page - 1:
                preliminary_context = None
            preliminary = scanned.map_payload(
                store.get("ocr", page) or {},
                budget_year=budget_year,
                context=preliminary_context,
            )
            preliminary_page = page
            preliminary_context = preliminary.get("mapping_context")
            if scanned.structural_score(preliminary) < config.structural_score_threshold:
                poor_pages.append(page)

        if config.adaptive_ocr and poor_pages:
            _stage_banner(
                "Recuperare OCR adaptiva",
                f"{len(poor_pages)} pagini sub scorul structural "
                f"{config.structural_score_threshold:.2f}; un singur candidat alternativ",
            )
            run_stage(
                store, "ocr_recovery", poor_pages,
                lambda p: scanned.ocr_page(
                    pdf, p,
                    rotation=(store.get("orient", p) or {}).get("rotation", 0),
                    scale=config.render_scale,
                    cell_matching=not config.docling_cell_matching,
                    stamp_filter=False,
                    ocr_engine=config.ocr_engine,
                    ocr_langs=tuple(config.ocr_langs),
                    tableformer_mode=config.tableformer_mode,
                    adaptive_preprocessing=True,
                    max_deskew_degrees=config.max_deskew_degrees,
                ),
            )

        _stage_banner("Mapare tabele", "structura OCR -> linii bugetare (rapid)")
        first_page = min(scanned_pages)
        previous = store.get("extract", first_page - 1) if first_page > 1 else None
        mapping_state = {
            "page": first_page - 1 if previous and previous.get("mapping_context") else None,
            "context": previous.get("mapping_context") if previous else None,
        }

        def _advance_mapping(page: int, payload: dict) -> None:
            mapping_state["page"] = page
            mapping_state["context"] = payload.get("mapping_context")

        def _map_scanned(page: int) -> dict:
            if mapping_state["page"] != page - 1:
                mapping_state["context"] = None
            candidates = [(
                "ocr_baseline",
                scanned.map_payload(
                    store.get("ocr", page) or {},
                    budget_year=budget_year,
                    context=mapping_state["context"],
                ),
            )]
            recovery = store.get("ocr_recovery", page)
            if recovery is not None:
                candidates.append((
                    "ocr_preprocessed",
                    scanned.map_payload(
                        recovery,
                        budget_year=budget_year,
                        context=mapping_state["context"],
                    ),
                ))
            native = store.get("ocr_native", page)
            if native is not None:
                candidates.append((
                    "native_text",
                    scanned.map_payload(
                        native,
                        budget_year=budget_year,
                        context=mapping_state["context"],
                    ),
                ))
            payload = scanned.choose_best_payload(candidates)
            _advance_mapping(page, payload)
            return payload

        summary = run_stage(
            store, "extract", scanned_pages,
            _map_scanned,
            on_cached=_advance_mapping,
            force=True,
        )
        console.print("scanned " + summary.line())


@app.command()
def extract(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    pages: str | None = typer.Option(None, "--pages", help="ex. 1-10 sau 9,31,151"),
    workers: int = typer.Option(1, min=1, max=8, help="Procese worker OCR în paralel"),
):
    """Rulează extracția: pe bază de coordonate pentru paginile digitale, docling pentru scanări."""
    from pypdf import PdfReader

    config = _config()
    store = RunStore(config, pdf)
    selected = parse_pages(pages, len(PdfReader(pdf).pages))

    # `_run_extraction` profiles first and fans out only the genuinely stale
    # OCR pages. The parent then performs the cheap mapping pass in page order
    # so continuation schemas can propagate safely. Spawning here would load
    # Docling in N processes even for a fully cached remap.
    _run_extraction(config, store, pdf, selected, workers=workers)


@app.command()
def models():
    """Preseturile de modele disponibile pentru `--model-preset`."""
    from rich.table import Table

    from .llm.ledger import MODEL_PRICES
    from .llm.presets import DEFAULT_PRESET, PRESETS

    t = Table(title="preseturi de modele (furnizor:model)")
    t.add_column("preset", no_wrap=True)
    t.add_column("reparare", no_wrap=True)
    t.add_column("celule", no_wrap=True)
    t.add_column("$/MTok in/out", justify="right", no_wrap=True)
    t.add_column("descriere")
    for key, p in PRESETS.items():
        pin, pout = MODEL_PRICES[p.repair_model]
        name = f"[bold]{key}[/bold] (implicit)" if key == DEFAULT_PRESET else key
        repair = p.repair_model
        if p.fallback_model:
            repair += f" (+{p.fallback_model} pt. pagini)"
        t.add_row(name, repair, p.cell_model, f"{pin:g}/{pout:g}", p.description)
    console.print(t)
    console.print(
        "[dim]cheile API per furnizor: vezi .env.example; prețurile marcate "
        "«verificați» în ledger.py sunt plafoane estimative — confirmați "
        "lista de prețuri curentă înainte de rulări mari[/dim]"
    )


@app.command()
def costuri(
    csv_out: Path | None = typer.Option(None, "--csv", help="Scrie agregatul și ca CSV"),
):
    """Agregă registrele LLM per furnizor/model/zi — pentru reconcilierea cu facturile."""
    import csv as csv_mod
    from collections import defaultdict

    from .llm.presets import MODEL_ROUTES

    agg: dict[tuple, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0,
                                                  "hidden": 0, "cost": 0.0})
    config = _config()
    for led in sorted(config.runs_dir.glob("*/llm_ledger.jsonl")):
        for line in led.read_text().splitlines():
            r = json.loads(line)
            if r.get("cached"):
                continue
            model = r.get("model") or "?"
            vendor = ("anthropic" if model.startswith("claude-")
                      else (MODEL_ROUTES.get(model, ("?", "?"))[1] or "?")
                      .split("//")[-1].split("/")[0])
            day = (r.get("ts") or "")[:10]
            a = agg[(vendor, model, day)]
            a["calls"] += 1
            a["in"] += r.get("input_tokens", 0)
            a["out"] += r.get("output_tokens", 0)
            a["hidden"] += max(0, r.get("output_tokens", 0)
                               - r.get("visible_output_tokens", r.get("output_tokens", 0)))
            a["cost"] += r.get("cost_usd", 0.0)
    if not agg:
        console.print("[dim]niciun registru LLM sub runs/[/dim]")
        return
    t = Table(title="costuri LLM din registre (comparați cu facturile furnizorilor)")
    for col in ("furnizor", "model", "zi"):
        t.add_column(col, no_wrap=True)
    for col in ("apeluri", "tok. in", "tok. out", "din care gândire", "cost $"):
        t.add_column(col, justify="right")
    total = 0.0
    for (vendor, model, day), a in sorted(agg.items()):
        total += a["cost"]
        t.add_row(vendor, model, day, str(a["calls"]), f"{a['in']:,}",
                  f"{a['out']:,}", f"{a['hidden']:,}", f"{a['cost']:.2f}")
    console.print(t)
    console.print(f"[bold]total registre: ${total:.2f}[/bold] — atenție: apelurile "
                  "dinaintea corecției de thinking (24.08.2026) subestimează costul Gemini")
    if csv_out:
        with csv_out.open("w", newline="") as f:
            w = csv_mod.writer(f)
            w.writerow(["furnizor", "model", "zi", "apeluri", "tok_in", "tok_out",
                        "tok_gandire", "cost_usd"])
            for (vendor, model, day), a in sorted(agg.items()):
                w.writerow([vendor, model, day, a["calls"], a["in"], a["out"],
                            a["hidden"], round(a["cost"], 4)])
        console.print(f"[dim]scris {csv_out}[/dim]")


@app.command()
def triage(pdf: Path = typer.Argument(..., exists=True, readable=True)):
    """Verificare preliminară: clasifică fișierul, estimează costul/timpul, semnalează layout-urile necunoscute.

    Eșantionează câteva pagini prin stiva reală de extracție (~1 min pentru scanări);
    munca eșantionată e pusă în cache și refolosită de conversia propriu-zisă."""
    from .triage import run_triage

    config = _config()
    store = RunStore(config, pdf)
    _stage_banner("Triaj", "profilare + esantion de pagini prin pipeline-ul real")
    r = run_triage(config, store, pdf)

    table = Table(title=f"triaj: {pdf.name}")
    table.add_column("")
    table.add_column("", overflow="fold")
    table.add_row("pagini", f"{r['pages']} ({r['digital_pages']} text nativ, {r['scanned_pages']} spre OCR)")
    if r["digital_status"]:
        table.add_row("grila digitala", r["digital_status"])
    if r["layouts_sampled"]:
        table.add_row("layout-uri (esantion)", ", ".join(f"{k}×{v}" for k, v in r["layouts_sampled"].items()))
        table.add_row("calitate OCR", ", ".join(f"{k}×{v}" for k, v in r["ocr_grades"].items()))
    table.add_row("durata estimata extractie", f"~{r['est_extraction_minutes']} min (1 proces; --workers 4 ≈ /3)")
    table.add_row("cost LLM estimat", f"~${r['est_llm_cost_usd']} (~{r['est_fallback_pages']} pagini fallback + reparatii)")
    console.print(table)
    if r["unknown_layouts"]:
        console.print(
            f"[yellow]⚠ layout necunoscut in esantion: {', '.join(r['unknown_layouts'])} — "
            "paginile de acest fel pot iesi subtiri; verifica-le in raport dupa conversie[/yellow]"
        )
    console.print(f"[bold]comanda recomandata:[/bold] {r['recommended']}")


@app.command()
def report(pdf: Path = typer.Argument(..., exists=True)):
    """Raport de calitate + cost pentru un PDF, din depozitul său de rulări."""
    config = _config()
    store = RunStore(config, pdf)
    table = Table(title=f"report: {pdf.name}")
    for col in ("stage", "pages done", "failures"):
        table.add_column(col, justify="right")
    for stage in ("profile", "orient", "ocr", "extract", "llm_extract"):
        d = (store.root / stage)
        if d.is_dir():
            table.add_row(stage, str(len(store.pages_done(stage))), str(len(store.failures(stage))))
    console.print(table)

    ledger_path = store.root / "llm_ledger.jsonl"
    if ledger_path.exists():
        from collections import Counter

        recs = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        by_purpose = Counter()
        cost_by_purpose: dict = Counter()
        for r in recs:
            by_purpose[r["purpose"]] += 1
            cost_by_purpose[r["purpose"]] += r["cost_usd"]
        console.print(f"[bold]LLM lifetime[/bold]: {len(recs)} calls, "
                      f"${sum(r['cost_usd'] for r in recs):.2f}")
        for purpose, n in by_purpose.most_common():
            console.print(f"  {purpose}: {n} calls, ${cost_by_purpose[purpose]:.2f}")
    xlsx = pdf.with_suffix(".xlsx")
    if xlsx.exists():
        console.print(f"workbook: [bold]{xlsx}[/bold] (see 'Sumar calitate' sheet)")


@app.command()
def convert(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    pages: str | None = typer.Option(None, "--pages"),
    out: Path | None = typer.Option(None, help="Fișierul .xlsx de ieșire (implicit: <pdf>.xlsx)"),
    llm: str | None = typer.Option(None, help="off | repair (implicit din configurație)"),
    max_llm_cost: float | None = typer.Option(
        None, min=0.0, max=5.0, help="Buget strict în USD pentru repararea LLM (maxim public: $5)"
    ),
    model_preset: str | None = typer.Option(
        None, "--model-preset",
        help="Preset de modele «furnizor:model» — lista: `bgconvertor models`"),
    workers: int = typer.Option(1, min=1, max=8, help="Procese worker OCR în paralel"),
):
    """Pipeline complet: profilare -> extracție -> asamblare -> validare [-> reparare] -> Excel."""
    from pypdf import PdfReader

    from . import export as export_mod
    from . import nomenclator as nom
    from .assemble import assemble
    from .model import ConversionResult, Issue
    from .validate import validate as run_validate

    config = _config()
    if llm:
        config.llm.mode = llm
    if max_llm_cost is not None:
        config.llm.max_cost_usd = max_llm_cost
    if model_preset:
        from .llm import presets

        try:
            p = presets.apply(config, model_preset)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None
        preset_models = [p.repair_model, p.cell_model, p.fallback_model or p.repair_model]
        if config.llm.batch and not all(m.startswith("claude-") for m in preset_models):
            console.print("[red]modul Batch API este disponibil doar pentru "
                          "modele Anthropic[/red]")
            raise typer.Exit(2)
        desc = f"reparare {p.repair_model}, celule {p.cell_model}"
        if p.fallback_model:
            desc += f", transcriere pagină {p.fallback_model}"
        console.print(f"model preset: [bold]{model_preset}[/bold] — {desc}")
    store = RunStore(config, pdf)
    n_pages = len(PdfReader(pdf).pages)
    selected = parse_pages(pages, n_pages)
    out_path = out or pdf.with_suffix(".xlsx")
    from .manifest import find_manifest

    governing_manifest = find_manifest(pdf.parent)
    corpus_target = (
        governing_manifest is not None
        and out_path.resolve() == pdf.with_suffix(".xlsx").resolve()
    )
    if corpus_target and selected != list(range(1, n_pages + 1)):
        console.print(
            "[red]o conversie partiala nu poate suprascrie artefactele publice ale "
            "corpusului; foloseste --out cu un alt nume[/red]"
        )
        raise typer.Exit(2)
    if corpus_target and config.llm.mode != "off" and config.llm.max_cost_usd > 5.0:
        console.print("[red]plafonul LLM pentru un artefact public este $5.00/PDF[/red]")
        raise typer.Exit(2)

    size_mb = pdf.stat().st_size / 1e6
    llm_desc = (
        f"[green]{config.llm.mode}[/green] (buget ${config.llm.max_cost_usd:.2f}/rulare)"
        if config.llm.mode != "off" else "[dim]off — celulele nerezolvate raman marcate[/dim]"
    )
    console.print(
        f"[bold]bgconvertor[/bold] · {pdf.name} · {size_mb:.1f} MB · "
        f"{len(selected)}/{n_pages} pagini · LLM: {llm_desc}"
    )
    console.print(
        "[dim]etape: profilare -> extractie -> asamblare -> validare"
        + (" -> reparare LLM" if config.llm.mode != "off" else "")
        + " -> Excel; totul e reluabil (cache per pagina)[/dim]"
    )
    _run_extraction(config, store, pdf, selected, workers=workers)
    registry = nom.load_registry(config.reference_dir)

    ledger = client = None
    if config.llm.mode in ("repair", "full"):
        from collections import Counter

        from . import profilepdf
        from .llm.client import LLMClient
        from .llm.fallback import (
            FALLBACK_PROMPT,
            extract_page_llm,
            fallback_benefit,
            fallback_columns,
            fallback_max_tokens,
            needs_fallback,
        )
        from .llm.ledger import Ledger, estimate_request_cost
        from .llm.planner import RecoveryCandidate, select_candidates

        # ONE ledger for the whole run: fallback + repair share the budget
        ledger = Ledger(
            path=store.root / "llm_ledger.jsonl",
            max_cost_usd=config.llm.max_cost_usd,
            max_calls=config.llm.max_calls,
        )
        client = LLMClient(config, ledger, store.root / "llm_cache")
        fallback_candidates_pages = [
            p for p in selected
            if (pl := store.get("extract", p)) is not None
            and pl.get("layout") != "digital_detail"
            and needs_fallback(pl)
            and store.get("llm_extract", p) is None
        ]
        fallback_plan = None
        if fallback_candidates_pages:
            col_freq: Counter = Counter()
            for p in selected:
                pl = store.get("extract", p) or {}
                col_freq.update({c for ln in pl.get("lines", []) for c in ln.get("values", {})})
            corpus_columns = [column for column, _count in col_freq.most_common()]

            # Reserve only the demand the deterministic validation can already
            # see, capped at 40% of the file budget. If there is no repairable
            # arithmetic/cell work, full-page rescue may use the whole cap.
            preliminary_documents = assemble(store, selected, registry)
            preliminary = ConversionResult(pdf=pdf.name, documents=preliminary_documents)
            run_validate(preliminary, registry)
            sum_signals = sum(
                1
                for document in preliminary.documents
                for line in document.lines
                if any(issue.check == "V4_hierarchy" for issue in line.issues)
            )
            cell_pages = {
                line.page
                for document in preliminary.documents
                for line in document.lines
                if any(
                    issue.check == "V7_hygiene" and "unparseable" in issue.message
                    for issue in line.issues
                )
            }
            repair_unit_cost = estimate_request_cost(
                config.llm.repair_model,
                prompt_chars=1000,
                output_tokens=2048,
                image_pixels=800_000,
            )
            cell_unit_cost = estimate_request_cost(
                config.llm.cell_model,
                prompt_chars=1000,
                output_tokens=2048,
                image_pixels=2_100_000,
            )
            repair_reserve = min(
                ledger.remaining_cost_usd * 0.4,
                sum_signals * repair_unit_cost + len(cell_pages) * cell_unit_cost,
            )
            fallback_budget = max(0.0, ledger.remaining_cost_usd - repair_reserve)

            prepared = {}
            candidates = []
            fb_model = config.llm.fallback_model or config.llm.repair_model
            for page in fallback_candidates_pages:
                payload = store.get("extract", page) or {}
                columns = fallback_columns(payload, corpus_columns)
                max_tokens = fallback_max_tokens(payload, len(columns))
                prompt = FALLBACK_PROMPT.format(
                    columns=", ".join(f'"{column}"' for column in columns)
                )
                prepared[page] = (columns, max_tokens)
                candidates.append(RecoveryCandidate(
                    key=f"fallback:p{page}",
                    kind="fallback_extract",
                    page=page,
                    benefit_units=fallback_benefit(payload),
                    estimated_cost_usd=estimate_request_cost(
                        fb_model,
                        len(prompt),
                        max_tokens,
                        image_pixels=2_100_000,
                    ),
                    detail=(
                        f"{payload.get('n_numeric_cells', 0)} OCR numeric tokens; "
                        f"{len(columns)} requested columns"
                    ),
                ))
            fallback_plan = select_candidates(
                candidates,
                fallback_budget,
                max_calls=ledger.remaining_calls,
            )
            fallback_pages = [candidate.page for candidate in fallback_plan.selected]
            _stage_banner(
                "Extractie LLM pagina-intreaga",
                f"{len(fallback_pages)}/{len(fallback_candidates_pages)} pagini selectate "
                "după câștigul estimat per dolar",
            )
            console.print(
                f"  rezervare worst-case selectată: ~${fallback_plan.estimated_cost_usd:.2f} · "
                f"rezervat pentru reparații țintite: ~${repair_reserve:.2f} · "
                f"{len(fallback_plan.skipped)} pagini amânate de planner"
            )

            def llm_page(p: int):
                img = profilepdf.render_page(pdf, p, scale=config.render_scale)
                rot = (store.get("orient", p) or {}).get("rotation", 0)
                if rot:
                    img = img.rotate(rot, expand=True)
                columns, max_tokens = prepared[p]
                return extract_page_llm(
                    client,
                    img,
                    columns,
                    p,
                    max_tokens=max_tokens,
                )

            if fallback_pages:
                run_stage(
                    store, "llm_extract", fallback_pages, llm_page,
                    concurrency=config.llm.concurrency,
                )

        plan_record = {
            "schema_version": 1,
            "public_file_cap_usd": config.llm.max_cost_usd,
            "fallback": fallback_plan.as_dict() if fallback_plan is not None else None,
            "note": (
                "Soft planner estimates order work; the ledger reservation before each "
                "request remains the hard spending authority."
            ),
        }
        (store.root / "llm_plan.json").write_text(
            json.dumps(plan_record, ensure_ascii=False, indent=2) + "\n"
        )

    _stage_banner("Asamblare + validare", "documente, sectiuni, coduri, sume incrucisate")
    documents = assemble(store, selected, registry)
    if not documents:
        console.print("[red]no documents assembled — nothing to export[/red]")
        raise typer.Exit(1)
    result = ConversionResult(
        pdf=pdf.name,
        documents=documents,
        pages_expected=n_pages,
        pages_selected=selected,
        pages_processed=[p for p in selected if store.get("extract", p) is not None],
    )
    run_validate(result, registry)
    pre = result.stats()
    console.print(
        f"  {pre['documents']} documente, {pre['lines']} linii — inainte de reparare: "
        f"{pre['pct_clean']}% strict verificate, {pre['issues']['error']} erori"
    )

    if config.llm.mode == "repair" and client is not None:
        from . import profilepdf
        from .llm.orchestrate import (
            estimate_sum_repair_candidates,
            estimate_unparseable_candidates,
            repair_document,
            repair_unparseable,
        )
        from .llm.planner import select_candidates

        sum_candidates = [
            candidate
            for document_index, document in enumerate(result.documents)
            for candidate in estimate_sum_repair_candidates(
                document,
                config.llm,
                row_crops_available=True,
                job_key_prefix=f"doc:{document_index}|",
            )
        ]
        sum_plan = select_candidates(
            sum_candidates,
            ledger.remaining_cost_usd,
            max_calls=ledger.remaining_calls,
        )
        allowed_sum_jobs = {candidate.key for candidate in sum_plan.selected}
        _stage_banner(
            "Reparare LLM",
            f"{len(sum_plan.selected)}/{len(sum_candidates)} grupuri cu sume rupte "
            "selectate global + celule ilizibile; "
            "o corectie se aplica DOAR daca suma re-citita bate",
        )
        if sum_plan.skipped:
            result.issues.append(Issue(
                check="V6_repair",
                severity="info",
                page=sum_plan.skipped[0].page,
                message=(
                    f"file-wide budget planner selected {len(sum_plan.selected)} sum groups "
                    f"(~${sum_plan.estimated_cost_usd:.3f} reserved worst-case) and deferred "
                    f"{len(sum_plan.skipped)} lower-yield groups"
                ),
            ))
        plan_path = store.root / "llm_plan.json"
        plan_record = json.loads(plan_path.read_text()) if plan_path.exists() else {
            "schema_version": 1,
            "public_file_cap_usd": config.llm.max_cost_usd,
        }
        plan_record["sum_repair"] = sum_plan.as_dict()
        plan_path.write_text(json.dumps(plan_record, ensure_ascii=False, indent=2) + "\n")

        def row_locator(p: int, codes: set) -> tuple | None:
            pl = store.get("ocr", p) or store.get("ocr_native", p) or {}
            grids, rows_y = pl.get("tables_raw", []), pl.get("tables_rows_y", [])
            hits = []
            for grid, ys in zip(grids, rows_y, strict=False):
                for row, band in zip(grid, ys, strict=False):
                    if any(c.strip().replace(" ", "") in codes for c in row if c.strip()):
                        hits.append(band)
            if not hits:
                return None
            return min(b[0] for b in hits), max(b[1] for b in hits)

        def page_image(p: int):
            img = profilepdf.render_page(pdf, p, scale=config.render_scale)
            rot = (store.get("orient", p) or {}).get("rotation", 0)
            return img.rotate(rot, expand=True) if rot else img

        for document_index, doc in enumerate(result.documents):
            result.issues.extend(
                repair_document(
                    doc,
                    client,
                    page_image,
                    row_locator=row_locator,
                    allowed_job_keys=allowed_sum_jobs,
                    job_key_prefix=f"doc:{document_index}|",
                )
            )
        cell_candidates = [
            candidate
            for document_index, document in enumerate(result.documents)
            for candidate in estimate_unparseable_candidates(
                document,
                config.llm,
                job_key_prefix=f"doc:{document_index}|",
            )
        ]
        cell_plan = select_candidates(
            cell_candidates,
            ledger.remaining_cost_usd,
            max_calls=ledger.remaining_calls,
        )
        allowed_cell_jobs = {candidate.key for candidate in cell_plan.selected}
        if cell_plan.skipped:
            result.issues.append(Issue(
                check="V6_repair",
                severity="info",
                page=cell_plan.skipped[0].page,
                message=(
                    f"file-wide budget planner selected {len(cell_plan.selected)} "
                    f"unparseable-cell pages and deferred {len(cell_plan.skipped)} "
                    "lower-confidence pages"
                ),
            ))
        plan_record["unparseable_cell"] = cell_plan.as_dict()
        plan_path.write_text(json.dumps(plan_record, ensure_ascii=False, indent=2) + "\n")
        for document_index, doc in enumerate(result.documents):
            result.issues.extend(repair_unparseable(
                doc,
                client,
                page_image,
                allowed_job_keys=allowed_cell_jobs,
                job_key_prefix=f"doc:{document_index}|",
            ))
        console.print(ledger.summary())

    publication_record = None
    if corpus_target:
        from .publication import publish_corpus_result

        try:
            publication_record = publish_corpus_result(
                result,
                pdf,
                out_path,
                governing_manifest,
                llm_preset=model_preset,
                llm_cost_usd=ledger.run_cost_usd if ledger is not None else 0.0,
                llm_lifetime_cost_usd=ledger.total_cost_usd if ledger is not None else 0.0,
            )
        except ValueError as exc:
            console.print(f"[red]artefactele publice nu au fost inlocuite: {exc}[/red]")
            raise typer.Exit(1) from None
    else:
        export_mod.export(result, out_path)

    stats = result.stats()
    console.print(f"\n[bold green]✓ scris {out_path}[/bold green]")
    console.print(
        f"{stats['documents']} documente · {stats['lines']} linii de date · "
        f"[bold]{stats['pct_clean']}% strict verificate[/bold] · "
        f"{stats['issues']['error']} erori · {stats['issues']['warning']} avertismente"
    )
    if stats["issues"]["error"]:
        console.print(
            "[dim]fiecare problema e localizata in foaia 'Probleme' (pagina + cod + coloana); "
            "randurile afectate sunt colorate in foile de date[/dim]"
        )
    if config.llm.mode != "off":
        console.print(
            "[dim]hint: reluarea cu --max-llm-cost mai mare continua repararea de unde "
            "a ramas (apelurile facute se refolosesc gratuit din cache)[/dim]"
        )
    if publication_record is not None:
        console.print(
            f"[dim]analysis: {publication_record['analysis']} · "
            f"bundle: {publication_record['artifacts']['bundle_id']}[/dim]"
        )
    console.print(f"[dim]detalii oricand: `bgconvertor report {pdf.name}`[/dim]")


@app.command("eval")
def eval_cmd(
    stage: str = typer.Option("extract", help="Etapa din depozitul de rulări de evaluat"),
    fixtures: Path = typer.Option(Path("tests/fixtures/golden"), help="Directorul fixture-urilor golden"),
    strict: bool = typer.Option(False, help="Iese cu cod 1 dacă nu se potrivesc toate ancorele"),
    min_anchors: int = typer.Option(0, help="Iese cu cod 1 dacă ancorele potrivite scad sub prag (poartă anti-regresie)"),
    min_text_assertions: int = typer.Option(
        0, help="Iese cu cod 1 dacă aserțiunile text potrivite scad sub prag"
    ),
    require_cell_ground_truth: int = typer.Option(
        0, help="Număr minim de fixture-uri cu inventar numeric exhaustiv evaluate"
    ),
    min_layout_cell_recall: float = typer.Option(
        0.0, min=0.0, max=100.0,
        help="Recall numeric minim pentru fiecare layout cu etalon exhaustiv",
    ),
    min_layout_cell_precision: float = typer.Option(
        0.0, min=0.0, max=100.0,
        help="Precizie numerică minimă pentru fiecare layout cu etalon exhaustiv",
    ),
    json_out: Path | None = typer.Option(None, help="Raport JSON machine-readable"),
):
    """Ancore selectate plus recall/precizie pe etaloanele exhaustive disponibile."""
    from . import eval_harness

    config = _config()
    results = eval_harness.evaluate_all(config, fixtures, Path.cwd(), stage=stage)

    table = Table(title=f"eval vs golden fixtures (stage: {stage})")
    for col in (
        "fixture", "layout", "status", "anchors", "hard", "text",
        "cell recall", "cell precision",
    ):
        table.add_column(col)
    for r in results:
        anchors = f"{r.anchors_matched}/{r.anchors_total}" if r.anchors_total else "-"
        hard = f"{r.hard_matched}/{r.hard_total}" if r.hard_total else "-"
        text = f"{r.text_matched}/{r.text_total}" if r.text_total else "-"
        cell_recall = f"{r.cells_matched}/{r.cells_expected}" if r.cells_expected else "-"
        cell_precision = (
            f"{r.cells_matched}/{r.cells_predicted}" if r.cells_predicted else "-"
        )
        style = "dim" if r.status == "missing" else (
            "green" if not r.misses else "yellow"
        )
        table.add_row(
            r.fixture_id, r.layout, r.status, anchors, hard, text,
            cell_recall, cell_precision, style=style,
        )
    console.print(table)

    for r in results:
        for miss in r.misses:
            console.print(f"  [yellow]{r.fixture_id}[/yellow] {miss}")

    report = eval_harness.evaluation_report(results)
    total = report["anchors"]["total"]
    matched = report["anchors"]["matched"]
    evaluated = report["fixtures"]["evaluated"]
    cell_recall = report["validated_cell_recall"]
    cell_precision = report["numeric_cell_precision_against_ground_truth"]
    console.print(
        f"\n[bold]{matched}/{total}[/bold] ancore selectate potrivite · "
        f"{report['text_assertions']['matched']}/{report['text_assertions']['total']} "
        f"asertiuni text · {evaluated}/{len(results)} fixture-uri evaluate"
    )
    if cell_recall["fixtures"]:
        console.print(
            f"[bold]{cell_recall['matched']}/{cell_recall['total']}[/bold] celule "
            f"exhaustive regăsite ({cell_recall['pct']}%) · "
            f"[bold]{cell_precision['correct']}/{cell_precision['predicted']}[/bold] "
            f"precizie față de etalon ({cell_precision['pct']}%)"
        )
    console.print(
        "[dim]selected_anchor_recall rămâne o poartă parțială; metricile pe celule "
        "se aplică numai grupurilor inventariate exhaustiv, nu întregului corpus[/dim]"
    )
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        console.print(f"[dim]raport: {json_out}[/dim]")
    if strict and (
        matched < total
        or report["text_assertions"]["matched"] < report["text_assertions"]["total"]
        or evaluated < len(results)
        or any(
            r.cell_ground_truth
            and (r.cells_matched < r.cells_expected or r.cells_matched < r.cells_predicted)
            for r in results
        )
    ):
        raise typer.Exit(1)
    if min_anchors and matched < min_anchors:
        console.print(f"[red]regresie: {matched} ancore potrivite < pragul de {min_anchors}[/red]")
        raise typer.Exit(1)
    text_matched = report["text_assertions"]["matched"]
    if min_text_assertions and text_matched < min_text_assertions:
        console.print(
            f"[red]regresie: {text_matched} aserțiuni text potrivite < pragul de "
            f"{min_text_assertions}[/red]"
        )
        raise typer.Exit(1)
    cell_fixtures = cell_recall["fixtures"]
    if require_cell_ground_truth and cell_fixtures < require_cell_ground_truth:
        console.print(
            f"[red]acoperire insuficientă: {cell_fixtures} fixture-uri exhaustive "
            f"evaluate < pragul de {require_cell_ground_truth}[/red]"
        )
        raise typer.Exit(1)
    for layout, metrics in report["by_layout"].items():
        if not metrics["cell_ground_truth_evaluated"]:
            continue
        recall = metrics["cell_recall_pct"]
        precision = metrics["cell_precision_pct"]
        if min_layout_cell_recall and recall < min_layout_cell_recall:
            console.print(
                f"[red]recall numeric {layout}: {recall}% < "
                f"{min_layout_cell_recall}%[/red]"
            )
            raise typer.Exit(1)
        if min_layout_cell_precision and precision < min_layout_cell_precision:
            console.print(
                f"[red]precizie numerică {layout}: {precision}% < "
                f"{min_layout_cell_precision}%[/red]"
            )
            raise typer.Exit(1)


@app.command()
def batch(
    data_dir: Path = typer.Argument(Path("data/2026"), exists=True),
    group: int = typer.Option(5, min=1, help="Orașe per grup de checkpoint"),
    workers: int = typer.Option(4, min=1, max=8),
    llm: str = typer.Option("repair", help="off | repair"),
    max_llm_cost: float = typer.Option(
        3.00, min=0.0, max=5.0, help="Buget USD per oraș (plafon public $5)"
    ),
    model_preset: str | None = typer.Option(
        None, "--model-preset",
        help="Preset «furnizor:model» pentru fiecare oraș — lista: `bgconvertor models`"),
    only: str = typer.Option("pending", help="pending | failed | all"),
    limit: int = typer.Option(0, help="Oprește după N orașe (0 = fără limită)"),
):
    """Convertește corpusul oraș cu oraș, în grupuri, scriind statusul în manifest.

    Fiecare oraș rulează în propriul proces (fail-soft); fișierul Excel ajunge
    lângă PDF-ul său; blocul `conversion` din manifest înregistrează statusul,
    calitatea și costul. Poate fi întrerupt și reluat în siguranță — totul se reia.
    """
    import datetime as dt
    import subprocess
    import sys

    from .manifest import Manifest

    manifest = Manifest(data_dir / "manifest.json")
    todo = []
    for city in manifest.cities():
        st = (city.entry.get("conversion") or {}).get("status")
        if only == "pending" and st == "converted":
            continue
        if only == "failed" and st != "failed":
            continue
        if not city.pdf.exists():
            if not (city.entry.get("conversion") or {}).get("status"):
                manifest.set_status(city, status="missing_pdf")
            continue
        todo.append(city)
    if limit:
        todo = todo[:limit]
    if not todo:
        console.print("[green]nimic de procesat — totul e convertit[/green]")
        return

    console.print(f"[bold]batch: {len(todo)} orase, grupuri de {group}, "
                  f"LLM {llm} (${max_llm_cost:.2f}/oras)[/bold]")
    done = 0
    for i in range(0, len(todo), group):
        chunk = todo[i:i + group]
        console.print(f"\n[bold cyan]— grupul {i // group + 1}: "
                      f"{', '.join(c.name for c in chunk)}[/bold cyan]")
        for city in chunk:
            out_xlsx = city.pdf.with_suffix(".xlsx")
            cmd = [sys.executable, "-m", "bgconvertor.cli", "convert", str(city.pdf),
                   "--workers", str(workers), "--out", str(out_xlsx)]
            if llm != "off":
                cmd += ["--llm", llm, "--max-llm-cost", str(max_llm_cost)]
                if model_preset:
                    cmd += ["--model-preset", model_preset]
            console.print(f"  [bold]{city.name}[/bold] ({city.siruta}) …")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr)[-400:]
                error = tail.splitlines()[-1] if tail else f"exit {proc.returncode}"
                previous = (city.entry.get("conversion") or {}).get("status")
                if previous == "converted":
                    # A failed refresh must not unpublish the last audited bundle.
                    manifest.set_status(
                        city,
                        last_attempt_status="failed",
                        last_attempt_error=error,
                        last_attempt_at=dt.datetime.now().isoformat(timespec="seconds"),
                    )
                else:
                    manifest.set_status(
                        city, status="failed", error=error,
                        at=dt.datetime.now().isoformat(timespec="seconds"),
                    )
                console.print(f"  [red]✗ {city.name} a esuat[/red]")
                continue
            fresh = Manifest(data_dir / "manifest.json")
            published = fresh.find(city.siruta)
            conv = (published.entry.get("conversion") or {}) if published else {}
            if conv.get("status") != "converted" or not conv.get("artifacts"):
                console.print(
                    f"  [red]✗ {city.name}: procesul s-a terminat fără un bundle publicat[/red]"
                )
                continue
            quality = conv.get("quality") or {}
            done += 1
            console.print(
                f"  [green]✓ {city.name}[/green]: {quality.get('lines', '?')} linii, "
                f"{quality.get('pct_lines_strictly_verified', '?')}% strict verificate"
            )
        console.print(f"[dim]checkpoint: {done}/{len(todo)} convertite — "
                      "moment bun pentru un commit al data/[/dim]")

site_app = typer.Typer(no_args_is_help=True)
app.add_typer(site_app, name="site", help="Site static GitHub Pages pentru corpus")


@site_app.command("build")
def site_build(
    data_dir: Path = typer.Option(Path("data"), exists=True,
                                  help="Rădăcina data/ (toți anii) sau un singur data/<an>"),
    out: Path = typer.Option(Path("site")),
    base_url: str = typer.Option("", help="Prefix de URL când site-ul e servit dintr-o subcale"),
):
    """Generează indexul corpusului + paginile per oraș, doar din fișierele comise.

    Cu rădăcina data/, cel mai recent an ajunge la rădăcina site-ului, iar
    edițiile anterioare la <out>/<an>/, legate între ele din index.
    """
    from .manifest import Manifest
    from .site import build, build_all

    if (data_dir / "manifest.json").exists():
        results = [build(Manifest(data_dir / "manifest.json"), out, base_url)]
    else:
        results = build_all(data_dir, out, base_url)
    for r in results:
        console.print(
            f"[bold green]✓ site: {r['cities']} orase, {r['converted_pages']} pagini "
            f"de analiza -> {r['out']}/[/bold green]"
        )


exec_app = typer.Typer(no_args_is_help=True)
app.add_typer(exec_app, name="execution", help="Execuția bugetară trimestrială (rapoarte Forexebug)")


@exec_app.command("build")
def execution_build(
    exec_dir: Path = typer.Option(Path("data/execution/2026"), exists=True,
                                  help="Rădăcina rapoartelor pentru un an"),
    only: str | None = typer.Option(None, help="Filtrează după slug de oraș, ex. 1017-alba-iulia"),
):
    """Parsează rapoartele Forexebug și scrie execution.json lângă ele.

    Valorile se normalizează în mii lei; totalurile tipărite în raport sunt
    suma de control. Codurile pe care nomenclatorul nu le enumeră sunt
    numărate, nu inventate.
    """
    from .execution import build_city, write_snapshot
    from .nomenclator import load_registry

    config = _config()
    registry = load_registry(config.reference_dir)
    counties = sorted(d for d in exec_dir.iterdir() if d.is_dir() and "-" in d.name)
    written = problems = 0
    for county in counties:
        for city in sorted(d for d in county.iterdir() if d.is_dir()):
            if only and only not in city.name:
                continue
            snap = build_city(exec_dir, county.name, city.name, registry)
            if not snap:
                continue
            write_snapshot(snap, city / "execution.json")
            written += 1
            problems += bool(snap["probleme"])
            console.print(
                f"  {city.name:32s} T{snap['trimestru']} · "
                f"ven {snap['venituri']:>11,.0f} · chelt {snap['cheltuieli']:>11,.0f}"
                + (f" [yellow]⚠ {'; '.join(snap['probleme'])[:60]}[/yellow]"
                   if snap["probleme"] else "")
            )
    console.print(f"[bold green]✓ execuție: {written} orase[/bold green]"
                  + (f" [yellow]({problems} cu probleme)[/yellow]" if problems else ""))


@exec_app.command("ingest")
def execution_ingest(
    exec_dir: Path = typer.Option(Path("data/execution/2026"), exists=True),
    quarter: int | None = typer.Option(None, min=1, max=4,
                                       help="Doar un trimestru (implicit: toate cele găsite pe disc)"),
):
    """Verifică rapoartele așezate pe disc și le scrie în manifest, apoi rebuild.

    Fișierele se descarcă manual din portalul ANAF (căutarea cere CAPTCHA) și
    se pun în structura existentă; comanda le verifică — identitatea entității,
    data raportului, sumele de control — le înregistrează cu checksum și
    regenerează instantaneele.
    """
    from .execution import ingest_quarter, quarters_on_disk
    from .nomenclator import load_registry

    registry = load_registry(_config().reference_dir)
    quarters = [quarter] if quarter else quarters_on_disk(exec_dir)
    if not quarters:
        console.print(f"[yellow]niciun raport sub {exec_dir}/<județ>/<oraș>/q<N>/[/yellow]")
        raise typer.Exit(1)

    failed = 0
    for q in quarters:
        r = ingest_quarter(exec_dir, q, registry)
        failed += r["failed"]
        style = "green" if not r["failed"] else "yellow"
        console.print(f"[bold {style}]T{q}: {r['verified']} verificate, "
                      f"{r['failed']} cu probleme, {r['missing']} lipsă[/bold {style}]")
        for e in r["entries"]:
            if e["verification_status"] == "failed":
                console.print(f"  [red]✗ {e['capital_name']}[/red]: {'; '.join(e['problems'])[:90]}")
    if failed:
        # a file that fails verification never reaches the snapshots
        console.print(f"[red]✗ {failed} rapoarte nu au trecut verificarea — "
                      "instantaneele NU au fost regenerate (vezi verification.json)[/red]")
        raise typer.Exit(1)
    execution_build(exec_dir=exec_dir, only=None)


@exec_app.command("status")
def execution_status(
    exec_dir: Path = typer.Option(Path("data/execution/2026"), exists=True),
    today: str | None = typer.Option(None, help="Data de referință (implicit: azi)"),
    as_json: bool = typer.Option(False, "--json", help="Ieșire pentru automatizare"),
):
    """Ce trimestre sunt aduse și care ar trebui să fie deja publicate."""
    import datetime as dt

    from .execution import quarter_status

    year = int(exec_dir.name)
    ref = dt.date.fromisoformat(today) if today else dt.date.today()
    st = quarter_status(exec_dir, year, ref)
    if as_json:
        print(json.dumps(st, ensure_ascii=False))
        return
    console.print(f"[bold]execuție {year}[/bold] (la {st['azi']})")
    console.print(f"  complete: {st['trimestre_complete'] or '—'}")
    console.print(f"  publicat de MFin până acum: T{st['trimestru_asteptat'] or '—'}")
    if st["de_adus"]:
        console.print(f"  [yellow]de adus: {st['de_adus']}[/yellow]"
                      + (f" (fără manifest: {st['manifest_lipsa']})" if st["manifest_lipsa"] else ""))
    else:
        console.print("  [green]la zi[/green]")


@exec_app.command("new-quarter")
def execution_new_quarter(
    quarter: int = typer.Argument(..., min=1, max=4),
    exec_dir: Path = typer.Option(Path("data/execution/2026"), exists=True),
):
    """Pregătește manifestul unui trimestru nou, copiind entitățile din cel anterior.

    URL-urile rămân goale: se obțin din căutarea de pe portalul ANAF, care
    este protejată cu CAPTCHA și nu poate fi automatizată.
    """
    from .execution import scaffold_quarter

    out = scaffold_quarter(exec_dir, quarter)
    n = len(json.loads(out.read_text())["entries"])
    console.print(f"[bold green]✓ {out}[/bold green] — {n} intrări, câmpul source_url gol")
    console.print("[dim]Completează URL-urile din portalul ANAF, apoi:\n"
                  f"  python3 {exec_dir}/download.py --quarter {quarter}\n"
                  f"  uv run bgconvertor execution build --exec-dir {exec_dir}[/dim]")


corpus_app = typer.Typer(no_args_is_help=True)
app.add_typer(corpus_app, name="corpus", help="Set de date și raport la nivelul tuturor municipalităților")


@corpus_app.command("audit")
def corpus_audit(
    data_dir: Path = typer.Argument(Path("data"), exists=True),
    json_out: Path | None = typer.Option(None, help="Raport JSON machine-readable"),
    strict: bool = typer.Option(False, help="Eșuează dacă există artefacte inconsistente"),
    require_modern: bool = typer.Option(
        False, help="Eșuează și pentru bundle-uri legacy fără hash-uri"
    ),
    details: bool = typer.Option(False, help="Afișează toate avertismentele și mesajele"),
):
    """Compară Excel, analysis.json și manifestul; verifică bundle id + SHA-256."""
    from .publication import audit_data, audit_report

    results = audit_data(data_dir)
    report = audit_report(results)
    summary = report["summary"]
    table = Table(title="audit artefacte publice")
    table.add_column("an")
    table.add_column("municipiu")
    table.add_column("stare")
    table.add_column("probleme", overflow="fold")
    for result in results:
        if result.status in {"verified", "not_converted"}:
            continue
        if result.status == "legacy_consistent" and not details:
            continue
        selected_issues = result.issues if details else [
            issue for issue in result.issues if issue.severity == "error"
        ][:1]
        messages = "; ".join(f"{issue.code}: {issue.message}" for issue in selected_issues)
        remaining = len(result.issues) - len(selected_issues)
        if remaining:
            messages += f" (+{remaining} în raportul JSON)"
        style = "red" if result.status == "inconsistent" else "yellow"
        table.add_row(str(result.year or "?"), result.municipality, result.status,
                      messages, style=style)
    console.print(table)
    console.print(
        f"[bold]{summary['trusted']}/{summary['converted']}[/bold] conversii coerente · "
        f"[red]{summary['inconsistent']} inconsistente[/red] · "
        f"{summary['entries'] - summary['converted']} neconvertite · "
        f"stări: {summary['by_status']}"
    )
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        console.print(f"[dim]raport: {json_out}[/dim]")
    not_modern = sum(
        result.status not in {"verified", "not_converted"} for result in results
    )
    if (strict and summary["inconsistent"]) or (require_modern and not_modern):
        raise typer.Exit(1)


@corpus_app.command("migrate-bundles")
def corpus_migrate_bundles(
    data_dir: Path = typer.Argument(Path("data"), exists=True),
    workers: int = typer.Option(4, min=1, max=8),
    limit: int = typer.Option(0, min=0, help="Procesează cel mult N intrări (0 = toate)"),
    dry_run: bool = typer.Option(False, help="Arată planul fără să modifice artefacte"),
    force: bool = typer.Option(
        False, help="Republică și bundle-urile moderne care trec deja auditul"
    ),
    json_out: Path | None = typer.Option(None, help="Raport JSON al migrării"),
):
    """Republică conversiile legacy ca bundle-uri moderne, fără apeluri LLM."""
    import datetime as dt
    import subprocess
    import sys
    import time
    from collections import Counter

    from .manifest import Manifest
    from .publication import audit_city, migration_candidates

    candidates = migration_candidates(data_dir, include_verified=force)
    if limit:
        candidates = candidates[:limit]
    if not candidates:
        console.print("[green]toate conversiile sunt deja bundle-uri moderne verificate[/green]")
        return

    by_year = Counter(candidate.year for candidate in candidates)
    by_preset = Counter(candidate.preset or "neînregistrat" for candidate in candidates)
    console.print(
        f"[bold]{len(candidates)} conversii de migrat[/bold] · "
        f"ani: {dict(sorted(by_year.items()))} · LLM: [bold green]off ($0)[/bold green]"
    )
    console.print(f"[dim]preseturi pentru replay cache: {dict(by_preset)}[/dim]")
    if dry_run:
        for candidate in candidates:
            console.print(
                f"  {candidate.year} · {candidate.municipality} · "
                f"{candidate.previous_artifact_status} · {candidate.preset or 'fără preset'}"
            )
        return

    config = _config()
    started = dt.datetime.now(dt.UTC)
    records = []
    for index, candidate in enumerate(candidates, 1):
        file_started = time.monotonic()
        console.print(
            f"[{index}/{len(candidates)}] [bold]{candidate.year} · "
            f"{candidate.municipality}[/bold] …",
            end=" ",
        )
        if not candidate.pdf.exists():
            records.append({
                "year": candidate.year, "siruta": candidate.siruta,
                "municipality": candidate.municipality, "status": "failed",
                "seconds": 0.0, "error": "source PDF missing",
            })
            console.print("[red]PDF lipsă[/red]")
            continue

        command = [
            sys.executable, "-m", "bgconvertor.cli",
            "--runs-dir", str(config.runs_dir),
            "convert", str(candidate.pdf),
            "--workers", str(workers),
            "--llm", "off",
            "--out", str(candidate.pdf.with_suffix(".xlsx")),
        ]
        if candidate.preset:
            command.extend(["--model-preset", candidate.preset])
        proc = subprocess.run(command, capture_output=True, text=True)
        elapsed = round(time.monotonic() - file_started, 2)
        status = "failed"
        error = None
        bundle_id = None
        if proc.returncode == 0:
            fresh_manifest = Manifest(candidate.manifest_path)
            fresh_city = fresh_manifest.by_pdf(candidate.pdf)
            audit = audit_city(fresh_manifest, fresh_city) if fresh_city else None
            if audit is not None and audit.status == "verified":
                status = "verified"
                bundle_id = (
                    fresh_city.entry.get("conversion") or {}
                ).get("artifacts", {}).get("bundle_id")
            else:
                error = f"post-publication audit: {audit.status if audit else 'entry missing'}"
        else:
            output = (proc.stdout + proc.stderr).strip().splitlines()
            error = output[-1][-500:] if output else f"exit {proc.returncode}"
        records.append({
            "year": candidate.year, "siruta": candidate.siruta,
            "municipality": candidate.municipality, "status": status,
            "seconds": elapsed, "bundle_id": bundle_id, "error": error,
        })
        if status == "verified":
            console.print(f"[green]✓ {elapsed:.1f}s[/green]")
        else:
            console.print(f"[red]✗ {error} ({elapsed:.1f}s)[/red]")

    finished = dt.datetime.now(dt.UTC)
    succeeded = sum(record["status"] == "verified" for record in records)
    failed = len(records) - succeeded
    report = {
        "schema_version": 1,
        "mode": "llm_off",
        "external_api_cost_usd": 0.0,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "seconds": round((finished - started).total_seconds(), 2),
        "summary": {"attempted": len(records), "verified": succeeded, "failed": failed},
        "files": records,
    }
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    console.print(
        f"[bold]{succeeded}/{len(records)}[/bold] bundle-uri moderne verificate · "
        f"{failed} eșuate · {report['seconds'] / 60:.1f} min · $0 API"
    )
    if failed:
        raise typer.Exit(1)


@corpus_app.command("export")
def corpus_export(
    out: Path = typer.Argument(Path("corpus.csv")),
    pdfs: list[Path] | None = typer.Argument(None),
):
    """Un singur set de date normalizat, în format lung, din toate fișierele convertite."""
    from . import corpus

    config = _config()
    files = [p for p in (pdfs or []) if p.exists()] or corpus.discover_pdfs(config, Path.cwd())
    if not files:
        console.print("[red]no converted PDFs found (run `convert` first)[/red]")
        raise typer.Exit(1)
    _stage_banner("Export corpus", f"{len(files)} fisiere -> {out}")
    r = corpus.export(config, files, out)
    for muni, n in r["per_file"].items():
        console.print(f"  {muni}: {n} randuri")
    console.print(f"[bold green]✓ {r['rows']} randuri -> {out}[/bold green]"
                  + (f" + {r['parquet']}" if r["parquet"] else ""))
    console.print("[dim]verified=true = linie fără error/warning/info; metrica nu "
                  "include rândurile sau celulele omise din extracție[/dim]")


@corpus_app.command("cross-check")
def corpus_cross_check(
    old_csv: Path = typer.Argument(..., exists=True, help="Export corpus al ediției vechi"),
    new_csv: Path = typer.Argument(..., exists=True, help="Export corpus al ediției noi"),
    out: Path = typer.Option(Path("cross-check.csv"), help="Lista de candidați pentru re-citire"),
    top: int = typer.Option(10, help="Câte orașe se afișează în tabel"),
):
    """Compară două ediții și clasează liniile care merită re-citite.

    Nu modifică nicio valoare: raportul e o listă de priorități pentru o
    re-citire țintită. Referința fiecărui oraș e propria lui mediană, deci
    creșterile reale de buget nu produc suspecți.
    """
    from . import crossyear

    _stage_banner("Validare încrucișată an-la-an", f"{old_csv.name} vs {new_csv.name}")
    reports = crossyear.compare(old_csv, new_csv)
    if not reports:
        console.print("[yellow]nicio pereche de orașe cu suprapunere suficientă[/yellow]")
        raise typer.Exit(1)
    t = Table(title="candidați pentru re-citire (fără nicio corecție automată)")
    t.add_column("oraș", no_wrap=True)
    for col in ("linii comune", "raport median", "suspecți", "din care cu cifră mutată"):
        t.add_column(col, justify="right")
    t.add_column("observație")
    for rep in reports[:top]:
        shifts = sum(1 for s in rep.suspects if s.signature == "decimal_shift")
        note = ("[yellow]unități diferite între ediții[/yellow]" if rep.unit_shift
                else "")
        t.add_row(rep.city, str(rep.matched), f"{rep.median_ratio:.3g}",
                  str(len(rep.suspects)), str(shifts), note)
    console.print(t)
    n = crossyear.write_csv(reports, out)
    total = sum(len(r.suspects) for r in reports)
    strong = sum(1 for r in reports for s in r.suspects
                 if s.old_verified and not s.new_verified)
    console.print(f"[bold green]✓ {n} candidați -> {out}[/bold green]")
    console.print(f"[dim]{strong} din {total} au perechea veche verificată și cea nouă "
                  "nu — cele mai bune ținte pentru re-citire[/dim]")


@corpus_app.command("aggregate")
def corpus_aggregate(
    data_dir: Path = typer.Option(Path("data"), exists=True, help="Rădăcina data/ cu toți anii"),
    out: Path = typer.Option(Path("corpus.json")),
):
    """Agregatul corpusului (toți anii, toate orașele) într-un singur JSON.

    Doar indicatorii afișați de site (identitate, cronologie, calitate,
    totaluri, top capitole); datele la nivel de linie rămân în `corpus export`.
    """
    from .aggregate import build_aggregate, write_aggregate

    corpus = build_aggregate(data_dir)
    write_aggregate(corpus, out)
    n_years = {y: sum(1 for c in corpus.cities if str(y) in c.years) for y in corpus.years}
    per_year = " · ".join(f"{y}: {n} orase" for y, n in n_years.items())
    console.print(f"[bold green]✓ agregat: {len(corpus.cities)} orase ({per_year}) -> {out}[/bold green]")


@corpus_app.command("analytics")
def corpus_analytics(
    data_dir: Path = typer.Option(Path("data"), exists=True, help="Rădăcina data/ cu toți anii"),
    out_dir: Path = typer.Option(Path("analytics"), help="Directorul pentru JSON, CSV și Excel"),
):
    """Construiește indicatori comparabili și un Excel analitic cu surse explicite.

    Valorile extrase, augmentările (populație/suprafață) și formulele derivate
    rămân straturi distincte. Intrările neeligibile apar în date cu motivul
    excluderii, dar nu intră în clasamente.
    """
    from .analytics import build_from_data, write_outputs

    dataset = build_from_data(data_dir)
    outputs = write_outputs(dataset, out_dir)
    eligible = sum(row.plan_comparison_eligible for row in dataset.rows)
    console.print(
        f"[bold green]✓ analitice: {len(dataset.rows)} municipiu-ani, "
        f"{eligible} eligibile pentru comparații[/bold green]"
    )
    console.print(" · ".join(f"{kind}: {path}" for kind, path in outputs.items()))


@corpus_app.command("report")
def corpus_report(pdfs: list[Path] | None = typer.Argument(None)):
    """Calitate și cost, una lângă alta, pentru fiecare municipalitate convertită."""
    from . import corpus

    config = _config()
    files = [p for p in (pdfs or []) if p.exists()] or corpus.discover_pdfs(config, Path.cwd())
    if not files:
        console.print("[red]no converted PDFs found[/red]")
        raise typer.Exit(1)
    rows = corpus.report(config, files)
    table = Table(title="corpus: calitate si cost pe municipalitate")
    for col in ("municipiu", "pagini", "doc", "linii", "% strict", "erori", "avert.", "apeluri LLM", "cost LLM"):
        table.add_column(col, justify="right")
    for r in rows:
        table.add_row(
            r["municipality"], str(r["pages"]), str(r["documents"]), str(r["lines"]),
            f"{r['pct_clean']}%", str(r["errors"]), str(r["warnings"]),
            str(r["llm_calls"]), f"${r['llm_cost_usd']}",
        )
    console.print(table)


@nom_app.command("build")
def nom_build():
    """Parsează fișierele XLSX de anexe locale în registry.json."""
    config = _config()
    reg = nom.build_registry(config.reference_dir)
    path = nom.save_registry(reg, config.reference_dir)
    console.print(f"registry written to [bold]{path}[/bold]")
    _print_stats(reg)


@nom_app.command("info")
def nom_info():
    """Afișează statisticile și sursele registrului."""
    reg = nom.load_registry(_config().reference_dir)
    console.print(f"generated: {reg.generated_at}")
    for fname, sha in reg.sources.items():
        console.print(f"  source: {fname} ({sha[:12]})")
    _print_stats(reg)


@nom_app.command("update")
def nom_update():
    """Caută anexe mai noi pe mfinante.gov.ro și reconstruiește registrul."""
    config = _config()
    downloaded = nom.update(config.reference_dir)
    if downloaded:
        console.print(f"downloaded: {', '.join(downloaded)} — registry rebuilt")
    else:
        console.print("no new annex files on the MF page")


@nom_app.command("check")
def nom_check(code: str, kind: str | None = typer.Option(None)):
    """Caută un cod în registru (utilitar de dezvoltare)."""
    reg = nom.load_registry(_config().reference_dir)
    for k in [kind] if kind else ["revenue", "expense_functional", "expense_economic"]:
        e = reg.get(k, code)
        if e:
            console.print(f"[green]{e.code}[/green] [{e.kind}/{e.level}/{e.budget}] {e.name}")
            kids = reg.children(k, code)
            if kids:
                console.print(f"  children: {', '.join(kids)}")
            return
    if any(r.code == code for r in reg.rollups):
        console.print(f"[yellow]{code}[/yellow] rollup pseudo-code (not in Anexa 2)")
        return
    console.print(f"[red]{code} not found[/red]")
    raise typer.Exit(1)


def _print_stats(reg: nom.Registry) -> None:
    for key, count in sorted(reg.stats().items()):
        console.print(f"  {key}: {count}")


if __name__ == "__main__":
    app()
