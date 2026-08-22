# bgconvertor

CLI tool that converts Romanian local administration budget PDFs to validated
Excel, checking every line against the official nomenclator (Ordinul MFP
1954/2005, classification in force for 2026). See [PLAN.md](PLAN.md) for the
full analysis and roadmap.

## Setup

```bash
uv sync
```

## Commands

```bash
# full pipeline: profile -> extract -> assemble -> validate [-> LLM repair] -> Excel
uv run bgconvertor convert budget_file_ab.pdf
uv run bgconvertor convert budget_file_ag.pdf --llm repair --max-llm-cost 2.00
# -> <pdf>.xlsx: data sheets per document/section + 'Probleme' + 'Sumar calitate'

# extraction only (cached per page); scans run orient -> docling OCR -> mapping
uv run bgconvertor extract budget_file_ab.pdf --pages 1-10
uv run bgconvertor extract budget_file_ar.pdf --workers 4   # parallel OCR processes

# quality + cost report from the run store
uv run bgconvertor report budget_file_ar.pdf

# LLM repair needs Claude API credentials (ANTHROPIC_API_KEY, e.g. via .env);
# without them run with the default --llm off — unresolved cells stay flagged.
# Re-running with a higher --max-llm-cost RESUMES: cached calls replay free.
```

## How the LLM is used (and constrained)

1. **Full-page fallback** — pages docling cannot structure (e.g. per-institution
   school budgets) are transcribed whole-page into the same contract.
2. **Sum repair** — every broken hierarchy sum triggers one re-read of the row
   group (including rows named by the formula printed on the page); the repair
   is applied ONLY if the re-read makes the arithmetic hold.
3. **Merged-cell recovery** — unparseable OCR cells are re-read as plain
   transcription, marked `unverified` (no arithmetic proof exists for them).

One ledger + hard dollar budget per run governs all three; identical calls are
cached forever; nothing is ever guessed — unresolved stays flagged in the
workbook.

```bash
# score the extraction stage against the hand-verified golden fixtures
# (tests/fixtures/golden/*.json — 12 pages covering every layout family)
uv run bgconvertor eval
uv run bgconvertor eval --strict   # exit 1 unless everything matches
```


```bash
# page census: text layer vs scanned, per-page routing info (cached, resumable)
uv run bgconvertor profile budget_file_ab.pdf
uv run bgconvertor profile budget_file_ar.pdf --pages 1-30

# render one page + dump every stored artifact for it (the dev loop tool)
uv run bgconvertor inspect budget_file_ag.pdf 9

# cache / failure state per stage
uv run bgconvertor runs budget_file_ag.pdf

# nomenclator registry (official MF XLSX annexes -> reference/nomenclator/registry.json)
uv run bgconvertor nomenclator build     # parse local annex files
uv run bgconvertor nomenclator info      # stats + sources
uv run bgconvertor nomenclator update    # scrape mfinante.gov.ro for newer annexes
uv run bgconvertor nomenclator check 65.02.04.01
```

Global flags: `-v/-vv` verbosity, `--fail-fast`, `--debug`, `--runs-dir`.

All per-page work is cached in `runs/<pdf-stem>/<stage>/` keyed by a
config hash — re-runs skip completed pages, and changing a setting
invalidates exactly the stages that depend on it.

## Tests

```bash
uv run pytest
```
