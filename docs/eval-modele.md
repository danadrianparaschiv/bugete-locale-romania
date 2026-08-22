# Plan de evaluare: compararea preseturilor de modele

Scop: măsurarea efectivă — pe fișiere reale din corpus — a celor 12
preseturi `furnizor:model` (vezi `bgconvertor models`), pe două întrebări:

1. **Valoare per dolar** — la buget egal, cât de mult curăță fiecare model?
2. **Fiabilitate de integrare** — respectă fiecare furnizor schema JSON
   strictă, limitele de rată, formatul de imagine?

Poarta de acceptare aritmetică garantează că niciun model nu poate corupe
datele — comparăm exclusiv *câte* grupuri repară și *cât costă*.

## Orașele selectate (5)

| Oraș | Stare | Profil | De ce |
|---|---|---|---|
| Sfântu Gheorghe | convertit, 92.3% | scanare bună, 1.743 linii | calitate la margine: puține erori rămase, model bun vs slab se vede în ultimele procente |
| Pitești | convertit, 44.7% | scanare grea, 3.198 linii | caz dificil cu referință cunoscută: sonnet-5 a atins 44.7% cu ~$5.35 |
| Suceava | neconvertit | 29 pag., scanare ușoară (triaj ~$0.5) | caz ușor: măsoară dacă modelele ieftine ajung acolo unde ajung cele scumpe |
| Galați | neconvertit | 63 pag., layout-uri mixte (triaj ~$3.75) | caz mediu: mix de reparare + fallback |
| Miercurea Ciuc | neconvertit | 34 pag., layout necunoscut ×5 (triaj ~$4) | caz greu: transcriere integrală de pagină — sarcina cea mai sensibilă la model |

## Designul experimentului

- **Referință gratuită**: fiecare oraș convertit întâi cu `--llm off`
  (determinist; OCR-ul intră în depozitul de rulări și se refolosește la
  toate preseturile — doar etapele LLM diferă între rulări).
- **Matricea**: 5 orașe × 12 preseturi = **60 de rulări**, fiecare cu
  `--llm repair --max-llm-cost 3.00 --model-preset <cheie>` și
  `--out evals/<oraș>/<furnizor>-<model>.xlsx` (ieșirea în afara
  corpusului nu scrie analysis.json — corpus-ul rămâne neatins).
- **Buget egal ($3/rulare)**: măsoară exact ce simte un utilizator.
  Modelele scumpe pot lovi plafonul — asta e o parte legitimă a
  rezultatului, nu un defect al designului.
- **Faza 3 (opțională)**: primele 3 preseturi din clasament, reluate pe
  Miercurea Ciuc cu plafon $10, pentru plafonul de capabilitate
  (reluarea refolosește gratuit apelurile din cache).

## Metrici (din registru + `Sumar calitate`)

Pentru fiecare rulare: `Δ% curat` față de referința `--llm off`, erori
rezolvate, linii recuperate, $ efectiv cheltuit, număr de apeluri, rata
de acceptare a reparațiilor (aplicate/încercate), erori de schemă sau de
API, durată. Clasament principal: **Δ% curat per dolar**; secundar:
Δ% curat absolut.

## Execuție

**Faza 0 — fum (≈$2 total).** Câte o rulare minimală per furnizor
(Suceava, plafon $0.50, preseturile cele mai ieftine:
`openai:gpt-5-mini`, `google:gemini-2.5-flash`, `mistral:mistral-medium-3`,
`qwen:qwen3-vl`, plus `anthropic:claude-haiku-4-5`). Validează: cheile,
ID-urile de model (cele mai noi pot diferi — `gemini-3-pro-preview`,
`qwen3-vl-plus`, `gpt-5.1` sunt de confirmat la prima rulare), calea
compatibilă OpenAI, prețurile din `ledger.py` marcate «verificați».

**Faza 1 — referințe.** `--llm off` pentru cele 3 orașe noi (OCR complet,
~30–60 min pe 4 procese; gratuit).

**Faza 2 — matricea.** Per furnizor secvențial (limitele de rată), între
furnizori în paralel. Ordinea orașelor: Suceava → Sfântu Gheorghe →
Galați → Miercurea Ciuc → Pitești (ieftin spre scump; abandonăm devreme
un preset care eșuează sistematic la integrare).

**Raport.** Un script agregă registrele JSONL + foile `Sumar calitate`
într-un `evals/rezultate.csv` și un tabel comparativ pe orașe și
preseturi (candidat pentru o pagină pe site după evaluare).

## Buget

Cel mai defavorabil caz: 60 × $3 = **$180**. Realist **$60–100** (multe
rulări nu ating plafonul; Suceava ~$0.5, referințele și OCR-ul sunt
gratuite). Faza 0 ≈ $2. Faza 3 opțională ≤ $30.

## Prerechizite

- [ ] chei API: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`,
      `DASHSCOPE_API_KEY` (Anthropic există) — în `.env`
- [ ] `uv sync --extra vendors`
- [x] izolarea ieșirii: `--out` în afara corpusului nu mai scrie
      analysis.json
- [ ] confirmarea prețurilor curente pentru modelele marcate «verificați»
      în `ledger.py` (fable-5, opus-5, gemini-3-pro, qwen3-vl)
- [ ] `evals/` în `.gitignore` (rezultatele intermediare nu intră în git;
      doar `rezultate.csv` final)

## Riscuri

- **ID-uri de model învechite** la furnizorii non-Anthropic → faza 0 le
  prinde cu $0.10; corectarea e o linie în `presets.py`.
- **Limite de rată** (mai ales Gemini/DashScope pe conturi noi) →
  concurență 4 poate necesita reducere la 2; secvențial per furnizor.
- **Prețuri placeholder** prea mari opresc bugetul devreme (niciodată
  târziu) — de corectat înainte de matrice pentru clasament corect.
- **Variabilitate între rulări** — cache-ul face fiecare rulare
  reproductibilă la re-execuție, dar nu între preseturi; clasamentele
  strânse (±1–2 pp) se citesc ca egalitate, nu ca diferență.
