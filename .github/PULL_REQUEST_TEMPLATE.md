## Ce schimbă acest PR

<!-- o frază-două: ce și de ce -->

## Cele două porți (obligatorii — vezi CONTRIBUTING.md)

- [ ] `uv run pytest` trece (inclusiv `test_ab_stays_fully_clean`)
- [ ] `uv run bgconvertor eval` nu regresează (lipiți scorul mai jos dacă
      s-a schimbat extracția)

## Dacă schimbă extracția

- [ ] `extract_version` incrementat în `config.py` (altfel modificarea nu
      se aplică paginilor din cache)
- [ ] fixture etalon nou în `tests/fixtures/golden/` pentru layout-uri noi

## Dacă adaugă date în corpus

- [ ] sursa oficială e în `manifest.json` + `SOURCES.md`
- [ ] fișierul sub 100MB (altfel: `download.py`)
