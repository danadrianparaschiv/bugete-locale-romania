# bgconvertor — Romanian Local Budget PDF → Excel Converter

Analysis and development plan · 2026-08-21

> **Status (end of day, 2026-08-21): Phases 0–4 implemented.** All three PDFs
> convert end to end (`bgconvertor convert <pdf> [--llm repair]`).
> Alba Iulia (digital): 100% validation-clean. Pitești + Arad (scanned):
> extracted via docling with orientation correction, validated against the
> registry, LLM-repaired under a hard budget. Golden-anchor eval: 132/140
> across all 12 fixtures; 97 offline tests. See README.md for commands.
> Notable deviations from the original plan: OCR and mapping split into
> separate cached stages; repair prompts are pure transcription (constraints
> stay on our side — the model rationalized when told the expected sum);
> full-page LLM fallback added for layouts TableFormer cannot structure;
> investment/procurement annexes classified out of nomenclator scope.

## 1. What the three PDFs actually contain

All three files were profiled programmatically (page counts, text layers, metadata, rendered samples).

| File | UAT | Pages | Type | Layout family |
|---|---|---|---|---|
| `budget_file_ab.pdf` | Municipiul Alba Iulia | 70 | **Born-digital** (PDF24), full text layer | "Buget detaliat" — code + rând + TOTAL + credite stingere plăți + Trim I–IV + estimări 2027–2029 |
| `budget_file_ag.pdf` | Municipiul Pitești (Argeș) | 236 | **Scanned**, zero text layer (Ghostscript re-print of a scan) | HCL prose pages, then budget tables (cod indicator + prevederi anuale + estimări), then rotated investment lists |
| `budget_file_ar.pdf` | Municipiul Arad | 333 | **Scanned**, zero text layer (Konica Minolta copier output) | "Buget general" matrix rotated 90° in-image; then per-chapter detail tables; then per-institution (`.10`) budgets incl. individual schools |

**569 of 639 pages have no text layer** — OCR is the dominant problem, not PDF parsing.

Concrete hazards observed in the samples:

