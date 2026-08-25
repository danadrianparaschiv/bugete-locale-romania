# Cum contribui

## Instalare

```bash
uv sync
uv run pytest        # trebuie să treacă, complet offline
uv run bgconvertor eval  # recall pe ancorele disponibile local
uv run bgconvertor corpus audit data --strict --require-modern \
  --json-out artifact-audit.json
```

Straturile LLM au nevoie de `ANTHROPIC_API_KEY` (vezi `.env.example`), dar
nimic din suita de teste nu atinge rețeaua: testele LLM redau răspunsuri
din cache sau folosesc clienți falși, iar testele care depind de PDF-uri se omit singure când
fișierele-eșantion lipsesc. Poți dezvolta și testa complet fără vreo
cheie API.

## Fluxul pentru contribuitori externi

Modificările de **cod** intră prin fork și pull request:

1. **Fork + clonare parțială.** Corpusul are sute de MB de PDF-uri de
   care nu ai nevoie ca să lucrezi la cod:

   ```bash
   git clone --filter=blob:none https://github.com/<user>/bugete-locale-romania.git
   ```

   Blob-urile se descarcă la checkout, doar cele atinse.

2. **Branch + cele trei porți** (secțiunea următoare) trecute local,
   plus `ruff check` / `ruff format`.

3. **Pull request spre `main`.** CI-ul rulează automat pe PR (ruff,
   pytest pe Python 3.12 și 3.13, evaluarea fixture-urilor digitale) —
   complet offline, deci nu are nevoie de niciun secret al depozitului.
   PR-urile se integrează prin squash merge; folosește prefixele din
   istoric (`extract:`, `assemble:`, `site:`, `corpus:`, `docs:`) în
   titlu. Pentru un layout nou, include scorul `bgconvertor eval` în
   descriere — fixture-ul etalon comis e singurul mod în care
   modificarea poate fi verificată obiectiv.

Pentru **date** (PDF-uri noi, corecturi de surse sau de metadate),
deschide un issue cu URL-ul documentului oficial în loc de PR:
proveniența fiecărui fișier trebuie verificată de mainteneri înainte să
intre în corpus.

## Porțile de calitate

Orice modificare trebuie să păstreze:

1. **`uv run pytest` verde** — inclusiv `test_ab_stays_fully_clean`:
   fișierul digital de referință (Alba Iulia) se validează mereu 100%
   curat. Dacă modificarea ta îl strică, modificarea clasifică greșit cel
   puțin o linie.
2. **`uv run bgconvertor eval` fără regresii** — fixture-urile etalon din
   `tests/fixtures/golden/` conțin ancore de celule verificate manual
   pentru familiile cunoscute. Metrica este `selected_anchor_recall`: nu
   reprezintă recall complet pe celule sau fișiere.
3. **Toate conversiile publice sunt bundle-uri moderne coerente.**
   `bgconvertor corpus audit data --strict --require-modern` compară Excelul,
   `analysis.json` și manifestul, inclusiv ID-urile, hash-urile și costul
   declarat. CI blochează orice PR care introduce o neconcordanță sau revine
   la metadate legacy.

CI-ul aplică automat poarta pentru familia digitală (extrage fișierul de
referință de la zero și cere 30 de ancore numerice + 5 aserțiuni text).
Fixture-urile pentru
scanări depind încă de cache-urile OCR locale din `runs/`; absența lor apare
explicit în raport și nu este interpretată drept succes. Rulează evaluarea
completă înainte de PR când ai aceste artefacte și trece acoperirea
fixture-urilor, nu doar procentul ancorelor, în descriere.

Modificările care invalidează cache-ul (orice schimbă rezultatul
extragerii) trebuie să incrementeze `extract_version` din `config.py` —
asta îi spune magaziei de rulări să remapeze; fără asta modificarea ta pur
și simplu nu se aplică paginilor deja procesate.

## Adăugarea unui format nou de municipiu

Aceasta este cea mai valoroasă contribuție. Urmează
[docs/adding-a-layout.md](docs/adding-a-layout.md); pe scurt: rulează
`triage` pe PDF, inspectează grilele care eșuează, adaugă un maper în
`src/bgconvertor/layouts/` (un modul + o linie de înregistrare), comite un
fixture etalon cu ancore verificate manual, arată scorul de la `eval`.

Când deschizi un issue despre un PDF care se convertește prost, atașează
rezultatul `bgconvertor triage <pdf>` și o pagină problematică
(`bgconvertor inspect`).

## Stil

`ruff check` / `ruff format` înainte de commit. Păstrează contractul
arhitecturii: extragerea emite payload-ul documentat în `eval_harness.py`;
validatoarele emit `Issue`-uri; nimic nu ghicește vreodată o cifră în
tăcere — stratul de reparare LLM aplică doar valori care fac aritmetica să
se închidă, iar tot restul rămâne marcat.
