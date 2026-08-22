# Adding support for a new layout

Municipalities buy budget software from different vendors, and every vendor
prints tables differently. This walkthrough is the intended path from "my
city's PDF converts badly" to a merged, measured fix — using the real story
of Bistrița's transposed layout as the example.

## 1. Triage first — never start with a full conversion

```bash
uv run bgconvertor triage path/to/budget_file.pdf
```

Triage profiles every page, pushes ~5 sampled pages through the real
pipeline (the work is cached and reused later), and reports the layout
families it found, OCR quality, and an honest cost estimate. A warning like

```
⚠ layout necunoscut in esantion: scan_table_other
```

means pages exist that no mapper claims — that's your target.

## 2. Look at the actual grids

The OCR stage stores every recognized table as a plain text grid. Inspect
one problem page:

```bash
uv run bgconvertor inspect path/to/budget_file.pdf 2
```

or programmatically:

```python
from bgconvertor.config import RunConfig
from bgconvertor.runstore import RunStore
store = RunStore(RunConfig(), Path("path/to/budget_file.pdf"))
grid = store.get("ocr", 2)["tables_raw"][0]
for row in grid[:12]:
    print(row)
```

For Bistrița this showed something unusual: the *periods* were rows and the
*indicators* were columns —

```
['2029',      '7470,00', '4000,00', ...]
['Trim. IV',  '1.386,00', '833,00', ...]
['TOTAL',     '7.145,00', '3.833,00', ...]
['Cod',       '070202',  '07020201', ...]
['Denumirea indicatorilor', '', 'Impozitul pe terenul...', ...]
```

a transposed table: one column per indicator, with the code row and wrapped
name rows at the bottom.

## 3. Write the mapper

Layout mappers live in `src/bgconvertor/layouts/`, one module per strategy.
A mapper is a function `try_map(grid) -> list[dict] | None` — return `None`
when the grid is not your shape (the registry then tries the next mapper;
the generic header-driven table mapper is the guaranteed last resort).

Emit lines in the extraction contract (documented in `eval_harness.py`):
`raw_code`, normalized `code`, `name`, `section`, `values` (canonical
decimal strings keyed by column: `total`, `trim1..4`, `est2027..29`, …).
Use the helpers in `layouts/common.py` — `mk_line`, `parse_cell` (locale-
and OCR-noise-tolerant), the shared header vocabulary.

Register it in `layouts/__init__.py`:

```python
MAPPERS = [
    transposed.try_map,   # <- one line
    matrix.try_map,
    table.map_grid,
]
```

Detection must be conservative: a mapper that claims grids it doesn't
understand degrades other municipalities. Anchor on a structural signature
(for `transposed`: a `Cod` row plus ≥4 period-label rows), not on
guesswork.

## 4. Commit a golden fixture

Pick one representative page and hand-verify a dozen cells against the
rendered PDF page. Fixtures are JSON in `tests/fixtures/golden/`:

```json
{
  "id": "bistrita_p002",
  "pdf": "...", "page": 2, "layout": "scan_transposed_detail",
  "anchors": [
    {"raw_code": "070202", "column": "total", "value": "7145.00"},
    {"raw_code": "070202", "column": "trim4", "value": "1386.00"}
  ]
}
```

Wherever possible pick anchors the arithmetic confirms (TOTAL = Σ
trimesters; capitol = Σ subcapitole) — then your ground truth is proven,
not just eyeballed. Mark stamp-covered or degraded cells `"hard": true`.

## 5. Gate with eval, then the suite

```bash
uv run bgconvertor eval        # your fixture green, nothing else regressed
uv run pytest                  # includes the ab-stays-100%-clean pin
```

If your change altered mapping output for existing files, bump
`extract_version` in `config.py` (this invalidates the cheap mapping cache,
not the expensive OCR) and re-run `bgconvertor extract` on the corpus files
before judging eval.

## 6. What a PR should contain

- the mapper module + registration line,
- the golden fixture (+ any new classification hints),
- before/after numbers: triage estimate and `% curat` for the target file,
  plus the unchanged eval score for everything else.

That's the whole loop. Bistrița went from 31 extracted lines to 163
(79% verified) through exactly these steps.
