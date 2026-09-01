# Adnotare independentă și măsurarea recall-ului

Validatorul poate demonstra că o celulă extrasă este coerentă, dar nu poate
număra un rând pe care converterul nu l-a văzut. Comenzile
`bgconvertor annotate` construiesc adevărul de referință independent necesar
pentru `validated_cell_recall`, precizia celulelor și recall-ul paginilor
bugetare.

Instrumentul este local și offline. PDF-urile, randările, output-ul
converterului și drafturile sunt păstrate sub `runs/annotations/`, director
ignorat de Git. Exportul public conține numai valori structurate, hash-ul
sursei și proveniența review-ului.

Fluxul manual de bază nu cere cheie API. Pentru documente mari există și un
ajutor opțional source-only: un model vision poate produce un prim draft din
randarea PDF, dar acel draft nu este nici adevăr de referință, nici output al
converterului și nu poate fi înghețat singur. Costul său este ținut într-un
ledger separat de costul conversiei publice.

## Fluxul complet

Sursele 2024 trebuie descărcate și verificate înainte de inițializare:

```bash
uv run python data/2024/download.py
uv run bgconvertor annotate init 2024
uv run bgconvertor annotate serve 2024
```

`init` verifică SHA-256 față de manifest și inventariază fiecare unitate a
sursei. Pentru ediția 2024 înseamnă 4.170 de pagini PDF și patru foi din cele
trei registre Excel native, 4.174 de unități în total. O reluare simplă
reutilizează workspace-ul. `--refresh` recitește sursele și artefactele
converterului fără să șteargă review-urile deja salvate.

În interfață, fiecare unitate trece prin următoarele stări:

1. **Clasificare independentă.** Reviewerul vede numai pagina PDF sau foaia
   Excel și alege `tabel bugetar`, `alt tabel/anexă`, `fără date bugetare` ori
   `nesigur`. Sugestia automată este ascunsă până la prima salvare.
2. **Transcriere exhaustivă.** Pentru o pagină bugetară dintr-un fișier
   benchmark sunt introduse toate celulele numerice tipărite. Grila acceptă
   navigare din tastatură, rânduri duplicate și paste TSV cu antet. Unitatea
   sursei (`lei` sau `mii lei`) și notația numerelor sunt explicite.
3. **Context.** Instituția, formularul, subdocumentul și secțiunea fac parte
   din identitatea rândului. Astfel, aceeași poziție bugetară tipărită legitim
   în două formulare nu este confundată cu un duplicat.
4. **Înghețarea adevărului.** Backend-ul refuză rânduri fără identitate,
   valori ambigue, unități necunoscute, celule duplicate și pagini declarate
   exhaustive fără conținut numeric. După înghețare, adevărul nu mai poate fi
   editat fără o deblocare explicită.
5. **Comparația.** Abia după înghețare devin vizibile faptele produse de
   converter. Diferențele sunt calculate unu-la-unu, pe pagină, context,
   identitate, coloană și valoare normalizată în `mii lei`.
6. **Revizia a doua.** O citire incertă sau o diferență față de converter
   intră în coada de review. Al doilea alias trebuie să fie diferit de primul;
   confirmarea nu rescrie adevărul.

Serverul ascultă numai pe `127.0.0.1`, folosește un token aleator per pornire,
respinge host-uri nelocale și nu încarcă sursele într-un serviciu extern.

## Flux asistat pentru fișiere exhaustive mari

Cele patru utilitare publice din `scripts/` reduc tastarea fără să permită
converterului să-și construiască propriul etalon:

1. `annotation_vision_draft.py` citește exclusiv imaginile PDF și scrie câte un
   draft JSON versionat per pagină, cu SHA-256-ul sursei, modelul, parametrii și
   ledgerul de cost;
2. `annotation_consensus.py` compară draftul documentului combinat cu anexe
   oficiale independente (de exemplu funcționare și dezvoltare) și confirmă o
   celulă numai când suma componentelor este exactă sau când aceeași valoare
   apare în unica secțiune tipărită;
