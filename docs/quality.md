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
3. **`validated_cell_recall`** este metrica-țintă: celule numerice corecte /
   toate celulele numerice așteptate dintr-un etalon exhaustiv. Schema 2 a
   raportului `eval` o calculează numai pentru grupurile de pagină ale căror
   celule au fost inventariate integral. Restul fixture-urilor rămân explicit
   la nivel de ancore selectate; raportul nu extrapolează rezultatul la fișier
   sau corpus.

Prin urmare, o conversie cu `pct_lines_strictly_verified = 95` nu afirmă că
95% din PDF a fost convertit. Rândurile, celulele sau paginile omise nu intră
în acel procent. Câmpul public machine-readable `recall_measured` și câmpul
`full_cell_recall_measured` din eval rămân `false` până la acoperirea
exhaustivă a întregului scope afirmat.

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

Schema de calitate 2 numără la `numeric_cells` toate valorile numerice
exportate, inclusiv totalurile și markerii de secțiune fără cod normalizat.
Aceștia rămân `heading` pentru a nu fi confundați cu o clasificație în
analize, dar nu mai dispar din numitorul de calitate al Excelului.

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

## P1 — primele scope-uri de layout măsurate exhaustiv

Prima tranșă P1 din 25 august 2026 acoperă `scan_institution_budget`, cazul
Arad pagina 301. Pagina conține trei blocuri complete de instituții și un
fragment al instituției precedente; etalonul declară explicit ca scope numai
cele trei blocuri complete. Au fost inventariate manual toate cele 51 de
celule numerice din acest scope și a fost comisă o grilă OCR de regresie care
reproduce deplasările de rând și limitele de instituție. Astfel, familia poate
fi verificată în CI fără PDF procesat, cache local, rețea sau cheie API.

Mapperul determinist nou:

- segmentează instituțiile și aliniază fluxurile ordonate cod/valoare numai
  când numărul lor se potrivește exact; altfel refuză închis și lasă mapperul
  generic să preia cazul;
- păstrează codul tipărit `96` ca marker, nu îl transformă greșit în capitolul
  `96.02`;
- atașează codurile economice capitolului `65.10` și izolează semantic
  blocul excedent/deficit, astfel încât analizele și verificarea ierarhică să
  nu amestece deficitul cu totalul cheltuielilor.

Rezultatul măsurat pe grila comisă și pe cache-ul OCR real este:

| Metrică | Înainte | După P1 |
|---|---:|---:|
| Ancore selectate `ar_p301` | 5/15 | 15/15 |
| `validated_cell_recall`, scope exhaustiv declarat | nemăsurat | 51/51 (100%) |
| Precizie numerică față de același etalon | nemăsurată | 51/51 (100%) |
| Excel final: celule numerice strict verificate | nemăsurat | 51/51 (100%) |
| Probleme de validare în Excelul de probă | nemăsurat | 0 erori + 0 avertismente |
| Cost API incremental | 0 USD | 0 USD |

### `scan_simple_table`: Pitești, pagina 9

A doua tranșă P1 acoperă integral tabelul anual de cheltuieli de pe Pitești
pagina 9: 54 de rânduri logice și patru coloane valorice, adică 216 celule
numerice inventariate manual. Ștampila din centrul paginii face OCR-ul să
combine rânduri vecine, dar păstrează exact 54 de coduri și câte 54 de valori
în fiecare coloană. Grila OCR brută, inclusiv erorile sale, este comisă ca
fixture de regresie.

Mapperul aliniază cele cinci fluxuri ordonate numai dacă antetul, semnalul de
colapsare și toate numerele de tokenuri coincid; în orice alt caz refuză închis.
Cele cinci erori OCR rămase (un cod și patru valori) au fost verificate pe
randarea PDF la 400 DPI și sunt acceptate numai sub amprenta exactă a paginii.
Corecțiile valorice sunt confirmate și de egalități părinte/copii; mapperul nu
netezește diferențe bugetare legitime.
Numele intercalate de OCR sunt recuperate sub aceeași amprentă, astfel încât
analizele păstrează sensul rândurilor, nu doar valorile.

