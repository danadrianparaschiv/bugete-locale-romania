# Contractul de calitate și publicare

Obiectivul produsului este conversia **PDF → Excel validat**, urmată de
analize de bază și îmbogățiri care nu suprascriu niciodată datele extrase.
Ținta de produs este minimum 90% recall pe celule numerice pentru fiecare
familie de PDF declarată ca suportată, cu un cost extern de maximum 5 USD per
fișier. Acest document separă ținta de ceea ce poate măsura astăzi pipeline-ul.

## Cele trei noțiuni care nu trebuie confundate

1. **`observed_strict_line_rate`** este procentul liniilor extrase pentru care
   niciun validator nu a emis `error`, `warning` sau `info`. Numitorul conține
   numai liniile deja extrase. Aceasta este metrica din Excel, `analysis.json`
   și manifest.
2. **`selected_anchor_recall`** este procentul ancorelor alese manual din
   fixture-urile golden care au fost regăsite de extracție. Este o poartă de
   regresie pe cazuri cunoscute, nu recall complet pe pagină sau fișier.
3. **`validated_cell_recall`** este metrica-țintă: celule numerice corecte și
   suficient demonstrate / toate celulele numerice așteptate din etalon. Nu
   poate fi calculată până când etaloanele nu inventariază toate rândurile și
   celulele, nu doar ancore selectate.

Prin urmare, o conversie cu `pct_lines_strictly_verified = 95` nu afirmă că
95% din PDF a fost convertit. Rândurile, celulele sau paginile omise nu intră
în acel procent. Câmpul machine-readable `recall_measured` rămâne `false`.

`quality.scope` publică separat:

- `pages_expected` — numărul paginilor PDF-ului;
- `pages_selected` — paginile cerute în rulare;
- `pages_processed` — paginile cu artefact de extracție;
- `complete_pdf` — adevărat numai când toate paginile așteptate au fost
  selectate și procesate.

O rulare parțială poate produce un Excel experimental prin `--out`, dar nu
poate suprascrie artefactele corpusului.

## Semantica `verified`

În exportul lung, `verified=true` înseamnă strict că linia nu are nicio
problemă de validare, indiferent de severitate. Coloanele
`verification_status` și `validation_issues` fac decizia auditabilă.
Analizele implicite folosesc numai aceste linii strict verificate. Problemele
de tip `info` (de exemplu o transcriere fără demonstrație independentă) nu mai
sunt promovate drept verificate.

## Bundle public atomic și auditabil

O conversie completă din arborele `data/` publică împreună:

- `budget_file.xlsx`;
- `analysis.json`;
- blocul `conversion` al intrării din `manifest.json`.

Excelul și analiza sunt scrise întâi în fișiere temporare. Ambele încorporează
același `bundle_id`, derivat determinist din SHA-256-ul PDF-ului și rezultatul
conversiei. Manifestul este commit point-ul și se scrie ultimul, atomic; el
înregistrează SHA-256 și dimensiunea fiecărui artefact, SHA-256-ul sursei,
schema de calitate, scope-ul și costul LLM incremental al rulării
(`llm_cost_scope=current_run_incremental`; costul istoric al ledgerului este
păstrat separat). Dacă scrierea
manifestului eșuează, artefactele anterioare sunt restaurate.

Auditul independent este:

```bash
uv run bgconvertor corpus audit data --json-out artifact-audit.json

# pentru o poartă de release: eșuează la orice neconcordanță
uv run bgconvertor corpus audit data --strict

# după migrarea completă: cere și bundle id + hash-uri, nu doar consistență legacy
uv run bgconvertor corpus audit data --strict --require-modern
```

Auditorul compară metricile din toate cele trei artefacte, bundle ID-urile,
hash-urile, dimensiunile și legătura cu PDF-ul sursă. Agregatul și site-ul nu
mai expun analize sau linkuri Excel pentru o conversie `artifact_mismatch`;
PDF-ul oficial și URL-ul sursei rămân disponibile.

## Baseline-ul de migrare P0

Auditul rulat la 24 august 2026 pe fișierele comise a găsit:

| Stare | Intrări |
|---|---:|
| Conversii legacy coerente în Excel + analiză + manifest | 11 |
| Conversii cu cel puțin o neconcordanță | 58 |
| Intrări neconvertite | 14 |
| Total intrări unice auditate | 83 |

