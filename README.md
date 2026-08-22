# bgconvertor

Convert Romanian local-government budget PDFs into validated, analysis-ready
Excel and datasets — even when the PDF is a rotated, stamped scan from a
copier.

Every extracted line is checked against the official classification of
public-finance indicators (**Ordinul MFP 1954/2005**, the annexes in force
for the budget year) and against the arithmetic the budget itself must
satisfy (quarterly sums, chapter hierarchies, section identities). What
can't be verified is *flagged, never guessed*.

The repository doubles as an open **corpus**: `data/<year>/` holds the
official budget PDFs of Romania's county-seat municipalities (SIRUTA-coded,
with per-file sources) alongside their converted workbooks.

## How it works — three layers

1. **Deterministic extraction.** Born-digital PDFs are read from their ruled
   grids by coordinates (multiple vendor layouts auto-detected). Scans go
   through orientation detection (0/90/180/270°) and docling's OCR +
   TableFormer, then a registry of layout mappers (transposed tables,
   consolidated-budget matrices, headerless continuation pages, …).
2. **Arithmetic verification.** The nomenclator's redundancy — row
   checksums, capitol = Σ subcapitole, section identities, printed
   composition formulas — turns almost any misread digit into a detectable
   inconsistency, at zero cost.
3. **Budget-capped LLM repair** *(optional)*. Cells that broke the
   arithmetic are re-read from the page image by Claude; a repair is
   accepted **only if it makes the sums hold**. Full-page transcription
   covers layouts OCR cannot structure. A hard dollar budget and a response
   cache govern every call; runs resume free.

## Quickstart

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and ~2GB for OCR
models on first run.

```bash
uv sync

# pre-flight: what is this file, what will it cost?
uv run bgconvertor triage data/2026/01-alba/1017-alba-iulia/budget_file.pdf

# convert (fully offline — no API key needed)
uv run bgconvertor convert data/2026/01-alba/1017-alba-iulia/budget_file.pdf

# with parallel OCR and LLM repair (needs ANTHROPIC_API_KEY, see .env.example)
uv run bgconvertor convert <pdf> --workers 4 --llm repair --max-llm-cost 3.00

# quality/cost report, per-page inspection, golden-fixture evaluation
uv run bgconvertor report <pdf>
uv run bgconvertor inspect <pdf> <page>
uv run bgconvertor eval

# one normalized dataset across all converted files
uv run bgconvertor corpus export corpus.csv
uv run bgconvertor corpus report
```

The output workbook contains data sheets per budget document and section,
a `Probleme` sheet locating every issue (page + code + column), and a
`Sumar calitate` scorecard. CLI progress output is in Romanian (its users
are Romanian); code and docs are in English.

## What to expect

| File type | Typical result | LLM cost |
|---|---|---|
| Born-digital with ruled grid | 94–100% verified lines | $0 |
| Good scan, known layouts | 60–80% verified | $1–4 |
| Hard scan (stamps, rotations, copier OCR) | 55–70% verified | $3–8 |

"Verified" means the line passed every nomenclator and arithmetic check —
the stratum safe to analyze without opening the PDF. Everything else stays
in the output too, flagged with its reason. See [DISCLAIMER.md](DISCLAIMER.md).

## The corpus

`data/2026/` — official budget PDFs of the 41 county-seat municipalities
plus Bucharest, laid out as `<county>-<slug>/<siruta>-<city>/budget_file.pdf`
with a manifest (sources, checksums, status). One file exceeds GitHub's
100MB limit and is fetched by `data/2026/download.py`. Documents are public
administrative records; per-file attribution in the manifest and
[NOTICE](NOTICE).

## Extending to a new layout

Municipalities use different budget-software vendors, and new formats keep
appearing. The pipeline is built for that: run `triage`, inspect the
mis-mapped grids, add a mapper module to `src/bgconvertor/layouts/`, add a
golden fixture, and gate with `bgconvertor eval`. The full walkthrough is
in [docs/adding-a-layout.md](docs/adding-a-layout.md).

## Development

```bash
uv run pytest          # offline test suite (LLM tests replay cassettes)
uv run bgconvertor eval  # cell-level score vs hand-verified golden fixtures
```

Two hard gates for every change: the test suite passes, and the golden-
anchor eval does not regress (the digital reference file must stay 100%
clean — it is pinned by a test). See [CONTRIBUTING.md](CONTRIBUTING.md)
and [docs/design.md](docs/design.md).

## License

Apache-2.0 for the code ([LICENSE](LICENSE)). Included third-party data —
Ministry of Finance classification annexes, municipal budget PDFs, SIRUTA
codes — is credited in [NOTICE](NOTICE).
