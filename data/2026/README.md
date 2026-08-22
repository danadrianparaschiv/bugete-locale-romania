# 2026 county-seat municipal budgets

This directory is a reproducible local corpus of official 2026 budget PDFs for
Romania's 41 county-seat municipalities plus Bucharest.

## Layout

```text
2026/
  <county-code>-<county-slug>/
    <capital-siruta>-<capital-slug>/
      budget_file.pdf
```

County codes and municipality identifiers are the official SIRUTA values from
the 2025 INS register. For example, Brașov is stored at
`08-brasov/40198-brasov/budget_file.pdf`.

Ilfov is a legal exception: its county seat is Bucharest. Therefore entries 25
and 42 both point to Bucharest and use the official Bucharest municipal budget.
They are retained separately so the corpus still contains all 42 county codes.

## Populate or refresh the corpus

Python 3 and `curl` are the only required tools. Poppler's `pdfinfo` is optional
but recommended for page-count validation.

```bash
python3 data/2026/download.py
```

Useful options:

```bash
python3 data/2026/download.py --overwrite
python3 data/2026/download.py --only 08,13,42
python3 data/2026/download.py --jobs 4
```

The downloader creates every directory in `manifest.json`, fetches only direct
official-administration sources, validates the PDF signature, and writes:

- `verification.json` with per-entry status, size, SHA-256, and page count;
- `checksums.sha256` for the PDFs that were found and validated.

The PDFs themselves are intentionally ignored by Git. This keeps the public
repository small and avoids duplicating large public records in repository
history. The manifest and checksums make the local corpus reproducible and
auditable.

A human-readable table of every county, city, and official download link is
available in [`SOURCES.md`](SOURCES.md).

## Source policy

Sources are restricted to official municipal or official local-council domains.
An approved initial budget is preferred. If an authority did not expose that
PDF, an approved rectification, official budget presentation, or official
published proposal may be used and is labelled accordingly in `manifest.json`.

"Not found" means no suitable official PDF was located during the audit on
2026-08-20. It does not mean that the municipality did not adopt a budget.
