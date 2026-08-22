# Design

Distilled from the project's development log (`PLAN.md` holds the full
history, including measurements and dead ends).

## The problem

Romanian local budgets are published as PDF annexes to council decisions:
some born-digital with ruled grids, many scanned — rotated in any of four
orientations, stamped over the numbers, printed by a dozen different
budget-software vendors with incompatible table layouts, in two number
locales. The data inside follows one national standard, though: the
classification of public-finance indicators (Ordinul MFP 1954/2005) and
the arithmetic of budget law.

## Core principle

**Extract with deterministic tools, verify with arithmetic, repair with an
LLM only under proof.**

The classification's redundancy (row checksums, capitol = Σ subcapitole,
grupa = Σ titluri, section identities, composition formulas printed in row
names) means a single misread digit almost always breaks an equation. That
gives high-precision, zero-cost error *detection* — so the expensive,
hallucination-prone step (vision LLM) is demoted to *repair*: it re-reads
only flagged row groups, and a repair is applied **only if the re-read
makes the sums hold**. Cells with no constraint to prove them stay marked
`unverified`. Nothing is ever silently guessed.

An early measured lesson locked this in: telling the model the expected
sum made it rationalize values toward it. Repair prompts are therefore
pure transcription; all arithmetic stays on our side.

## Pipeline

```
profile -> [digital grid | orient -> OCR(docling) -> layout mappers]
        -> assemble (documents, institutions, sections, code semantics)
        -> validate (nomenclator + arithmetic)      -> Excel + dataset
        -> LLM tiers (fallback / sum-repair / cell recovery), re-validate
```

Every stage writes per-page JSON to a **run store** keyed by
`(file, page, stage, config-hash)`: re-runs skip finished pages, and a
config or code-version change invalidates exactly the stages that depend
on it. Expensive stages (OCR) are separated from cheap ones (mapping) so
mapper iteration never re-pays OCR. Failures are per-page artifacts with
tracebacks; a crash on page 37 never loses pages 1–36.

## Layout registry

Grid → lines mapping strategies are pluggable (`layouts/`): transposed
tables (indicators as columns), consolidated-budget matrices (year
sub-rows, printed column-index row as fallback semantics), and the generic
header-driven table mapper (shared vocabulary; positional fallback for
headerless continuation pages, both column orders). Vendors' quirks live
in data and small modules: combined `capitol.economic` codes (with
PDF-truncated prefixes repaired from document context), phantom `.00`
suffixes, OCR `x`-marker zoo, two number locales, per-page name-wrap
styles, per-institution document splitting driven by page headers.

## Verification model

Issues are typed (`V1` code validity … `V7` hygiene) with severities, and
every line carries provenance: page, source (`digital`/`ocr`/`llm`), and a
`verified` flag = passed all checks. The corpus export exposes exactly
this, so downstream analysis can choose its risk level.

## LLM guardrails

One ledger per file records every call (tokens, cost, purpose); a hard
per-run dollar budget aborts LLM passes, never the pipeline; identical
calls replay from a response cache forever (also the offline test
cassettes); calls run in a thread pool; large outputs stream; Batch API
mode halves cost for unattended runs; sum-repair reads crop to the row
group when bounding boxes are available.

## Measured negative results (kept on purpose)

- Copier PDFs' embedded text layers looked usable but scored **worse** than
  re-OCR on validated cleanliness (−8pp on the Bacău A/B) — shipped off by
  default behind `prefer_native_text`.
- A stamp-removal chroma filter didn't move a single golden anchor — the
  OCR already read through the corpus's stamps; shipped off by default.

The eval harness (hand-verified golden anchors per layout family,
`bgconvertor eval`) is what makes such calls cheap: hypotheses get
numbers, not opinions.
