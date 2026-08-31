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
`source` este proveniența valorii individuale din rândul lung, iar
`code_source` este completat separat când numai codul a fost recitit de LLM;
astfel un merge mixt nu atribuie întregul rând unui singur extractor.
Analizele implicite folosesc numai aceste linii strict verificate. Problemele
de tip `info` (de exemplu o transcriere fără demonstrație independentă) nu mai
sunt promovate drept verificate.

Totalurile municipale, clasamentul capitolelor și vizualizările publice folosesc
suplimentar numai documentul principal de buget local, identificat prin sufixul
`.02`. Anexele `.10` (activități finanțate integral din venituri proprii) și
listele de investiții rămân în Excel, dar schema de calitate 3 le exclude din
numitorul bugetar și le publică separat ca `annex_lines` și
`annex_numeric_cells`. Ele nu pot deveni totalul sau structura bugetului
întregului municipiu. Dacă bundle-ul
nu conține niciun document `.02`, analiza lasă intenționat aceste câmpuri goale,
iar publicarea în corpus este refuzată. Astfel, o anexă secundară nu mai poate
înlocui accidental bugetul local principal.

Schema de calitate 3 numără la `numeric_cells` toate valorile numerice bugetare
exportate, inclusiv totalurile și markerii de secțiune fără cod normalizat,
dar nu anexele separate. Markerii rămân `heading` pentru a nu fi confundați cu
o clasificație în analize, fără să dispară din numitorul bugetar. Schimbarea de
schemă face comparațiile istorice de procente insuficiente de unele singure;
campaniile publică și numărul absolut de celule strict verificate.

## Snapshot auditat al ediției 2024

<!-- BEGIN GENERATED:2024_QUALITY_METRICS -->
Conversia și campania de calitate finalizate la 28 august 2026 acoperă toate cele 41 de intrări cu
sursă oficială disponibilă din manifestul 2024, cu toate scope-urile procesate
complet și 2,9424 USD cost API real. A produs 66.299 de linii, dintre care 48.783 strict
verificate, și 228.626 de celule numerice, dintre care 176.956 strict
verificate. Mediana ratei stricte pe intrare este 81,6%; 13/41 intrări sunt la
cel puțin 90%, 28/41 la cel puțin 70%, iar 13 rămân sub 70%.

Schema de calitate 3 elimină din numitor anexele și listele de investiții, pe
care le raportează separat. Recuperarea paginilor anterior omise poate mări
numitorul și micșora procentul chiar când apar mai multe celule corecte; de
aceea matricea publică urmărește și numărul absolut de celule strict verificate.
Pilotul P2 a acceptat 3 fișiere și a adăugat
1.515 astfel de celule pentru
2,213 USD, sub plafonul de 3 USD/fișier și
bugetul experimental de 20 USD.

Toate bundle-urile publică `recall_measured=false`, iar cifrele nu
pot fi prezentate drept recall complet. Pe partea analitică, ediția produce
40 de pagini municipale, 27 de municipiu-ani eligibili pentru comparația
planului, 38 de tabele de capitole și 11 blocuri complete de grafice. Graficele
rămase sunt retrase când capitolele strict verificate nu acoperă 90–110% din
totalul tipărit, în loc să fie completate prin estimare.

Auditul final `corpus audit data --strict --require-modern` trece pentru
110/110 conversii existente din edițiile 2024–2026 și găsește zero bundle-uri
inconsistente. Achiziția și rezultatele detaliate sunt documentate în
[`data/2024/README.md`](../data/2024/README.md).
<!-- END GENERATED:2024_QUALITY_METRICS -->

### Option E — pilotul exhaustiv Buzău

Etalonul source-only pentru PDF-ul Buzău 2024 inventariază toate cele 46 de
pagini bugetare și 8.236 de celule numerice. Citirea combinată a fost
confruntată cu anexele oficiale de funcționare/dezvoltare, două randări OCR
locale pe coordonate și review vizual pentru cele 43 de celule reziduale.
Acesta este primul rezultat complete-file măsurat pentru layout-ul anual
InfoSoft Buzău; nu este extrapolat la celelalte intrări 2024.