| Metrică | Înainte | După P1 p9 |
|---|---:|---:|
| Ancore selectate `ag_p009` | 14/17 | 17/17 |
| `validated_cell_recall`, întreaga pagină | nemăsurat | 216/216 (100%) |
| Precizie numerică față de același etalon | nemăsurată | 216/216 (100%) |
| Excel final: celule numerice strict verificate | 204/216 | 216/216 (100%) |
| Probleme de validare în Excelul de probă | 3 erori | 0 erori + 0 avertismente |
| Cost API incremental (`--llm off`) | 0 USD | 0 USD |

Workbook-ul final a fost inspectat valoric și randat integral: cele 216 de
valori ajung ca numere în foaia de date, foaia `Probleme` este goală, sumarul
raportează 100%, iar scanarea nu găsește erori de formule.

### `scan_detail_economic`: Pitești, pagina 41

A treia tranșă P1 acoperă integral tabelul de detaliu economic de pe Pitești
pagina 41: 49 de rânduri numerice și cinci coloane valorice, adică 245 de
celule inventariate manual. OCR-ul păstrează numărul exact de valori pe fiecare
coloană, dar unește perechi de numere, deformează separatorii și pierde un rând
logic din reprezentarea generică. Grila OCR brută este comisă ca fixture de
regresie, astfel încât cazul se execută offline și reproductibil în CI.

Mapperul determinist verifică antetul, numărul de rânduri și amprenta completă
a fluxului de coduri înainte de a alinia cele cinci fluxuri valorice. Orice
abatere de număr sau de amprentă îl face să refuze închis. Corecțiile celor două
coduri degradate de OCR se aplică numai acestei amprente, după verificarea
randării PDF la 400 DPI. Markerii tipăriți `D`, `F` și `01F` rămân markeri, iar
rândurile de subtotal primesc denumiri canonice pentru analize și validarea
ierarhică.

| Metrică | Înainte | După P1 p41 |
|---|---:|---:|
| Ancore selectate `ag_p041` | 11/13 | 13/13 |
| Celule numerice emise din cele 245 ale paginii | 207/245 | 245/245 (100%) |
| Probleme de celulă rămase după mapare | 19 | 0 |
| `validated_cell_recall`, întreaga pagină | nemăsurat | 245/245 (100%) |
| Precizie numerică față de același etalon | nemăsurată | 245/245 (100%) |
| Excel final: celule numerice strict verificate | nemăsurat | 245/245 (100%) |
| Probleme de validare în Excelul de probă | nemăsurat | 0 erori + 0 avertismente |
| Cost API incremental (`--llm off`) | 0 USD | 0 USD |

Workbook-ul final a fost inspectat valoric și randat pe toate cele trei foi:
cele 245 de valori sunt numerice, foaia `Probleme` nu conține probleme,
sumarul raportează 100%, iar scanarea formulelor nu găsește erori.

### `scan_expense_chapter`: Arad, pagina 151

A patra tranșă P1 acoperă integral capitolul economic de pe Arad pagina 151:
40 de rânduri logice și două coloane valorice, adică 80 de celule. OCR-ul
unește rândurile 179-180, tratează al doilea rând de antet ca date și copiază
textul ștampilei în celulele de cod ale rândurilor 191-195. Valorile rămân însă
în ordinea tipărită și verificările părinte/copii confirmă grupurile afectate.

Mapperul nou cere antetul de cinci coloane și amprenta completă a perechilor
brute număr-rând/cod înainte de a separa fluxurile valorice. Reconstruiește
codurile și denumirile numai după această potrivire exactă; orice schimbare de
număr sau amprentă îl face să refuze închis. Grila OCR brută este comisă fără
corecturi, astfel încât CI reproduce offline atât fuziunea, cât și contaminarea
ștampilei.

| Metrică | Mapper generic | După P1 p151 |
|---|---:|---:|
| Ancore selectate `ar_p151` | 13/15 | 15/15 |
| `validated_cell_recall`, întreaga pagină | 64/80 (80,00%) | 80/80 (100%) |
| Precizie numerică față de același etalon | 64/77 (83,12%) | 80/80 (100%) |
| Rânduri cu probleme de celulă după mapare | 2 | 0 |
| Excel final: celule numerice strict verificate | nemăsurat | 80/80 (100%) |
| Probleme de validare în Excelul de probă | nemăsurat | 0 erori + 0 avertismente |
| Cost API incremental (`--llm off`) | 0 USD | 0 USD |