3. `annotation_ocr_consensus.py` face o a doua citire locală, fără API, pe
   coordonate de coloană, păstrează boxele și scorurile OCR și aliniază
   monoton rândurile;
4. `annotation_import_draft.py` este poarta fail-closed: refuză importul dacă
   lipsește evidence pentru măcar o celulă, dacă hash-ul sursei diferă ori dacă
   deciziile vizuale reziduale nu au reviewer. Rândurile tipărite identic în
   secțiuni diferite sunt păstrate cu context, nu deduplicate.

Exemplu schematic (centrele de coloană trebuie măsurate pe layout-ul sursei):

```bash
uv run python scripts/annotation_vision_draft.py SOURCE.pdf runs/annotations/DRAFT \
  --year 2024 --columns total_2024,restante,trim1,trim2,trim3,trim4,est2025,est2026,est2027 \
  --rotation 270 --max-cost 5

uv run python scripts/annotation_consensus.py runs/annotations/DRAFT \
  --mirror runs/annotations/FUNCTIONARE \
  --mirror runs/annotations/DEZVOLTARE \
  --out runs/annotations/component-consensus.json

uv run python scripts/annotation_ocr_consensus.py SOURCE.pdf \
  runs/annotations/DRAFT runs/annotations/ocr-consensus.json \
  --rotation 270 \
  --centers total_2024=.459,restante=.521,trim1=.583,trim2=.644,trim3=.706,trim4=.768,est2025=.829,est2026=.891,est2027=.953

uv run python scripts/annotation_import_draft.py runs/annotations/2024 DOCUMENT_ID \
  runs/annotations/DRAFT \
  --component-report runs/annotations/component-consensus.json \
  --ocr-report runs/annotations/ocr-consensus.json \
  --visual-decisions runs/annotations/visual-decisions.json \
  --reviewer REVIEWER_ALIAS --freeze
```

Fișierele de evidence, PDF-urile și drafturile rămân ignorate de Git. CI testează
offline normalizarea, consensul, alinierea, acoperirea fail-closed și păstrarea
repetițiilor legitime folosind date sintetice sanitizate.

## Modelul datelor

Fiecare celulă de referință păstrează:

- valoarea exact așa cum a fost citită din sursă;
- valoarea derivată în `mii lei`;
- coloana bugetară (`buget_2023`, `executie_2023`, `total_2024`, `trim1`,
  `est2025` etc.); eticheta păstrează sensul și anul tipărit, chiar când
  documentul compară bugetul curent cu un exercițiu anterior;
- codul tipărit, codul funcțional și codul economic, când există;
- denumirea, instituția, formularul, subdocumentul și secțiunea;
- certitudinea citirii și nota reviewerului;
- numărul paginii sau numele foii, hash-ul sursei și revizia adnotării.

Celulele marcate `X`, spațiile goale și simplele liniuțe nu sunt celule
numerice și nu intră în numitor. Valorile derivate de analytics, care nu sunt
tipărite în sursă, sunt de asemenea excluse din precizia extracției.

## Scoruri și porți

```text
validated_cell_recall = celule așteptate regăsite exact / toate celulele așteptate
cell_precision        = celule așteptate regăsite exact / toate celulele emise
budget_page_recall    = pagini bugetare detectate structural / toate paginile bugetare
budget_page_precision = pagini bugetare corecte / toate paginile detectate ca bugetare
```

O valoare greșită este simultan o celulă așteptată lipsă și o celulă emisă
suplimentar. Matching-ul este unu-la-unu, astfel încât un duplicat nu poate
satisface aceeași celulă de două ori.

Pentru PDF, detecția structurală folosește decizia persistată
`mapping_context.budget_table`, nu existența unei valori numerice. Astfel, o
pagină bugetară legitimă ce conține numai marcaje `X` sau sume intenționat
necompletate nu este raportată fals ca pagină ratată.