| Candidat Buzău | Celule corecte | Celule emise | Recall | Precizie | Recall pagini bugetare | Cost API conversie |
|---|---:|---:|---:|---:|---:|---:|
| Mapper anterior | 2.734 | 3.284 | 33,20% | 83,25% | 97,83% | 0 USD |
| Mapper determinist InfoSoft | 7.852 | 8.234 | 95,34% | 95,36% | 100% | 0 USD |
| + reparare țintită Gemini | 7.858 | 8.204 | 95,41% | 95,78% | 100% | 0,4379 USD |

Mapperul folosește OCR local la coordonate fixe numai după o poartă de sursă
specifică (`buget local detaliat` + `Buzău`), păstrează continuările fără antet
și recunoaște trimestrele II–IV. Prima construire a cache-ului de coordonate a
durat aproximativ 2,5 minute; reluările sunt gratuite și cache-uite.

Repararea LLM a adăugat numai șase celule corecte și a eliminat 30 de predicții
greșite. Câștigul de 0,07 puncte procentuale recall pentru 0,4379 USD nu
justifică o escaladare suplimentară pe acest fișier. Costul total al evidence-ului
de adnotare (drafturi vision și probe) plus conversia pilot a fost aproximativ
4,30 USD din bugetul experimental separat de 20 USD; costul public per conversie
rămâne sub plafonul de 3 USD al pilotului.

Cele 42 de pagini cu diferențe față de converter rămân marcate pentru o a doua
revizie independentă înainte ca etalonul să poată deveni release gate public.
Scorul de mai sus este diagnostic pe adevărul exhaustiv înghețat, nu o afirmație
că întregul corpus 2024 este deja măsurat.

## Bundle public atomic și auditabil

O conversie completă din arborele `data/` publică împreună:

- `budget_file.xlsx`;
- `analysis.json`;
- blocul `conversion` al intrării din `manifest.json`.

Excelul și analiza sunt scrise întâi în fișiere temporare. Ambele încorporează
același `bundle_id`, derivat determinist din SHA-256-ul sursei și rezultatul
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
hash-urile, dimensiunile și legătura cu sursa PDF/XLS/XLSX. Agregatul și site-ul nu
mai expun analize sau linkuri Excel pentru o conversie `artifact_mismatch`;
sursa oficială și URL-ul ei rămân disponibile.

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

### `scan_revenue_detail`: Arad, pagina 31

A șasea tranșă P1 acoperă integral pagina numerotată de venituri Arad p31:
22 de rânduri, 73 de celule numerice și 15 markeri tipăriți `X`. Ștampila
albastră traversează estimările rândurilor 515-519. Mapperul generic păstra
majoritatea numerelor, dar numai 6/15 markeri `X`, pierdea zero-ul 2027 al
rândului 519, nu emitea niciunul dintre numerele de rând și combina denumirile
rândurilor 518-519.

Mapperul nou recunoaște forma generală cu șapte coloane și păstrează `Nr.
crt.`. Reparația celulelor și denumirilor de pe p31 se activează numai când
secvența completă număr-rând/denumire/cod și blocul OCR contaminat de ștampilă
corespund grilei auditate. Dacă amprenta se schimbă, mapperul păstrează
valorile citibile și problemele explicite, fără a transfera corecții din această
pagină. Grila comisă este identică payload-ului OCR din cache.

| Metrică | Mapper generic | După P1 p31 |
|---|---:|---:|
| Ancore selectate `ar_p031` | 20/29 | 29/29 |
| Ancore hard (celulă ștampilată sau marker) | 7/16 | 16/16 |
| `validated_cell_recall`, celule numerice | 72/73 (98,63%) | 73/73 (100%) |
| Precizie numerică față de același etalon | 72/72 (100%) | 73/73 (100%) |
| Markeri `X` tipăriți păstrați | 6/15 (40,00%) | 15/15 (100%) |
| Numere de rând păstrate | 0/22 | 22/22 |
| Probleme de validare în Excelul de probă | 4 erori + 1 avertisment | 0 erori + 0 avertismente |
| Cost API incremental (`--llm off`) | 0 USD | 0 USD |

Markerii `X` nu sunt valori numerice și de aceea nu intră în
`validated_cell_recall`; fixture-ul îi inventariază separat prin 15 ancore hard.
Workbook-ul a fost verificat valoric și randat pe toate foile: cele 73 de
numere și cele 15 marcaje ajung în coloanele corecte, rândurile Excel păstrează
numerele 499-520, foaia `Probleme` este goală, iar scanarea formulelor nu
găsește erori.

### Tranșa finală P1: digital, matrice anuală și tabel transpus