Acesta este un audit de **consistență a artefactelor**, nu un scor de
acuratețe. Cele 58 de conversii nu trebuie „reparate” prin copierea unui
procent între fișiere: Excelul și analiza pot proveni din rezultate diferite.
Migrarea corectă este o conversie completă care regenerează toate artefactele
într-un singur bundle. Până atunci, cifrele istorice agregate din documentele
retrospective sunt informative, nu un baseline de release.

Migrarea rapidă, reluabilă și fără apeluri externe este:

```bash
# inventariază numai conversiile existente care nu sunt încă bundle-uri moderne
uv run bgconvertor corpus migrate-bundles data --dry-run

# regenerează Excel + analysis.json + manifest cu LLM oprit
uv run bgconvertor corpus migrate-bundles data \
  --workers 4 --json-out bundle-migration.json

# poarta finală: toate conversiile existente trebuie să aibă bundle și hash-uri valide
uv run bgconvertor corpus audit data --strict --require-modern
```

Comanda selectează numai intrările deja marcate `converted`; nu încearcă să
convertească intrările în așteptare. Forțează `--llm off`, deci costul API al
migrării este 0 USD. Presetul înregistrat este păstrat numai pentru identitatea
cache-ului și redarea rezultatelor compatibile deja existente. O reluare omite
automat bundle-urile moderne care trec auditul, iar o conversie eșuată nu
înlocuiește ultimul set de artefacte publice.
Opțiunea `--force` republică și bundle-urile moderne verificate atunci când o
corecție de export trebuie propagată întregului corpus.

### Rezultatul migrării din 24 august 2026

Migrarea rapidă a terminat în 335,95 secunde (5,6 minute), cu rezultatul:

| Stare după migrare | Intrări |
|---|---:|
| Bundle-uri moderne verificate | 69 |
| Conversii inconsistente sau legacy | 0 |
| Intrări neconvertite, lăsate intenționat nemodificate | 14 |

Raportul a înregistrat 69/69 încercări reușite, 0 eșecuri și 0 USD cost API.
Poarta `corpus audit data --strict --require-modern` trece pentru toate
conversiile existente.

Verificarea vizuală a Excelului din 25 august a identificat apoi șase etichete
de secțiune care începeau cu `=` și erau interpretate de Excel drept formule.
Exporterul le scrie acum explicit ca text, iar un test de regresie blochează
reapariția erorii. Republicarea forțată a celor 69 de bundle-uri corectate a
durat încă 261,67 secunde (4,4 minute), tot cu 69/69 reușite și 0 USD. Timpul
total al migrării plus corecția descoperită la QA a fost 597,62 secunde, adică
aproximativ 10 minute.

Aceasta rezolvă consistența și proveniența publicării, nu ținta de acuratețe.
Evaluarea locală disponibilă după migrare găsește 114/131 ancore numerice
(87,02%) și 22/23 aserțiuni text (95,65%) în 14/14 fixture-uri. Prin urmare,
`eval --strict` încă nu trece, iar corpusul nu pretinde că a atins 90% recall
pe toate celulele sau toate familiile. Extinderea etaloanelor și corectarea
celor 17 ancore numerice și a aserțiunii text lipsă rămân lucrări de calitate
ulterioare P0.

## Porți propuse pentru ținta de 90%

După extinderea corpusului golden la etaloane exhaustive:

- ≥90% `validated_cell_recall` pentru fiecare familie de PDF suportată;
- ≥99,5% precizie între celulele marcate verificate;
- ≥98% recall al paginilor care conțin tabele bugetare;
- 0 bundle-uri inconsistente;
- 0 USD cost API pentru PDF-urile digitale suportate;
- mediană ≤3 USD și plafon dur ≤5 USD pentru PDF-urile scanate.

Bugetul de 5 USD nu justifică transcrierea LLM a sute de pagini. Calitatea
trebuie să vină în principal din rutare, OCR și mappere deterministe; LLM-ul
este rezervat paginilor și celulelor cu valoare de validare mare. Dacă
plafonul se termină, pipeline-ul publică numai ce poate marca onest și lasă
restul semnalat.