1. **In-image rotation** (`ar`): landscape tables scanned into portrait pages. Docling does *not* auto-correct this (open issues #1376, #2343) — a deskew/rotate pre-step is mandatory.
2. **Official stamps overlapping data cells** (`ag` p9: stamp over the 65.02.04 column; `ar` p31, p151). OCR will misread or drop those digits.
3. **Clipped indicator names** at column edges and struck-through/corrected values (`ag`).
4. **Heterogeneous document units inside one PDF**: HCL decision prose, nomenclator budget tables (multiple layout families), investment-objective lists ("Denumire obiectiv / surse de finanțare / verde-maro-mixt-neutru"), per-institution school budgets with vertical side labels. The tool must **segment and classify before extracting**.
5. **Even the digital PDF is treacherous**: pypdf text extraction scrambles column order (row number, code and TOTAL concatenate; numeric columns come out in stream order). Extraction must be coordinate-based (words + x/y positions), never plain text.
6. **Multiple budgets per file**: `ab` = buget local (p1–54) + buget centralizat instituții din venituri proprii (p55–70). Code suffix `.02` vs `.10` distinguishes them; each has SECȚIUNEA TOTAL / FUNCȚIONARE / DEZVOLTARE runs.
7. **Numbers in Romanian format** `1.234.567,89`, unit *mii lei*, `X` markers in some estimate cells (`ar`), negative memo lines (37.02.03), `0,00` fillers everywhere.

## 2. The nomenclator (Ordinul 1954/2005, in force for 2026)

The official, current, machine-readable source is
**https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare** — XLS/XLSX annexes whose filenames embed the amendment date and change URL on every update (scrape the page, don't hardcode URLs). Already downloaded to [reference/nomenclator/](reference/nomenclator/):

- `Anexanr2_08052026.xlsx` — **Clasificația indicatorilor privind bugetele locale**: sheet "venituri bl. 2026" (567 rows, Capitol|Subcap.|Paragraf|Denumire) + sheet "ch. funct. bl. 2026" (172 rows, functional expense codes).
- `AnexanrIec_28052026.xlsx` — **Clasificația economică** (titlu/articol/alineat, ~1048 rows) — one shared classification for all budgets.
- `AnexanrI_29072026.xlsx`, `Cuprins2026.xls` — overall functional classification + table of contents.

Notes that matter for the validator:

- data.gov.ro's copy is **frozen at 2018** — do not use it.
- No maintained open-source digitization exists; parsing the MF XLSX ourselves (trivial with openpyxl) is the right call.
- Code grammar: revenues `cc.02[.ss[.pp]]`; functional expenses capitol/subcapitol/paragraf `65.02.04.01`; economic titlu/articol/alineat `10.01.01`. Suffix `.02` = local budget, `.10` = own-revenue institutions. Rollup pseudo-codes `00.xx`, `49.90` (venituri proprii), `98.02`/`99.02` (excedent/deficit) are **not in Anexa 2** — they exist only in report forms and must be seeded separately.
- Aggregation rules (validator's arithmetic backbone):
  - `TOTAL VENITURI (00.01) = 00.02+00.15+00.16+00.17+45.02+46.02+48.02`; the full 00.xx cascade documented in `reference/` (00.02=00.03+00.12, etc.).
  - `VENITURI PROPRII (49.90) = 00.02 − 11.02 − 37.02 + 00.15`.
  - capitol = Σ subcapitole; subcapitol = Σ paragrafe; economic: titlu = Σ articole = Σ alineate; grupa 01 = Σ titluri 10..65.
  - `TOTAL = SECȚIUNEA FUNCȚIONARE + SECȚIUNEA DEZVOLTARE`; the sections are glued by `37.02.03` (negative, funcționare) = −`37.02.04` (positive, dezvoltare).
  - In the `ab` layout: `TOTAL anual = Trim I + II + III + IV` — a per-row checksum, extremely valuable for OCR error detection.
  - Exceptions: "din care:" memo lines are never summed; `*)` codes appear only in execution; title 85 is negative; 2026-new codes flagged `*`/`**` (42.02.98, 54.02.18, 65.02.06, …) are valid for 2026+ only.

## 3. Extraction stack — findings and decisions

Research summary (docling ecosystem, Aug 2026):

- **Docling classic pipeline** is the right primary extractor: pluggable OCR (default **RapidOCR**, PP-OCR v5/v6 Latin models cover Romanian `ro`; Tesseract `ron` is the alternative — EasyOCR is a 10× slower quality fallback; **ocrmac/Apple Vision does not support Romanian** — avoid), **TableFormer ACCURATE** mode is near-SOTA on dense financial tables (~94% on complex tables), and everything lands in a lossless Pydantic `DoclingDocument` with per-cell row/col indices, spans and bounding boxes, plus `TableItem.export_to_dataframe()`.
- Docling limitations to design around: no in-image rotation correction (pre-rotate ourselves; Tesseract OSD or a cheap projection-profile heuristic), no per-cell OCR confidence (page-level confidence grades only, since v2.34 — use them to route pages), merged/dropped cells on very dense tables (mitigate with `images_scale≈2.0`, `force_full_page_ocr=True`, and test `do_cell_matching=False`).
- Throughput: plan ~3–8 s/page on Apple Silicon CPU for OCR + ACCURATE TableFormer (≈ 45–90 min for the 569 scanned pages; parallelizable per page).
- Alternatives considered and rejected as primary: **marker/surya** (handles rotation and benchmarks well, but Open Rail-M weights restrict commercial use), **unstructured** (weaker table fidelity), **camelot/tabula** (digital-only — though camelot lattice is a good cross-check for `ab`), **Azure/Google Document AI** (best accuracy floor + per-word confidences, but managed/vendor cost — keep as an optional escape hatch), **pure LLM-vision extraction** (best semantic understanding, but unverifiable digit hallucination on dense numeric tables — use as *validator/repair layer*, not primary extractor).

**Core architectural principle: extract with deterministic tools, verify with arithmetic, repair with the LLM.** The nomenclator's redundancy (row checksums, hierarchy sums, section identities) means a single misread digit almost always breaks an equation — giving us high-precision, zero-cost error *detection*, so the LLM only needs to *fix* flagged cells, not read everything.

## 4. LLM: role and model choice

The LLM (Claude API, Python SDK) is used for four narrow jobs, all with vision input (rendered page crops) and Pydantic-validated structured outputs (`client.messages.parse()` / `output_config.format`):

1. **Page/document classification** — label each page: HCL prose / budget table (which layout family, which budget `.02`/`.10`, which section) / investment list / other. Cheap, low-risk.
2. **Targeted cell repair** — for cells flagged by the validator (broken sums, stamp overlap regions, empty cells where OCR failed): send a cropped image + neighbors, ask for the digits. Cross-check the repair by re-running the sum.
3. **Header/name canonicalization** — match OCR'd indicator names (clipped, diacritics-mangled) to official nomenclator entries when the code itself is damaged; fuzzy string match first, LLM only for the ambiguous tail.
4. **Full-page fallback extraction** — for pages where docling's confidence grade is POOR or the table structure is unusable (e.g. worst `ar` pages): whole-page vision extraction into the same Pydantic schema, flagged as LLM-sourced in the quality report.

**Model recommendation** (Claude API pricing, Aug 2026):

| Model | Input/Output $/MTok | Role |
|---|---|---|
| **Claude Sonnet 5** (`claude-sonnet-5`) | $3/$15 (intro **$2/$10 through 2026-08-31**) | Default for repair + fallback extraction — best accuracy/cost on dense numeric vision |
| **Claude Haiku 4.5** (`claude-haiku-4-5`) | $1/$5 | Page classification; optionally first-attempt repair with Sonnet escalation |
| Batch API | **−50%** on any model | All non-interactive passes (the whole pipeline is batch-friendly) |

Cost envelope for this corpus (569 scanned pages, ~2.5K image tokens + ~1.5K output tokens/page): even a **full** dual-pass with Sonnet 5 via Batch API is ≈ $6–12; the intended targeted-repair mode (LLM touches ~20–30% of pages) is ≈ $2–4 per corpus. Model is a CLI flag — nothing hardcoded.

## 5. Proposed architecture

```
bgconvertor/
├── pyproject.toml            # uv-managed; typer CLI entry point
├── reference/nomenclator/    # official XLSX annexes (committed) → parsed cache
├── src/bgconvertor/
│   ├── cli.py                # typer app: convert / validate / nomenclator / report
│   ├── profilepdf.py         # stage 0: text-layer census, orientation, rendering
│   ├── classify.py           # stage 1: page → document unit + layout family
│   ├── extract/
│   │   ├── digital.py        # pdfplumber words+coords → rows (ab-type)
│   │   ├── scanned.py        # pre-rotate → docling (RapidOCR ro, TableFormer ACCURATE)
│   │   └── llm.py            # Claude structured-output repair + fallback extraction
│   ├── model.py              # Pydantic: BudgetDocument / BudgetSection / BudgetLine
│   ├── nomenclator.py        # XLSX → code registry + hierarchy + aggregation rules
│   ├── validate.py           # code checks, name fuzzy-match, sum engine, section identities
│   ├── export.py             # openpyxl: canonical sheets + annotated issues
│   └── report.py             # quality scorecard (per file / page / line)
└── tests/                    # golden pages from all 3 files, unit tests for sums/parsing
```

Pipeline per PDF: `profile → classify → extract (per page, path chosen by class) → normalize → validate → [LLM repair loop, re-validate] → export + report`.

Key data model (`model.py`):

```python
class BudgetLine(BaseModel):
    code: str | None            # normalized dotted form "65.02.04.01"
    raw_code: str | None        # as printed, e.g. "65020401"
    name: str
    row_no: int | None          # "rând"
    kind: Literal["revenue", "expense_functional", "expense_economic", "rollup", "memo"]
    values: dict[str, Decimal]  # column key → value (mii lei), e.g. {"total_2026": ..., "trim1": ...}
    provenance: Provenance      # page, bbox, source: digital|ocr|llm, confidence
    issues: list[Issue]         # populated by validator
```

`Decimal` throughout (never float); Romanian number parser (`1.234,56`, `X`, `-`, blanks) as a single audited function.

### Validation = quality measurement

Every check emits a typed `Issue` with severity; the quality report is an aggregation of these:

- **V1 code validity**: exists in nomenclator (right annex for `.02`/`.10`), or is a known rollup pseudo-code.
- **V2 name concordance**: rapidfuzz score vs official denumire (diacritic-insensitive); low score → LLM canonicalization → still low → warn.
- **V3 row checksums** (`ab` layout): TOTAL = ΣTrim.
- **V4 hierarchy sums**: children Σ = parent, skipping memo/"din care" lines, honoring negative codes (37.02.03, title 85).
- **V5 section identities**: TOTAL = FUNCȚIONARE + DEZVOLTARE per indicator; 37.02.03 = −37.02.04; venituri proprii formula; TOTAL VENITURI cascade.
- **V6 cross-document**: buget local totals vs buget general (ar); HCL headline figures vs table totals (optional, LLM-read from prose).
- **V7 extraction hygiene**: unparseable cells, empty required columns, duplicate codes in a section, page-level docling confidence grade.

Excel output (openpyxl): one workbook per PDF — `Venituri` / `Cheltuieli` sheets per budget & section with canonical columns (code, name, per-column values, source, confidence), a `Probleme` sheet listing every Issue with page/cell reference and severity color, and a `Sumar calitate` sheet (per-page stats, % lines fully validated, sum-check pass rate, LLM intervention count, unresolved flags). Investment lists and per-institution school budgets go to separate clearly-labeled sheets (they are outside the strict nomenclator scope).

## 6. Robustness & debuggability — built in from the first line

These files are heterogeneous enough that experimentation is the normal mode of development. The engineering goal is: **no failed run may cost more than one page of work and zero LLM dollars to diagnose.** Every rule below exists to prevent long failing loops that burn wall-clock time and API tokens.

### 6.1 Page-level work units with a persistent run store

- Everything operates on **one page at a time**; a "run" is just an orchestration over per-page units.
- Every stage writes its output as JSON to a content-addressed store:
  `runs/<pdf-stem>/<stage>/<page>.json`, keyed by `hash(pdf) + page + stage + hash(stage config + prompt version)`.
- Re-running is **always incremental**: completed (pdf, page, stage, config) tuples are skipped. Changing a prompt or a docling option invalidates only the affected stage, not upstream ones.
- `--pages 1-10`, `--pages 9,31,151`, `--sample 12` (stratified by page class) let every experiment run on a slice. Development default is a slice; full runs are explicit.
- **Fail-soft per page**: an exception in page 37 is caught, recorded as a `PageFailure` artifact (traceback + stage + config hash), and the run continues. `--fail-fast` flips this for debugging. A run summary always ends with "N ok / M failed / K cached".

### 6.2 Debug artifacts, not log archaeology

- `--debug` writes, next to each page's JSON: the rendered page PNG, the OCR word boxes overlaid on the image (one cheap matplotlib/PIL render), the reconstructed table grid, and the row-level parse. A misextracted page is diagnosed by *opening two images*, not by re-running with print statements.
- Structured logging (stdlib `logging` + rich handler): `-v` = stage progress per page, `-vv` = per-decision detail (column boundary choices, fuzzy-match scores, sum-check inputs). Logs carry `(pdf, page, stage)` context on every line.
- Every `Issue` and every extracted value carries **provenance** (page, bbox, source, config hash) from day one — this is what makes the debug overlays and the Excel annotations possible without re-computation.
- `bgconvertor inspect <pdf> <page>` — render one page with all artifacts to a folder and open it; the primary dev loop tool.

### 6.3 LLM guardrails: budget, cache, replay

- **Ledger**: every API call appends a JSONL record — purpose, model, page, input/output tokens, cost (from the response `usage` fields), duration. Every run prints its cost; `bgconvertor report` aggregates historic spend.
- **Hard budget**: `--max-llm-cost 2.00` (and `--max-llm-calls`) aborts LLM passes — never the deterministic pipeline — when hit. Default budget is small; raising it is a conscious act.
- **Call cache**: responses cached by `hash(model + prompt version + image bytes + schema)`. Re-running an experiment never re-pays for an identical call. Repair loops are capped (max 2 attempts per cell) and a cell that fails twice becomes `UNRESOLVED`, never a retry storm.
- **`--llm off | repair | full`** with `off` as the development default. The entire pipeline must run and produce output (with more `UNRESOLVED` flags) with the LLM disabled.
- **Recorded cassettes**: raw request/response pairs from real calls are saved as fixtures; tests and offline development replay them. No test ever hits the API.
- Prompts are versioned files, not inline strings — a prompt change is a diffable commit and a cache-key change.

### 6.4 Tests before features, measured not eyeballed

- **Golden fixture set first** (Phase 0.5, below): ~12–15 hand-picked pages covering every layout family and hazard (clean digital, stamp overlap, strikethrough, rotated matrix, school budget, investment list, HCL prose). For each: the page PNG + a hand-verified expected JSON. This is the corpus every experiment is scored against.
- **Pure-function core**: number parser (`1.234,56`, `X`, `-`, blanks, negatives), code normalizer (`65020401` → `65.02.04.01`), sum engine, fuzzy matcher — all side-effect-free, unit-tested exhaustively (hypothesis property tests for the parser: parse∘format = id).
- **`bgconvertor eval`**: runs the pipeline on the golden pages and reports cell-level precision/recall vs expected JSON, per layout family. Tuning docling options or prompts = run eval, compare numbers. No "it looks better".
- **Snapshot tests per stage** on the fixtures, so an upstream change that shifts downstream output is visible in review, not discovered in production.
- Everything runs offline in CI (cassettes + committed fixtures); the only network-touching command is `nomenclator update`.

### 6.5 Config as data

One `RunConfig` (pydantic-settings): docling options, OCR engine/langs, render scale, model names, budgets, prompt versions. Serialized into every run directory and hashed into every cache key — so any artifact can answer "what settings produced you?", and two runs are comparable by diffing their configs.

## 7. Development phases

**Phase 0 — Foundations (1 day)**
uv project, typer skeleton, and the **robustness scaffolding from §6 before any extraction code**: run store + page-level cache, structured logging, `RunConfig`, fail-soft page orchestration, LLM ledger/budget stubs, `inspect` command skeleton. Plus nomenclator ingestion: parse the three XLSX annexes into a cached registry (JSON) with hierarchy + seeded rollup codes + aggregation-rule table; `bgconvertor nomenclator update` re-scrapes the MF page (filenames change on every amendment; server needs browser UA + retries).

**Phase 0.5 — Golden fixtures + eval harness (½–1 day)**
Hand-pick and verify ~12–15 pages across all layout families and hazards; commit page PNGs + expected JSON; build `bgconvertor eval`. Also the pure-function core with its unit/property tests (number parser, code normalizer, sum engine). From here on, every change is scored against the fixtures.

**Phase 1 — Digital path end-to-end (1–2 days)**
`ab` file → coordinate-based extraction (pdfplumber words clustered into rows/columns; ruling lines present, so column x-boundaries are detectable) → model → validator → Excel + report. This exercises the full skeleton with zero OCR noise and produces the first real deliverable. Developed and evaluated on fixture slices (`--pages`), then a full 70-page run.

**Phase 2 — Scanned path (2–4 days)**
Docling integration (RapidOCR `ro`, `force_full_page_ocr`, ACCURATE, `images_scale=2`, MPS accelerator), orientation pre-step, page classification (heuristics + Haiku), `ag` end-to-end. Tune on the stamp/strikethrough fixture pages using `eval` + debug overlays; wire the validator-driven LLM repair loop (crop → Sonnet structured output → re-validate) behind the ledger/budget/cache from Phase 0, recording cassettes as fixtures as real calls happen.

**Phase 3 — Hard cases (2–3 days)**
`ar`: in-image rotation handling, buget-general matrix layout (different column semantics), per-institution `.10` budgets, `X`-marker cells. Full-page LLM fallback for pages docling can't structure. Investment-list extraction to side sheets (best-effort, flagged).

**Phase 4 — Hardening (1–2 days)**
Batch API mode for LLM passes, parallel page processing, CLI UX polish (progress, cumulative cost display), README. (Resumability, caching and `--llm` modes already exist from Phase 0 — this phase only tunes them.)

Total: roughly 8–12 working days to a robust v1.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| OCR digit errors that *don't* break any sum (compensating/isolated cells) | Report honestly: quality score counts "arithmetically confirmed" vs "unverified" lines; optional dual-extraction (docling + LLM) diff on demand |
| Stamps destroy digits beyond repair | Repair prompt sees row context + sum constraints; if still inconsistent → cell flagged `UNRESOLVED`, never silently guessed |
| Layout families beyond the 4 found | Classifier has an `unknown` class → page routed to LLM fallback + warning in report |
| Nomenclator amendments mid-year | `nomenclator update` command + registry stamped with annex dates; report records which version validated the file |
| docling perf on 333-page file | Per-page parallelism, resumable cache; ~1h worst case is acceptable for a batch CLI |
| Experiment loops burning time/tokens | §6 in full: page-slice runs, stage-level cache, LLM call cache + hard cost budget, offline cassettes, eval-scored changes |

## 9. Phase 5 — Corpus scale-out (planned 2026-08-21)

Driver: the second batch of samples (Bistrița, Oradea, Bacău) confirmed wide
variation in quality, format and structure, and the end goal is
**cross-municipality analysis**. Theme: route every file to the cheapest path
that handles it, and make new-format onboarding a contained, measured change.

### Wave 1 — routing & correctness (~3–4 days)

**5.1 `triage` command (pre-flight)** — ~1 day
Profile all pages; orient+OCR a stratified sample (~5 pages); classify layouts.
Report: layout families found, unknown-layout warning, scan-quality grades,
estimated duration + LLM cost, recommended command. Stored as
`runs/<stem>/triage.json`. *Acceptance: triage of the 6 current files matches
known reality; an unseen layout is flagged as unknown, not silently mangled.*

**5.2 Layout registry** — ~1–2 days
Refactor the mapper's accumulated branching into `layouts/`: one module per
family = detector (grid+text → confidence) + mapper (grid → lines) + column
schema + identity hooks; a priority-ordered registry dispatches. Port all ~9
known families with their tests. *Acceptance: golden eval ≥ 132/140 unchanged;
adding a dummy family touches zero shared code.*

**5.3 Digital grid generalization (Oradea)** — ~1–2 days
Grid extractor v2: any N ruled columns, semantics from header words (reuse the
scanned header vocabulary). Oradea fixtures added. Romanian UATs cluster on a
few budget-software vendors, so each digital template likely unlocks many
municipalities at zero OCR cost. *Acceptance: Oradea converts on the digital
path at digital-grade cleanliness; Alba Iulia stays 100%.*

### Wave 2 — scanned speed & quality (~2–3 days)

> **Wave 2 outcomes (measured, 2026-08-21):** 5.4 built and A/B-measured on
> Bacău — 3× faster but −8pp validated cleanliness (copier text layer is
> corrupted); shipped behind `prefer_native_text=False`, image-OCR stays the
> default. 5.5 stamp filter built and measured on the three stamp fixtures —
> no anchor improvement (current OCR already reads through these stamps);
> shipped behind `stamp_filter=False`. 5.6 shipped: per-file measured
> timings drive plan ETAs; adaptive orientation (upright-streak prior with
> periodic full checks) cuts orient cost on upright-heavy files.

**5.4 Native text-layer path for copier PDFs (Bacău)** — ~1 day
When a page has an embedded text layer but no grid, feed docling the PDF page
directly (no full-page OCR) so it uses the copier's text + TableFormer.
A/B-measured on new Bacău fixtures. *Acceptance: ≥ equal anchor accuracy at
≥3× the speed of the render-and-OCR path.*

**5.5 Scan preprocessing: stamp filter + deskew** — ~1 day
Stamps are saturated blue/purple ink over black text: an HSV chroma filter
before OCR should erase them nearly for free; add small-angle deskew.
Config-gated, measured on the stamp-covered "hard" anchors. *Acceptance: hard-
anchor pass rate improves with no regressions elsewhere.*

**5.6 Adaptive orientation + learned ETAs** — ~1 day
Per-file orientation prior (after N consecutive upright pages, spot-check
instead of full 4-rotation OCR; consider rapidocr's angle classifier).
run_stage records real timings to `runs/<stem>/timings.json`; the plan/ETA
uses measured history instead of constants (Bacău exposed both). *Acceptance:
Bacău-class orientation ≥3× faster; ETA within ±30% on reruns.*

### Wave 3 — LLM cost & corpus analysis (~3–4 days)

> **Wave 3 outcomes (2026-08-21):** 5.7 shipped — OCR now stores per-row
> y-extents, repair reads crop to the sum-group's rows (measured live:
> avg input 3,600 → 1,507 tokens), cell recovery routes to Haiku
> (`llm.cell_model`), and `llm.batch=True` submits repair reads via the
> Batch API (−50%) through the same cache+ledger. Crops apply to pages
> OCR'd after this change (older payloads fall back to full page). Name-
> based kind disambiguation fixed the 51.02-class ambiguity (ab pinned at
> 100% by a regression test). 5.8 shipped — `corpus export` (61,599 rows
> across 6 municipalities, 94% arithmetic-verified) and `corpus report`.

**5.7 LLM efficiency: crop repair + Batch API + model routing** — ~2–3 days
Keep per-cell bboxes in the OCR payload (schedule the field change with a
planned re-OCR batch — it invalidates the OCR cache); repair sends row crops
instead of full pages (5–10× cheaper, more accurate). `--llm-batch` submits
fallback/repair sets via the Batch API (−50%) for unattended corpus runs.
Route single-cell reads to Haiku. *Acceptance: Arad-class repair ≤ $2.5 with
the same applied-repair count on an eval slice.*

**5.8 Corpus outputs: consolidated dataset + dashboard** — ~1–2 days
`bgconvertor corpus export`: one normalized long-format dataset (CSV/Parquet)
across all converted files — municipality, document/budget, section, kind,
code, func_code, name, column, value, source (digital/ocr/llm), verified-by-
arithmetic flag, page. `bgconvertor corpus report`: cross-municipality quality
and spend table. *Acceptance: dataset loads in pandas and per-municipality
totals reconcile with each workbook's Sumar sheet.*

Cross-cutting rules: every item lands with golden fixtures + eval gates; cache
-invalidating field changes (5.7 bboxes) are batched to avoid unplanned mass
re-OCR; new sample files feed fixtures for their families as they arrive.

## 10. Phase 6 — Public release preparation (planned 2026-08-22)

Goal: a public repo a stranger can clone, run on their own municipality's
PDF, and extend with a new layout — without reading this session's history.
The repo is not yet under git, so history can be born clean.

### P1 — must happen before the repo goes public (~4–5 days with corpus integration)

**6.1 Git + hygiene (half day)**
`git init`; first commit contains code only. `.gitignore` already covers
`.env`, `runs/`, `.venv`; add `*.xlsx` outputs and `corpus.csv`. The API key
in `.env` never enters history — and gets ROTATED anyway before publishing
(it lived in a session transcript). Add `.env.example`. Decide the public
name (keep `bgconvertor` or rename) before the remote exists.

**6.2 License + data/legal notices (half day)**
- Code: MIT or Apache-2.0 (Apache-2.0 recommended — patent grant, common
  for data tooling).
- `reference/nomenclator/*.xlsx`: official MF publications — keep, with a
  NOTICE citing the source page and the annex's own caveat ("nu reprezintă
  temei legal"), plus `nomenclator update` as the refresh path.
- Budget PDFs and converted XLSX stay IN the repo, in the `data/` corpus
  tree (decision 2026-08-22; the tree, SIRUTA manifest, checksums and
  download.py already exist). Storage reality check: corpus is ~589MB for
  36/42 PDFs; **Sibiu's PDF is 156MB — over GitHub's hard 100MB limit** —
  and Bucharest's 61MB file is committed twice (Ilfov + București).
  Approach: plain git for everything under 100MB (accept a ~0.7GB repo;
  document the heavy clone + `--filter=blob:none` hint); the >100MB
  file(s) are NOT committed — `data/<year>/download.py` + checksums fetch
  them (manifest marks `oversize: true`); dedupe Bucharest to one copy
  with both manifest entries pointing at it. Git LFS is the fallback if
  GitHub complains, but its 1GB/mo free bandwidth dies under public
  clones — revisit only with a budget. At multi-year scale (~3GB+),
  migrate PDFs to Releases/HF datasets and keep only XLSX in-tree.
- `DISCLAIMER.md`: outputs are extractions with a verified-flag, not
  official figures; errors remain possible; check the Probleme sheet.

**6.3 Documentation set (1 day)**
- `README.md` rewrite for strangers: what/why (1 paragraph + a screenshot
  of the plan table & a workbook), quickstart (uv, one command on a sample
  PDF), the three-layer design in 10 lines (deterministic extract →
  arithmetic verify → budget-capped LLM repair), cost expectations table,
  supported layout families, limitations. English primary; note that CLI
  output is Romanian (its users are Romanian).
- `docs/design.md`: distilled from this PLAN (architecture, run store,
  eval methodology, measured decisions incl. the negative results). PLAN.md
  itself moves to `docs/history.md` or gets trimmed — session-log framing
  ("today", "tonight") must go.
- `docs/adding-a-layout.md` — THE extension story: triage a new PDF →
  inspect grids → write the layout module + registration line → add a
  golden fixture → `bgconvertor eval`. Walk it with a real example
  (Bistrița's transposed family).
- `docs/nomenclator.md`: code grammar, rollups, identities, sources.
- `.env.example`, `CONTRIBUTING.md` (dev setup, test/eval gates, the
  "ab stays 100%" rule), `CHANGELOG.md` started at v0.1.0.

**6.4 CI + testing hardening (1 day)**
- GitHub Actions: `uv sync` + `ruff check` + `pytest` on 3.12/3.13. The
  suite is already offline-safe (cassettes; PDF-dependent tests skip when
  files are absent) — CI runs the committed small PDFs' subset, incl. the
  ab-100% pin and eval over the fixtures whose PDFs are committed.
- New CI-friendly tests that need NO PDFs: per-family **grid fixtures**
  (the OCR text grids as JSON — transposed, matrix, headerless, combined-
  code, US-locale) asserting mapper output; assemble/validate unit tests on
  synthetic documents (region switching, truncation repair, kind
  disambiguation); CLI smoke tests via typer's runner.
- `ruff` (lint+format) added to dev deps and pre-commit config; one
  formatting pass over the codebase.

**6.5 Corpus tree integration (1 day) — prerequisite for everything below**
- Fix the run-store key collision: every corpus file is `budget_file.pdf`,
  so `runs/<stem>` must become `runs/<relative-path-slug>` (e.g.
  `runs/2026-01-alba-1017-alba-iulia`). Migration shim for the existing
  flat-file stores; the flat `budget_file_*.pdf` samples move into the
  tree (they ARE county seats: ab=1017-alba-iulia, ar=arad, …) and golden
  fixtures/tests repoint to `data/` paths.
- Manifest becomes the identity source: `corpus export` rows carry
  `siruta`, `county_code`, `city` from `data/<year>/manifest.json` instead
  of filename guessing; converted workbook lands NEXT TO its PDF
  (`budget_file.xlsx`) and is committed.
- `triage`/`convert`/`report` accept a manifest entry (`--city 1017` or a
  tree path) as well as a bare PDF path.

**6.6 Batch runner (1 day)**
`bgconvertor batch data/2026 [--group 5] [--llm repair --max-llm-cost N
per-file] [--only pending|failed]`: walks the manifest, processes N cities
at a time (extraction workers within each; group = commit/checkpoint
granularity), fail-soft per city, resumable via a `conversion_status`
block written back into the manifest (status, pct_clean, errors, spend,
converted_at, tool version). Ends with the corpus report table. Designed
so a GitHub Action or a human can run "the next 5" safely.

**6.7 GitHub Pages site, minimal cut (1–1.5 days)**
`bgconvertor site build` → static `site/` from the manifest + conversion
results: an index page (42 county seats: status badge, % clean, links)
and one page per converted city (headline totals venituri/cheltuieli per
section, top capitole table from VERIFIED rows only, quality scorecard,
provenance/spend, download links to xlsx + source PDF, the DISCLAIMER).
Jinja2 templates, no JS build chain; charts as inline SVG or static PNG.
Publish via GitHub Actions (site rebuild on `data/**` change → Pages
deploy). Per-city analysis depth beyond this (per-capita, year-over-year,
cross-city rankings) is P2.

### P2 — shortly after going public (~2 days, can trail)

- **Docs polish**: layout gallery (one rendered page image per family with
  its mapper named), FAQ (costs, key setup, "my PDF has a new layout").
- **Issue templates**: "new municipality/layout" template that asks for
  `bgconvertor triage` output + a sample page; bug template asking for
  `bgconvertor report`.
- **Packaging**: PyPI publication (`uv build`), pinned lower bounds, and a
  `--version` flag; optional Dockerfile for the OCR toolchain.
- **API-key UX**: first-run message explaining `--llm off` works fully
  offline and what a key adds; document typical spend per file class.
- **Site analysis depth**: per-capita figures (INS population by SIRUTA),
  cross-city comparison pages (spending structure by capitol, rankings),
  year-over-year once a second year lands in `data/`; a county map index.
- **Batch automation**: a scheduled/manual GitHub Action that runs
  `batch --group 5` on runners? Likely NOT worth it (OCR needs ~30-60
  CPU-min/city and LLM repair needs the key as a secret) — document local
  runs as the intended path, Action only for site rebuild + eval CI.

### Explicit non-goals for the first public cut
GUI, hosted service, non-Romanian nomenclatures, Windows CI (document
macOS/Linux; docling works on Windows but untested here).

## 11. Recommended stack summary

Python 3.12+/uv · **typer** (CLI) · **pypdfium2 + pdfplumber** (profiling/digital) · **docling** (scanned: RapidOCR-ro + TableFormer ACCURATE) · **pydantic v2 + pydantic-settings** (schema + RunConfig everywhere) · **anthropic** SDK (Sonnet 5 repair/fallback, Haiku 4.5 classification, Batch API, structured outputs) · **rapidfuzz** (name matching) · **openpyxl** (Excel) · **rich** (terminal report + logging) · **pytest + hypothesis** (unit/property tests, snapshot tests, cassette replay, golden-page eval).