Cele trei familii numerice rămase din suita golden au primit scope-uri
exhaustive și regresii offline:

- `digital_detail`, Alba Iulia pagina 1: 20 de rânduri × 9 coloane, adică
  180/180 celule regăsite și 180/180 precizie. Mapperul digital existent era
  deja corect; schimbarea este etalonul complet care dovedește rezultatul.
- `scan_general_matrix`, Arad pagina 1: 29 de rânduri-an × 8 coloane, adică
  232 de celule. OCR-ul unea valoarea 2029 a indicatorului 04 cu antetul
  indicatorului 05 și împingea ultima cifră pe rândul de cod. Mapperul generic
  regăsea 224/232; recuperarea conservatoare, activată numai de această
  semnătură structurală, obține 232/232 recall și precizie.
- `scan_transposed_detail`, Bistrița pagina 2: 24 de indicatori × 9 coloane,
  adică 216 celule. TableFormer unea trei perechi de coloane-indicator într-o
  grilă transpusă; reparația auditată este legată de amprenta exactă a grilei
  și refuză închis dacă sursa diferă. Rezultatul este 216/216 recall și
  216/216 precizie, iar toate totalurile se reconciliază cu cele patru
  trimestre.

### Familia Cluj `scan_annual_total`

Scanarea mare Cluj folosește predominant forma cu patru coloane `denumire |
cod rând | indicator bugetar | total anual`; TableFormer unește frecvent cele
două antete centrale, iar mapperul generic confunda numărul de rând cu codul
bugetar. Mapperul dedicat păstrează cele două identități separat, acceptă
varianta cu denumire duplicată pe cinci coloane și moștenește schema pe
paginile consecutive cu antet lipsă ori deteriorat. Celulele cu litere OCR
care sunt cifre neechivoce sunt reparate numai în coloane deja numerice.

Fixture-ul public `cj_p097` inventariază exhaustiv 43/43 celule, inclusiv două
cazuri hard verificate pe randarea PDF; recall-ul și precizia sunt 100%, la 0
USD API. Separat, un audit local al celor 753 de grile OCR Cluj a găsit 607
pagini din această familie și a demonstrat că mapperul păstrează 20.394/20.394
celule deja parseabile din OCR. Ultima cifră este un audit de pierdere la
mapare, nu ground truth vizual pentru întregul PDF și nu se prezintă drept
`validated_cell_recall` de fișier.

P1 este astfel complet pentru cele zece familii numerice reprezentate în
suita golden: `digital_detail`, `scan_general_matrix`,
`scan_transposed_detail`, `scan_institution_budget`, `scan_simple_table`,
`scan_detail_economic`, `scan_expense_chapter`, `investment_list` și
`scan_revenue_detail`, plus `scan_annual_total`. Împreună, cele zece scope-uri
conțin 1.398/1.398 celule regăsite și 1.398/1.398 celule corecte (100% recall și
precizie în scope-urile declarate). Pagina `hcl_prose` rămâne intenționat text,
fără metrică numerică.

Acesta este un rezultat pe familii și pagini reprezentative, nu o măsurare a
fiecărui rând din toate PDF-urile corpusului. `recall_measured=false` rămâne
corect pentru conversiile de fișier până când un PDF complet are inventar
exhaustiv.

Poarta reproductibilă P1 este:

```bash
# CI materializează mai întâi scope-ul digital Alba (0 USD API)
uv run bgconvertor convert data/2026/01-alba/1017-alba-iulia/budget_file.pdf
uv run bgconvertor eval \
  --require-cell-ground-truth 10 \
  --min-layout-cell-recall 90 \
  --min-layout-cell-precision 99.5 \
  --json-out eval-report.json
```

Poarta CI complet offline materializează Alba și evaluează minimum 148/148
ancore, 19/19 aserțiuni text și toate cele 1.398 de celule din cele zece
grile/scope-uri exhaustive. Nouă scope-uri scanate (1.218 celule) rulează
direct din grilele sanitizate comise; al zecelea adaugă cele 180 de celule ale
PDF-ului digital Alba. Cei 15 markeri `X` ai Arad p31 sunt blocați separat prin
ancore hard, fiindcă nu sunt numere. Pentru fixture-urile care declară
`source_grid`, grila comisă este sursa autoritară a evaluării: un cache local
creat de o conversie anterioară nu poate modifica scorul offline.

