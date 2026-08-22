# Cum contribui

## Instalare

```bash
uv sync
uv run pytest        # trebuie să treacă, complet offline
uv run bgconvertor eval
```

Straturile LLM au nevoie de `ANTHROPIC_API_KEY` (vezi `.env.example`), dar
nimic din suita de teste nu atinge rețeaua: testele LLM redau răspunsuri
înregistrate, iar testele care depind de PDF-uri se omit singure când
fișierele-eșantion lipsesc.

## Cele două porți

Orice modificare trebuie să păstreze:

1. **`uv run pytest` verde** — inclusiv `test_ab_stays_fully_clean`:
   fișierul digital de referință (Alba Iulia) se validează mereu 100%
   curat. Dacă modificarea ta îl strică, modificarea clasifică greșit cel
   puțin o linie.
2. **`uv run bgconvertor eval` fără regresii** — fixture-urile etalon din
   `tests/fixtures/golden/` conțin ancore de celule verificate manual
   pentru fiecare familie de formate. Reglajele se măsoară, nu se apreciază
   din ochi.

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