```bash
uv run bgconvertor annotate status 2024
uv run bgconvertor annotate audit 2024 --json-out tmp/annotation-audit.json
uv run bgconvertor annotate score 2024
```

`score` scrie candidatul, hash-urile artefactelor per etapă și configurațiile
în `runs/annotations/2024/score.json`. Un raport parțial rămâne util pentru
diagnostic, dar declară `recall_measured=false` până când inventarul este
rezolvat și toate paginile bugetare din scope-urile exhaustive sunt înghețate.
Auditul raportează separat fiecare familie de layout întâlnită pe paginile
bugetare și refuză publicarea dacă familia nu are nicio pagină exhaustivă
înghețată. Un document marcat `sample` trebuie să contribuie efectiv cel puțin
o asemenea pagină.

Pentru PDF-uri, converterul persistă după un export reușit și snapshot-ul
source-bound `final_candidate.json`. Acesta include exact `ConversionResult`
care a produs Excelul, inclusiv merge-urile țintite LLM care există numai după
asamblare. Scorerul îl preferă artefactelor pre-repair și îi publică hash-ul;
dacă lipsește sau SHA-256-ul sursei nu corespunde, revine transparent la
asamblarea artefactelor per pagină.

Porțile de publicare rămân:

- ≥90% recall pe celule validate pentru fiecare familie suportată;
- ≥99,5% precizie a celulelor;
- ≥98% recall al paginilor cu tabele bugetare;
- surse cu hash verificat și zero unități `nerevizuit` sau `nesigur`;
- a doua revizie pentru citiri incerte și discrepanțe;
- zero PDF-uri, randări, cache-uri sau secrete în export.

Exportul este intenționat separat de draft:

```bash
uv run bgconvertor annotate export 2024 --out benchmarks/2024
```

Comanda refuză implicit un benchmark incomplet. `--allow-incomplete` este
destinat exclusiv schimbului de drafturi și nu poate justifica o afirmație
publică de recall.

## Prioritatea 2024

La inițializare, cele 13 intrări cu `observed_strict_line_rate < 70%` primesc
automat scope `full`: Giurgiu, Călărași, Sibiu, Deva, Buzău, Zalău, Târgu Jiu,
Focșani, Vaslui, Galați, Alba Iulia, Brăila și Ploiești. Giurgiu este pilotul
de ergonomie deoarece are rata observată cea mai mică și numai 28 de pagini.

Piloții exhaustivi Giurgiu, Călărași și Sibiu sunt înghețați. Călărași
clasifică toate cele 87 de pagini și inventariază 7.748 de celule pe 63 de
pagini bugetare; baza curentă regăsește 6.981 de celule (90,10%), emite 7.640
(91,37% precizie) și detectează toate cele 63 de pagini bugetare. Sibiu
inventariază 10.531 de celule pe toate cele 98 de pagini bugetare; mapperul
determinist regăsește 9.751 (92,59%), emite 10.113 (96,42% precizie) și
detectează 98/98 pagini. Ambele depășesc poarta de recall, dar nu poarta de
99,5% precizie; discrepanțele rămase cer un al doilea reviewer uman distinct
înaintea unei declarații de release gate.
Buzău are toate cele 46 de pagini bugetare și 8.236 de celule numerice înghețate
prin consens source-only. Diferențele față de converter rămân în coada separată
de revizie a doua; un scor diagnostic nu ocolește această poartă. Contextul
instituție/formular/subdocument/secțiune este obligatoriu în matching și în
detecția duplicatelor, pentru a păstra repetițiile legitime dintre instituții.

După cele 13 fișiere, se marchează `sample` cel puțin un document complet din
fiecare familie de layout rămasă. Numai apoi există bază pentru o afirmație de
recall per familie. Fluxul manual și OCR-ul local nu consumă API; draftul vision
opțional are ledger separat. Un eventual P2 păstrează plafonul de 3 USD/fișier
pentru pilot și 5 USD/PDF pentru publicare.