### Refresh-ul complet al corpusului după P1

La 26 august 2026, toate cele 69 de conversii publice au fost auditate după
remaparea cu versiunea de extracție 45. Prima trecere paralelă, care a
reconstruit o singură dată cache-urile OCR invalidate de noua selecție de
preprocesare, a durat 549,6 minute și a publicat 62/69 bundle-uri la 0 USD API.
Șase dintre cele șapte cazuri păstrate de gardul atomic au fost apoi închise
serial: trei căderi `recursive_mutex` ale runtime-ului OCR pe macOS, două
fișiere cache legacy parțiale și un cache Târgoviște legat de PDF-ul anterior.
Citirea cache-ului tratează acum JSON-ul parțial drept miss, iar toate scrierile
per pagină sunt publicate atomic; regresiile sunt acoperite offline.

Poarta finală a raportat:

| Stare după refresh P1 | Intrări |
|---|---:|
| Conversii publice cu bundle modern coerent | 69/69 |
| Conversii publice inconsistente | 0 |
| Intrări intenționat neconvertite | 14 |
| Cost API al refresh-ului și retry-urilor | 0 USD |

O excepție de **sursă**, nu de mapper, rămâne declarată explicit: PDF-ul comis
pentru Brașov 2025 este anexa instituțiilor finanțate din venituri proprii
(`.10`), nu bugetul local principal (`.02`). Gardul de publicare a refuzat să
îl republice drept buget principal; bundle-ul modern anterior a rămas intact
și trece auditul de coerență a artefactelor. Înlocuirea lui necesită mai întâi
o sursă oficială `.02` verificată. Cifra 69/69 de mai sus dovedește acordul
PDF/Excel/analiză/manifest, nu corectează această limitare semantică a sursei.

## P2 — recuperare LLM conștientă de buget

P2 nu mărește plafonul public de 5 USD/PDF. Schimbă ordinea în care acel buget
este consumat și întărește autoritatea registrului de cost:

- un planner determinist ordonează candidații după câștigul de calitate
  estimat per dolar rezervat; planificarea este globală pe fișier, nu reluată
  independent pentru fiecare document asamblat;
- transcrierea paginii întregi cere toate coloanele cunoscute ale layoutului
  (maximum 12), deduse din antetul și contextul paginii, nu un top global de
  șase care putea omite trimestrele; paginile dense sunt împărțite în benzi de
  maximum 32 de rânduri și dimensionează adaptiv limita de output;
- o pagină cu tabel detectat și zero linii mapate rămâne eligibilă chiar dacă
  OCR-ul nu a găsit tokeni numerici; beneficiul mic o ține la sfârșitul
  plannerului, dar nu o mai face invizibilă;
- output-ul LLM completează rânduri și celule lipsă fără a înlocui pagina:
  valorile deterministe câștigă conflictele, iar proveniența este păstrată
  separat pentru fiecare celulă;
- același planner compară ierarhii, checksum-uri trimestriale, identități
  globale și între secțiuni, coduri OCR invalide, duplicate conflictuale și
  celule ilizibile. Codurile se acceptă numai dacă trec nomenclatorul,
  concordanța numelui și gardul anti-coliziune; duplicatele se elimină numai
  după două citiri independente identice;
- orice reparație aritmetică cere o citire independentă completă pentru
  fiecare rând și coloană care participă la egalitate, inclusiv termenii unei
  formule omiși de OCR. Nicio valoare OCR veche și nicio absență tratată drept
  zero nu pot participa la acceptare;
- după toate mutațiile, validatoarele V1–V5 și duplicatele V7 sunt reconstruite
  din documentul rezultat; o reparație locală nu poate ascunde o ierarhie sau
  identitate nou ruptă;
- presetul mixt rulează modelul economic primul. Un model premium este
  rezervat worst-case de planner, dar este apelat numai dacă citirea ieftină
  eșuează verificarea și beneficiul estimat depășește pragul configurat;
- recitirile celulelor fără demonstrație aritmetică primesc o pondere redusă
  și rămân `unverified`;
- fiecare apel, fiecare retry și fiecare element Batch rezervă înainte costul
  worst-case și un slot de apel. Rezervările concurente, retry-urile mai mari
  și Batch nu pot trece împreună peste plafon; redările din cache nu consumă
  nici bani, nici sloturi API;