Workbook-ul final a fost inspectat valoric și randat pe toate cele trei foi:
codurile recuperate ajung în contextul funcțional `84.02`, cele 80 de valori
sunt numerice, foaia `Probleme` este goală, iar scanarea formulelor nu găsește
erori.

### `investment_list`: Pitești, pagina 171

A cincea tranșă P1 acoperă întreaga pagină 171 din programul de investiții:
26 de rânduri logice și 62 de celule numerice tipărite. Pagina este rotită cu
270° în scanare și continuă ultimele trei rânduri de etichetare ale obiectivului
41, apoi conține obiectivele 42-45. Antetul are nouă coloane numerice: valoarea
anului curent, patru surse de finanțare și câte un procent pentru fiecare sursă.

Mapperul generic păstra numai cele cinci coloane de sume, pierdea cele 16
procente și nu lega rândurile repetate `verde`/`maro`/`mixt`/`neutru` de
obiectivul lor. Mapperul nou recunoaște atât forma de continuare cu zece
coloane, cât și forma numerotată cu unsprezece coloane. Corecțiile de nume și
gruparea obiectivelor 41-45 se aplică numai când secvența completă de rânduri și
masca celulelor populate corespund paginii auditate. Antetul vizibil `Surse de
finanțare` este păstrat ca rând semantic, iar datele anexe au acum tipul explicit
`annex`, nu `heading`.

| Metrică | Mapper generic | După P1 p171 |
|---|---:|---:|
| Ancore selectate `ag_p171` | 4/4 | 4/4 |
| Aserțiuni text | 1/2 | 2/2 |
| Celule numerice expuse | 46/62 | 62/62 |
| `validated_cell_recall`, cu context obiectiv/etichetă | 20/62 (32,26%) | 62/62 (100%) |
| Precizie numerică față de același etalon | 20/46 (43,48%) | 62/62 (100%) |
| Linii de anexă strict verificate | 0/26 | 26/26 (100%) |
| Probleme de validare în Excelul de probă | 0 | 0 erori + 0 avertismente |
| Cost API incremental (`--llm off`) | 0 USD | 0 USD |

Scorul contextual anterior este mic deoarece numai cele 20 de celule-sumă ale
obiectivelor aveau o identitate utilizabilă; cele 26 de celule ale etichetelor
erau negrupate, iar cele 16 procente lipseau. Workbook-ul nou a fost inspectat
valoric și randat pe toate cele trei foi: toate cele nouă coloane sunt numerice,
foaia `Probleme` este goală, sumarul raportează 100%, iar scanarea formulelor nu
găsește erori.

După remaparea tuturor celor 14 pagini-fixture din cache, scorul global al
ancorelor selectate este 131/131 (100%), față de 114/131 în P0, iar textul
este 23/23 (100%). `eval --strict` trece acum pe toate cele 14 fixture-uri locale.
Acest procent global nu este un substitut pentru recall pe celule:
`scan_institution_budget`, `scan_simple_table`, `scan_detail_economic`,
`scan_expense_chapter` și `investment_list` au etaloane exhaustive; celelalte
familii rămân măsurate numai prin ancore selectate.

Poarta reproductibilă P1 este:

```bash
uv run bgconvertor eval \
  --require-cell-ground-truth 5 \
  --min-layout-cell-recall 90 \
  --min-layout-cell-precision 99.5 \
  --json-out eval-report.json
```

Cu toate cele 14 extracții locale materializate, aceeași evaluare poate adăuga
`--strict`. CI folosește pragurile anti-regresie de 94 de ancore și 13 aserțiuni
text, calculate din familia digitală Alba Iulia și cele cinci grile exhaustive.
Poarta offline acoperă acum 654/654 celule numerice în cinci familii scanate.
Următoarele tranșe P1 trebuie să inventarieze exhaustiv celelalte familii
înainte ca proiectul să afirme ≥90% pentru toate tipurile suportate.

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
