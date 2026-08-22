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
app.add_typer(nom_app, name="nomenclator", help="Manage the Ordinul 1954/2005 code registry")

console = Console()
_state: dict = {}


@app.callback()
def main(
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="-v progress, -vv detail"),
    runs_dir: Path | None = typer.Option(None, help="Artifact store root (default: ./runs)"),
    fail_fast: bool = typer.Option(False, help="Stop at the first page failure"),
    debug: bool = typer.Option(False, help="Write debug artifacts (page PNGs, overlays)"),
):
    setup_logging(verbose)
    # load .env (ANTHROPIC_API_KEY etc.) so `--llm repair` works out of the box
    env_file = Path(".env")
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
    pages: str | None = typer.Option(None, "--pages", help="e.g. 1-10 or 9,31,151"),
):
    """Run the page-census stage: text layer, geometry, per-page routing info."""
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
    """Render one page to the debug dir and dump every stored artifact for it."""
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
    """Show cache and failure state per stage for a PDF."""
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
                lambda p: digital.extract_page(plumber.pages[p - 1]),
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
        if rerouted and config.prefer_native_text:
            from .extract import scanned as sc

            _stage_banner("Extractie din stratul de text (nativ)",
                          "TableFormer pe textul incorporat; fara OCR, fara orientare")
            run_stage(
                store, "ocr_native", rerouted,
                lambda p: sc.ocr_page_native(pdf, p),
            )
            summary = run_stage(
                store, "extract", rerouted,
                lambda p: sc.map_payload(store.get("ocr_native", p) or {}),
            )
            console.print("native " + summary.line())
        elif rerouted:
            console.print(
                f"[yellow]{len(rerouted)} pages have a text layer but no ruled grid "
                f"(copier PDF) — rerouting through OCR (measured more accurate than "
                f"the embedded text; BGC_PREFER_NATIVE_TEXT=1 flips this)[/yellow]"
            )
            scanned_pages = sorted(set(scanned_pages) | set(rerouted))

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
            ),
        )
        _stage_banner("Mapare tabele", "structura OCR -> linii bugetare (rapid)")
        summary = run_stage(
            store, "extract", scanned_pages,
            lambda p: scanned.map_payload(store.get("ocr", p) or {}),
        )
        console.print("scanned " + summary.line())


@app.command()
def extract(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    pages: str | None = typer.Option(None, "--pages", help="e.g. 1-10 or 9,31,151"),
    workers: int = typer.Option(1, min=1, max=8, help="Parallel OCR worker processes"),
):
    """Run extraction: coordinate-based for digital pages, docling for scans."""
    from pypdf import PdfReader

    config = _config()
    store = RunStore(config, pdf)
    selected = parse_pages(pages, len(PdfReader(pdf).pages))

    if workers > 1 and len(selected) > workers:
        _spawn_extract_workers(store, pdf, selected, workers)
        done = len(store.pages_done("extract"))
        console.print(f"workers finished: {done} pages extracted in store")
        return

    _run_extraction(config, store, pdf, selected)