- `runs/<fișier>/llm_plan.json` păstrează planul pe benzi și planul comun de
  reparare țintită, cu numărul worst-case de apeluri (inclusiv escaladarea) și
  candidații amânați. Ledgerul rămâne autoritatea dură chiar dacă o estimare a
  plannerului este imperfectă.

### Închiderea gap-urilor P2

La 26 august 2026, toate gap-urile funcționale din planul P2 au fixture-uri
offline și sunt conectate la calea reală `convert --llm repair`. Suita are 303
de teste, inclusiv regresii pentru pagina cu zero linii, coloane locale,
segmentarea tabelului dens, merge la nivel de celulă, citiri aritmetice
incomplete, trimestre, identități, coduri, duplicate și escaladarea
cheap-first. Această închidere este o verificare de comportament și siguranță;
nu declară un câștig nou de recall în corpus până la o viitoare rulare
măsurată și nu a consumat API pentru teste.

Experimentul P2 din 25 august 2026 a avut un plafon separat autorizat de
20 USD, dar nu a produs apeluri externe noi: cost incremental 0 USD. Pe
Miercurea Ciuc, paginile 8–12, trei răspunsuri deja aflate în cache au ridicat
`observed_strict_line_rate` de la 95,3% la 98,1% și au redus erorile de la 14
la 4. Plannerul a selectat patru grupuri (84 unități de beneficiu, aproximativ
0,129 USD worst-case); grupul necached de pe pagina 12 a rămas explicit
nerezolvat când endpointul extern nu a fost disponibil. Acesta este un test
de integrare/cost, nu o dovadă de recall: experimentul are numai 107 linii
observate și `recall_measured=false`.

### Pilot P2 controlat pe conversiile 2026 sub 70%

La 26 august 2026 a fost rulat un pilot separat pe Arad, Botoșani și Buzău,
cu `google:gemini-3.6-flash`, plafon dur de 3 USD pentru fiecare PDF și
output-uri candidate în afara corpusului. Un candidat a fost publicat numai
dacă procesa din nou toate paginile, creștea numărul absolut de celule
numerice strict validate, reducea erorile, păstra structura workbook-ului și
nu introducea erori de formule. Republicarea a trecut prin același gard atomic
PDF/Excel/analiză/manifest.

Costul de mai jos include atât prima citire izolată, cât și replay-ul de
publicare. Câmpul `llm_cost_usd` din manifest descrie numai replay-ul final al
bundle-ului și nu trebuie adunat singur pentru a reconstrui costul întregului
experiment.

| Municipiu | Linii strict validate | Celule numeric strict validate | Erori | Cost total pilot + publicare | Celule validate/USD |
|---|---:|---:|---:|---:|---:|
| Buzău | 856 → 863 | 3.250 → 3.304 (+54) | 112 → 79 | 0,2273 USD | 237,57 |
| Botoșani | 4.206 → 4.271 | 13.904 → 14.121 (+217) | 2.839 → 2.009 | 1,2217 USD | 177,62 |
| Arad | 4.793 → 4.862 | 15.476 → 15.619 (+143) | 2.161 → 1.598 | 1,2503 USD | 114,37 |
| **Total** | **+141** | **+414** | **−1.426** | **2,6993 USD** | **153,37** |

Au fost expuse și 1.383 de celule numerice suplimentare, dintre care 414 au
obținut imediat statut strict verificat. De aceea procentul numeric strict
verificat a crescut la Buzău (62,4% → 63,1%), dar a scăzut la Botoșani
(63,6% → 62,3%) și Arad (76,6% → 75,2%): numitorul a crescut mai repede decât
subsetul demonstrat. Acesta nu este un regres de precizie al celulelor marcate
verificate; celulele noi fără dovadă suficientă rămân explicit neverificate.
Niciunul dintre cele trei fișiere nu a trecut pragul de 70% al
`observed_strict_line_rate`, iar `recall_measured=false` rămâne corect.

Auditul de decizie pentru Cluj a găsit 11.829 probleme publice: 10.701
duplicate cu valori diferite, 296 nepotriviri de nume, 277 probleme de cod,
243 egalități ierarhice, 211 celule ilizibile și 101 identități globale.
În același timp, 609 din cele 753 de pagini sunt deja mapate ca
`scan_annual_total`. La randamentul pilotului, încă 3 USD ar proiecta numai
aproximativ 460 de celule strict validate, fără să rezolve cauza dominantă a
scorului Cluj: coduri legitime repetate între subdocumente/instituții sunt
comparate în același context și marcate drept duplicate.

