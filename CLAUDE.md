# CLAUDE.md

Corpus public al bugetelor locale (PDF-uri oficiale + date validate) și
**bgconvertor**, instrumentul care îl construiește. Detalii: README.md,
CONTRIBUTING.md, docs/design.md.

## Comenzi uzuale

```bash
uv sync
uv run pytest                      # poarta 1 — vezi capcana runs/ mai jos
uv run bgconvertor eval            # poarta 2 — fără regresii pe fixture-urile golden
uv run bgconvertor triage <pdf>    # ce e fișierul, cât ar costa conversia
uv run bgconvertor convert <pdf>   # offline; --llm repair cere ANTHROPIC_API_KEY
uv run bgconvertor corpus aggregate                # corpus.json (toți anii)
uv run bgconvertor corpus audit data               # Excel + analiză + manifest
uv run bgconvertor site build --out site --base-url /bugete-locale-romania
```

## Git: două benzi

- **Date** (`corpus:`): direct pe `main`, cu `git pull --rebase` înainte de
  push — sesiuni paralele aterizează des pe main. Corecțiile de date se fac
  prin commituri noi, niciodată amend/rebase/force-push: istoria publică
  e proveniența datelor.
- **Cod**: branch scurt + PR cu squash. Motiv: push-ul pe main care atinge
  `data/**`, template-urile sau `site.py` **publică automat site-ul**
  (workflow-ul pages); CI-ul trebuie să ruleze înainte, nu după.
- Ruleset-urile GitHub blochează force-push/ștergere pe main pentru oricine;
  cerința de PR are bypass de admin — deci pentru cod disciplina e a ta,
  nu a serverului.
- Mesaje de commit în română, cu prefixele din istoric:
  `extract:`, `assemble:`, `corpus:`, `site:`, `ci:`, `docs:`.

## Capcane (nu se văd din cod)

- **`extract_version` (config.py)**: orice modificare care schimbă rezultatul
  extragerii TREBUIE să-l incrementeze, altfel paginile deja procesate se
  servesc din cache și modificarea ta pur și simplu nu se aplică — fără
  nicio eroare.
- **Porțile se omit silențios fără `runs/`**: testul-ancoră
  (`test_ab_stays_fully_clean`) și eval-ul citesc cache-uri din `runs/`
  (gitignored). Într-un checkout/worktree proaspăt, pytest e verde dar
  poarta n-a rulat. Materializare ieftină (digital, ~25s, fără OCR/LLM):
  `uv run bgconvertor convert data/2026/01-alba/1017-alba-iulia/budget_file.pdf`
  — apoi `eval --min-anchors 30 --min-text-assertions 5` e exact poarta din CI. Fixture-urile
  scanate cer cache-urile OCR complete: copiază `runs/` din checkout-ul
  principal.
- **`site/` este generat** — nu edita HTML-ul din el; sursa sunt
  `src/bgconvertor/templates/` + `src/bgconvertor/site.py` + agregatul
  (`src/bgconvertor/aggregate.py`).
- **`data/<an>/manifest.json` e registrul de progres și commit point-ul
  bundle-ului public**, scris atât de `batch`, cât și de o conversie completă
  la calea implicită — nu-l edita concurent cu o conversie. Un `convert
  --pages` nu poate publica; cu `--out` diferit rămâne experiment.
- **LLM = bani reali**: `--llm repair/full` apelează API-uri plătite; dă
  întotdeauna `--max-llm-cost` (artefactele publice au plafon dur $5/PDF) și preferă preseturile din
  `bgconvertor models` (batch în masă: `google:gemini-3.6-flash`).
- **Identitatea orașelor e codul SIRUTA de UAT** (stabil între ani), nu
  numele și nu codul de localitate — agregatul leagă anii prin el.
- Fișierele peste 100MB nu sunt în git (limita GitHub); le aduce
  `data/<an>/download.py`, iar manifestul le marchează.
