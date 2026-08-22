# Changelog

## v0.1.0 — unreleased

Initial public version.

- Extraction: header-driven digital grid reader (multiple vendor variants,
  per-page wrap-style detection); docling OCR + TableFormer pipeline with
  0/90/180/270° orientation detection (adaptive upright prior); layout
  registry (generic header tables, transposed tables, consolidated-budget
  matrices, headerless continuation pages, combined capitol+economic codes,
  Romanian and US number locales, OCR x-marker normalization).
- Validation: Ordinul 1954/2005 registry (auto-refresh from mfinante.gov.ro),
  code/name checks, row checksums, hierarchy sums, section identities;
  per-institution document splitting.
- LLM tiers (optional, Claude API): full-page fallback for unstructurable
  layouts, sum-repair with arithmetic acceptance proofs, cell recovery;
  hard dollar budget, response cache with free resume, parallel calls,
  Batch API mode, crop-based reads.
- Tooling: `triage` pre-flight with cost/time estimates, resumable per-page
  run store, parallel OCR workers, quality/cost `report`, golden-fixture
  `eval`, `corpus export`/`report` for cross-municipality datasets.
- Corpus: `data/2026/` — 2026 budgets of the 41 county-seat municipalities
  plus Bucharest, SIRUTA-coded with source manifest.