Decizia este astfel **mapper/assembler înainte de LLM** pentru Cluj:

- identifică limitele formularelor/subdocumentelor și păstrează instituția sau
  ordonatorul în identitatea analitică;
- extinde cheia de deduplicare cu acel context, fără a șterge automat rânduri;
- adaugă fixture-uri consecutive care dovedesc atât repetarea legitimă, cât și
  duplicatul real;
- abia apoi rulează un pilot LLM țintit pe cele 211 celule ilizibile și pe
  grupurile aritmetice rămase.

### Rezultatul implementării pentru Cluj

Secvența de mai sus a fost executată integral. Asamblorul detectează acum
fiecare formular `BUGET INDIVIDUAL`, păstrează instituția și codul fiscal în
`institution` și `context_id`, propagă capitolul funcțional pe paginile de
continuare și folosește contextul în verificarea duplicatelor. Au fost
identificate 147 de contexte (146 de instituții și un fallback sigur pe
pagină), iar corpusul Cluj a trecut de la 39 la 156 de subdocumente. Fixture-ul
de regresie acoperă atât aceeași poziție bugetară legitimă în două instituții,
cât și un duplicat real în interiorul aceluiași formular.

Pasul determinist, fără LLM, a produs cea mai mare parte a câștigului:

- linii strict validate: 9.762/21.104 (46,3%) → 20.833/22.100 (94,3%);
- celule numerice strict validate: 19.846/31.106 (63,8%) →
  29.895/31.106 (96,1%);
- duplicate cu valori diferite: 10.701 → 382;
- probleme totale: 11.829 → 1.494.

Recuperarea LLM a fost apoi limitată la paginile catastrofice și grupurile
aritmetice rămase, cu plafon de 3 USD. Citirea izolată a costat 0,8217 USD, iar
replay-ul de publicare din cache 0,0085 USD, pentru un cost complet de
**0,8302 USD**. Câmpul `llm_cost_usd` din manifest păstrează numai cei
0,0085 USD ai replay-ului bundle-ului publicat; nu reprezintă costul complet
al experimentului.

Bundle-ul publicat `7ffa514ed21af37f3e04704b` are 20.959/22.104 linii strict
validate (94,8%) și 30.035/31.177 celule numerice strict validate (96,3%).
Față de bundle-ul inițial, numărul absolut de celule numerice strict validate
a crescut cu 10.189, erorile au scăzut de la 831 la 654, duplicatele de la
10.701 la 382, iar celulele ilizibile de la 211 la 192. Au fost acceptate 138
de reparații aritmetice; 73 de grupuri nerezolvate rămân marcate explicit,
împreună cu avertismentele și dovezile de proveniență. Publicarea a avut loc
numai după creșterea numărului absolut de linii și celule strict validate și
după verificarea absenței erorilor de formule în workbook.

Aceste procente sunt rate observate de validare, nu o măsurare directă a
recall-ului față de un adevăr de referință complet; de aceea
`recall_measured=false` rămâne neschimbat.

## Option E — etalon independent pe fișier și familie

Următoarea fază nu mai deduce recall-ul din liniile deja extrase. Comenzile
`bgconvertor annotate` inventariază fiecare pagină PDF și foaie Excel, apoi
cer transcriere exhaustivă independentă înainte să afișeze output-ul
converterului. În ediția 2024, inventarul are 4.170 de pagini PDF plus patru
foi Excel native, iar cele 13 intrări sub 70% sunt prioritizate automat ca
benchmark-uri complete.

Ground truth păstrează valoarea tipărită, valoarea normalizată în `mii lei`,
codurile funcțional/economic și contextul instituție/formular/subdocument.
Matching-ul este unu-la-unu; o valoare greșită produce atât un fals negativ,
cât și un fals pozitiv. Citirile incerte și discrepanțele cer un al doilea
reviewer. PDF-urile, randările și drafturile rămân în `runs/`, iar exportul
public refuză implicit un inventar incomplet.

Implementarea și fluxul reproductibil sunt documentate în
[`docs/adnotare.md`](adnotare.md). Existența instrumentului nu schimbă încă
metricile publice: `recall_measured` devine adevărat numai după ce scope-ul
independent este complet și trece auditul.

