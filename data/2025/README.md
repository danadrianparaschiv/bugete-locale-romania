# 2025 county-seat municipal budgets

This directory is a reproducible local corpus of official 2025 budget PDFs for
Romania's 41 county-seat municipalities plus Bucharest.

## Layout

```text
2025/
  <county-code>-<county-slug>/
    <capital-siruta>-<capital-slug>/
      budget_file.pdf
```

County codes and municipality identifiers are the official SIRUTA values from
the 2025 INS register. Ilfov is a legal exception: its county seat is Bucharest,
so entries 25 and 42 use the same official Bucharest municipal budget while
remaining separate in the 42-code corpus.

## Populate or refresh the corpus

Python 3 and `curl` are required. Poppler's `pdfinfo` is optional but strongly
recommended for page-count validation.

```bash
python3 data/2025/download.py
```

Useful options:

```bash
python3 data/2025/download.py --overwrite
python3 data/2025/download.py --only 08,13,42
python3 data/2025/download.py --jobs 4
```

The downloader creates all 42 directories, fetches official-administration
sources, validates the PDF signature, and writes `verification.json` and
`checksums.sha256`. Downloaded PDFs are ignored by Git; the manifest, source
table, checksums, and verification report keep the corpus reproducible.

See [`SOURCES.md`](SOURCES.md) for the human-readable source table.

## Source policy

Approved initial budgets are preferred. If the initial document was not
available as an official PDF, a labelled approved rectification, official
presentation, or official proposal is used. `not_found` means no suitable
official PDF was located during the audit on 2026-08-23; it does not mean the
municipality failed to adopt a budget.
