## Ce schimbă acest PR

<!-- o frază-două: ce și de ce -->

## Cele trei porți (obligatorii — vezi CONTRIBUTING.md)

- [ ] `uv run pytest` trece (inclusiv `test_ab_stays_fully_clean`)
- [ ] `uv run bgconvertor eval` nu regresează (lipiți scorul mai jos dacă
      s-a schimbat extracția)
- [ ] layout-urile suportate noi au cel puțin un scope `cell_ground_truth`
      exhaustiv și trec pragurile recall/precizie
- [ ] dacă schimbă ieșiri publice: `bgconvertor corpus audit data --strict
      --require-modern` trece și raportul este atașat

## Dacă schimbă extracția

- [ ] `extract_version` incrementat în `config.py` (altfel modificarea nu
      se aplică paginilor din cache)
- [ ] fixture etalon nou în `tests/fixtures/golden/` pentru layout-uri noi

## Dacă adaugă date în corpus

- [ ] sursa oficială e în `manifest.json` + `SOURCES.md`
- [ ] fișierul sub 100MB (altfel: `download.py`)
- [ ] conversia completă a produs un bundle cu `bundle_id` și SHA-256; nu au
      fost editate manual procentele din Excel/analiză/manifest