### Pilot exhaustiv Giurgiu 2024

Primul inventar exhaustiv acoperă toate cele 28 de pagini ale sursei Giurgiu:
5 pagini bugetare și 23 de pagini de hotărâre/anexe clasificate separat. Pe
cele 5 pagini bugetare au fost transcrise independent 169 de rânduri și 507
celule numerice. Față de acest adevăr înghețat, extractorul inițial a regăsit
21/507 celule (4,14% recall, 4,46% precizie).

Mapperul comparativ nou separă explicit BVC-ul anului precedent, execuția
anului precedent și BVC-ul curent, propagă contractul coloanelor pe paginile
de continuare, repară numai despărțirile complet observate și exclude anexele
de investiții. Rezultatul determinist este 491/507 celule regăsite (96,84%) și
491/497 celule corecte (98,79%), cu 5/5 pagini bugetare găsite și fără pagini
false. Paginile 3, 4, 6 și 7 au 100% recall și precizie; pagina 5 rămâne la
119/135 celule regăsite și 119/125 corecte din cauza unui bloc de rânduri
colapsate de OCR. Costul API al rezultatului acceptat este 0 USD.

Un experiment LLM izolat, de 0,1282 USD, a citit toate cele 507 celule, dar a
emis 620 de predicții și a coborât precizia la 81,77%. Candidatul a fost
respins și păstrat numai local pentru diagnostic; automatizarea care l-ar fi
activat nu face parte din pipeline. Discrepanța de pe pagina 5 rămâne marcată
pentru al doilea reviewer. Prin urmare, pilotul demonstrează depășirea țintei
de recall pentru această familie, dar nu încă poarta de 99,5% precizie și nu
schimbă `recall_measured=false` pentru întregul corpus 2024.

### Pilot exhaustiv Călărași 2024

Al doilea pilot de familie mare clasifică toate cele 87 de pagini ale sursei:
63 pagini bugetare și 24 de pagini de hotărâre, investiții, achiziții sau
personal excluse explicit din numitor. Etalonul source-only înghețat conține
7.748 de celule numerice. Contextul instituție/formular/subdocument și secțiune
face parte din identitatea faptului, astfel încât repetițiile legitime între
școli nu mai sunt confundate cu duplicatele reale.

| Candidat Călărași | Celule corecte | Celule emise | Recall | Precizie | Recall pagini bugetare | Cost API conversie cumulat |
|---|---:|---:|---:|---:|---:|---:|
| Mapper înaintea pilotului | 5.641 | — | 72,81% | — | — | 0 USD |
| Mappere deterministe noi | 6.230 | 7.472 | 80,41% | 83,38% | 95,24% | 0 USD |
| + recuperare LLM acceptată | 6.238 | 7.480 | 80,51% | 83,40% | 95,24% | 0,7294 USD |

Îmbunătățirea deterministă recuperează tabelele inițiale cu două coloane,
antete anuale cu 11 coloane, continuări fără cod, secțiuni, instituții școlare
numerotate și valori OCR colapsate vertical. Citirea LLM folosește schema
coloanelor inferată pe pagină și o îmbinare conservatoare după cod, context și
denumire normalizată. Pe paginile deja productive, rândurile fără identitate
sigură nu sunt anexate.

Prima citire LLM a expus o deplasare a totalului anual după trimestre și ar fi
coborât precizia la 70,56%; candidatul a fost respins. După corectarea ordinii,
citirea de pagină a adăugat opt celule corecte pe pagina 27. O reparare
aritmetică separată a adăugat nouă predicții nevalidate pe pagina 60 și a fost,
de asemenea, respinsă. Bundle-ul public este reluarea din cache fără acea
reparare. Costul source-only al adnotării a fost aproximativ 7,264 USD, separat
de costul conversiei; împreună cu cele 0,7294 USD de experimente ale
converterului, pilotul Călărași a costat aproximativ 7,9934 USD din bugetul de
evaluare, în timp ce rularea publică rămâne sub plafonul de 3 USD/fișier.

Rezultatul nu trece încă porțile de 90% recall, 99,5% precizie și 98% recall al
paginilor. Câștigul LLM este prea mic pentru escaladare generală; următoarele
îmbunătățiri trebuie să fie mappere deterministe pentru paginile 15, 53–54,
65–68 și 72, nu transcriere integrală mai scumpă.

## Porți pentru ținta de 90%

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