@app.command()
def triage(pdf: Path = typer.Argument(..., exists=True, readable=True)):
    """Pre-flight: classify the file, estimate cost/time, flag unknown layouts.

    Samples a few pages through the real extraction stack (~1 min for scans);
    the sampled work is cached and reused by the actual conversion."""
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
    """Quality + cost report for a PDF from its run store."""
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
    out: Path | None = typer.Option(None, help="Output .xlsx (default: <pdf>.xlsx)"),
    llm: str | None = typer.Option(None, help="off | repair (default from config)"),
    max_llm_cost: float | None = typer.Option(None, help="Hard USD budget for LLM repair"),
    workers: int = typer.Option(1, min=1, max=8, help="Parallel OCR worker processes"),
):
    """Full pipeline: profile -> extract -> assemble -> validate [-> repair] -> Excel."""
    from pypdf import PdfReader

    from . import export as export_mod
    from . import nomenclator as nom
    from .assemble import assemble
    from .model import ConversionResult
    from .validate import validate as run_validate

    config = _config()
    if llm:
        config.llm.mode = llm
    if max_llm_cost is not None:
        config.llm.max_cost_usd = max_llm_cost
    store = RunStore(config, pdf)
    n_pages = len(PdfReader(pdf).pages)
    selected = parse_pages(pages, n_pages)

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

    ledger = client = None
    if config.llm.mode in ("repair", "full"):
        from collections import Counter

        from . import profilepdf
        from .llm.client import LLMClient
        from .llm.fallback import extract_page_llm, needs_fallback
        from .llm.ledger import Ledger

        # ONE ledger for the whole run: fallback + repair share the budget
        ledger = Ledger(
            path=store.root / "llm_ledger.jsonl",
            max_cost_usd=config.llm.max_cost_usd,
            max_calls=config.llm.max_calls,
        )
        client = LLMClient(config, ledger, store.root / "llm_cache")
        fallback_pages = [
            p for p in selected
            if (pl := store.get("extract", p)) is not None
            and pl.get("layout") != "digital_detail"
            and needs_fallback(pl)
            and store.get("llm_extract", p) is None
        ]
        if fallback_pages:
            col_freq: Counter = Counter()
            for p in selected:
                pl = store.get("extract", p) or {}
                col_freq.update({c for ln in pl.get("lines", []) for c in ln.get("values", {})})
            columns = [c for c, n in col_freq.most_common(6)] or ["buget_2026"]
            est = len(fallback_pages) * 0.13
            _stage_banner(
                "Extractie LLM pagina-intreaga",
                f"{len(fallback_pages)} pagini pe care docling nu le-a putut structura",
            )
            console.print(
                f"  cost estimat: ~${est:.2f} (≈$0.13/pagina) · buget ramas: "
                f"${max(0.0, config.llm.max_cost_usd - ledger.run_cost_usd):.2f} · "
                f"coloane: {', '.join(columns)}"
            )

            def llm_page(p: int):
                img = profilepdf.render_page(pdf, p, scale=config.render_scale)
                rot = (store.get("orient", p) or {}).get("rotation", 0)
                if rot:
                    img = img.rotate(rot, expand=True)
                return extract_page_llm(client, img, columns, p)

            run_stage(
                store, "llm_extract", fallback_pages, llm_page,
                concurrency=config.llm.concurrency,
            )

    _stage_banner("Asamblare + validare", "documente, sectiuni, coduri, sume incrucisate")
    registry = nom.load_registry(config.reference_dir)
    documents = assemble(store, selected, registry)
    if not documents:
        console.print("[red]no documents assembled — nothing to export[/red]")
        raise typer.Exit(1)
    result = ConversionResult(pdf=pdf.name, documents=documents)
    run_validate(result, registry)
    pre = result.stats()
    console.print(
        f"  {pre['documents']} documente, {pre['lines']} linii — inainte de reparare: "
        f"{pre['pct_clean']}% curate, {pre['issues']['error']} erori"
    )

    if config.llm.mode == "repair" and client is not None:
        from . import profilepdf
        from .llm.orchestrate import repair_document, repair_unparseable

        n_sum_groups = sum(
            1 for d in result.documents for ln in d.lines
            if any(i.check == "V4_hierarchy" for i in ln.issues)
        )
        _stage_banner(
            "Reparare LLM",
            f"{n_sum_groups} grupuri cu sume rupte + celule ilizibile; "
            "o corectie se aplica DOAR daca suma re-citita bate",
        )

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

        for doc in result.documents:
            result.issues.extend(
                repair_document(doc, client, page_image, row_locator=row_locator)
            )
        for doc in result.documents:
            result.issues.extend(repair_unparseable(doc, client, page_image))
        console.print(ledger.summary())

    out_path = out or pdf.with_suffix(".xlsx")
    export_mod.export(result, out_path)

    stats = result.stats()
    console.print(f"\n[bold green]✓ scris {out_path}[/bold green]")
    console.print(
        f"{stats['documents']} documente · {stats['lines']} linii de date · "
        f"[bold]{stats['pct_clean']}% complet curate[/bold] · "
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
    # corpus-tree files also get an analysis.json (feeds the static site)
    from .manifest import find_manifest

    if find_manifest(pdf.parent) is not None:
        from .analysis import write_analysis

        apath = write_analysis(result, pdf.with_name("analysis.json"))
        console.print(f"[dim]analysis: {apath}[/dim]")
    console.print(f"[dim]detalii oricand: `bgconvertor report {pdf.name}`[/dim]")


@app.command("eval")
def eval_cmd(
    stage: str = typer.Option("extract", help="Run-store stage to score"),
    fixtures: Path = typer.Option(Path("tests/fixtures/golden"), help="Golden fixtures dir"),
    strict: bool = typer.Option(False, help="Exit 1 unless every anchor matches"),
):
    """Score the extraction stage against the hand-verified golden fixtures."""
    from . import eval_harness

    config = _config()
    results = eval_harness.evaluate_all(config, fixtures, Path.cwd(), stage=stage)

    table = Table(title=f"eval vs golden fixtures (stage: {stage})")
    for col in ("fixture", "layout", "status", "anchors", "hard", "text"):
        table.add_column(col)
    for r in results:
        anchors = f"{r.anchors_matched}/{r.anchors_total}" if r.anchors_total else "-"
        hard = f"{r.hard_matched}/{r.hard_total}" if r.hard_total else "-"
        text = f"{r.text_matched}/{r.text_total}" if r.text_total else "-"
        style = "dim" if r.status == "missing" else (
            "green" if not r.misses else "yellow"
        )
        table.add_row(r.fixture_id, r.layout, r.status, anchors, hard, text, style=style)
    console.print(table)

    for r in results:
        for miss in r.misses:
            console.print(f"  [yellow]{r.fixture_id}[/yellow] {miss}")

    by_layout = eval_harness.summarize_by_layout(results)
    total = sum(a["anchors_total"] for a in by_layout.values())
    matched = sum(a["anchors_matched"] for a in by_layout.values())
    evaluated = [r for r in results if r.status == "evaluated"]
    console.print(
        f"\n[bold]{matched}/{total}[/bold] anchors matched across "
        f"{len(evaluated)}/{len(results)} evaluated fixtures"
    )
    if strict and (matched < total or len(evaluated) < len(results)):
        raise typer.Exit(1)


@app.command()
def batch(
    data_dir: Path = typer.Argument(Path("data/2026"), exists=True),
    group: int = typer.Option(5, min=1, help="Cities per checkpoint group"),
    workers: int = typer.Option(4, min=1, max=8),
    llm: str = typer.Option("repair", help="off | repair"),
    max_llm_cost: float = typer.Option(3.00, help="USD budget per city"),
    only: str = typer.Option("pending", help="pending | failed | all"),
    limit: int = typer.Option(0, help="Stop after N cities (0 = no limit)"),
):
    """Convert the corpus city by city, in groups, writing status to the manifest.

    Each city runs in its own process (fail-soft); the workbook lands next
    to its PDF; the manifest's `conversion` block records status, quality
    and spend. Safe to interrupt and re-run — everything resumes.
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
            console.print(f"  [bold]{city.name}[/bold] ({city.siruta}) …")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr)[-400:]
                manifest.set_status(
                    city, status="failed",
                    error=tail.splitlines()[-1] if tail else f"exit {proc.returncode}",
                    at=dt.datetime.now().isoformat(timespec="seconds"),
                )
                console.print(f"  [red]✗ {city.name} a esuat[/red]")
                continue
            stats = _workbook_stats(out_xlsx)
            manifest.set_status(
                city, status="converted", workbook=out_xlsx.name,
                at=dt.datetime.now().isoformat(timespec="seconds"),
                tool_version="0.1.0", **stats,
            )
            done += 1
            console.print(
                f"  [green]✓ {city.name}[/green]: {stats.get('lines', '?')} linii, "
                f"{stats.get('pct_clean', '?')}% curate"
            )
        console.print(f"[dim]checkpoint: {done}/{len(todo)} convertite — "
                      "moment bun pentru un commit al data/[/dim]")


def _workbook_stats(xlsx: Path) -> dict:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(xlsx, read_only=True)
        rows = {str(r[0]): r[1] for r in wb["Sumar calitate"].iter_rows(values_only=True)
                if r and r[0]}
        wb.close()
        return {
            "lines": rows.get("Linii de date"),
            "pct_clean": rows.get("% curat"),
            "errors": rows.get("Erori"),
            "warnings": rows.get("Avertismente"),
        }
    except Exception:  # noqa: BLE001 - stats are best-effort
        return {}


site_app = typer.Typer(no_args_is_help=True)
app.add_typer(site_app, name="site", help="Static GitHub Pages site for the corpus")


@site_app.command("build")
def site_build(
    data_dir: Path = typer.Option(Path("data/2026"), exists=True),
    out: Path = typer.Option(Path("site")),
    base_url: str = typer.Option("", help="URL prefix when served from a subpath"),
):
    """Render the corpus index + per-city pages from committed files only."""
    from .manifest import Manifest
    from .site import build

    r = build(Manifest(data_dir / "manifest.json"), out, base_url)
    console.print(
        f"[bold green]✓ site: {r['cities']} orase, {r['converted_pages']} pagini "
        f"de analiza -> {r['out']}/[/bold green]"
    )


corpus_app = typer.Typer(no_args_is_help=True)
app.add_typer(corpus_app, name="corpus", help="Cross-municipality dataset and report")


@corpus_app.command("export")
def corpus_export(
    out: Path = typer.Argument(Path("corpus.csv")),
    pdfs: list[Path] | None = typer.Argument(None),
):
    """One normalized long-format dataset across all converted files."""
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
    console.print("[dim]coloana 'verified' = liniile care au trecut toate verificarile "
                  "aritmetice si de nomenclator — stratul sigur pentru analiza[/dim]")


@corpus_app.command("report")
def corpus_report(pdfs: list[Path] | None = typer.Argument(None)):
    """Quality and spend, side by side, for every converted municipality."""
    from . import corpus

    config = _config()
    files = [p for p in (pdfs or []) if p.exists()] or corpus.discover_pdfs(config, Path.cwd())
    if not files:
        console.print("[red]no converted PDFs found[/red]")
        raise typer.Exit(1)
    rows = corpus.report(config, files)
    table = Table(title="corpus: calitate si cost pe municipalitate")
    for col in ("municipiu", "pagini", "doc", "linii", "% curat", "erori", "avert.", "apeluri LLM", "cost LLM"):
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
    """Parse the local annex XLSX files into registry.json."""
    config = _config()
    reg = nom.build_registry(config.reference_dir)
    path = nom.save_registry(reg, config.reference_dir)
    console.print(f"registry written to [bold]{path}[/bold]")
    _print_stats(reg)


@nom_app.command("info")
def nom_info():
    """Show registry stats and sources."""
    reg = nom.load_registry(_config().reference_dir)
    console.print(f"generated: {reg.generated_at}")
    for fname, sha in reg.sources.items():
        console.print(f"  source: {fname} ({sha[:12]})")
    _print_stats(reg)


@nom_app.command("update")
def nom_update():
    """Scrape mfinante.gov.ro for newer annexes and rebuild the registry."""
    config = _config()
    downloaded = nom.update(config.reference_dir)
    if downloaded:
        console.print(f"downloaded: {', '.join(downloaded)} — registry rebuilt")
    else:
        console.print("no new annex files on the MF page")


@nom_app.command("check")
def nom_check(code: str, kind: str | None = typer.Option(None)):
    """Look up one code in the registry (dev helper)."""
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
