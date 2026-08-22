# Contributing

## Setup

```bash
uv sync
uv run pytest        # must pass, fully offline
uv run bgconvertor eval
```

The LLM layers need `ANTHROPIC_API_KEY` (see `.env.example`) but nothing in
the test suite touches the network: LLM tests replay recorded responses,
and PDF-dependent tests skip when the sample files are absent.

## The two gates

Every change must keep:

1. **`uv run pytest` green** — including `test_ab_stays_fully_clean`: the
   digital reference file (Alba Iulia) validates 100% clean, always. If
   your change breaks it, the change mis-files at least one line.
2. **`uv run bgconvertor eval` not regressing** — golden fixtures in
   `tests/fixtures/golden/` hold hand-verified cell anchors for every
   layout family. Tuning is measured, never eyeballed.

Cache-invalidating changes (anything altering extraction output) must bump
`extract_version` in `config.py` — that is what tells the run store to
remap; without it your change silently doesn't apply to cached pages.

## Adding support for a new municipality's layout

This is the most valuable contribution. Follow
[docs/adding-a-layout.md](docs/adding-a-layout.md); in short: `triage` the
PDF, inspect the failing grids, add a mapper in `src/bgconvertor/layouts/`
(one module + one registration line), commit a golden fixture with
hand-verified anchors, show the eval score.

When filing an issue about a PDF that converts poorly, attach the output of
`bgconvertor triage <pdf>` and one problem page (`bgconvertor inspect`).

## Style

`ruff check` / `ruff format` before committing. Keep the architecture's
contract: extraction emits the payload documented in `eval_harness.py`;
validators emit `Issue`s; nothing ever silently guesses a number — the LLM
repair tier only applies values that make the arithmetic hold, and
everything else stays flagged.
