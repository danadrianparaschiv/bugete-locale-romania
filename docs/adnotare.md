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
budget_page_recall    = pagini bugetare cu fapte emise / toate paginile bugetare
budget_page_precision = pagini bugetare cu fapte emise / toate paginile cu fapte emise
```

O valoare greșită este simultan o celulă așteptată lipsă și o celulă emisă
suplimentar. Matching-ul este unu-la-unu, astfel încât un duplicat nu poate
satisface aceeași celulă de două ori.

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

După cele 13 fișiere, se marchează `sample` cel puțin un document complet din
fiecare familie de layout rămasă. Numai apoi există bază pentru o afirmație de
recall per familie. Adnotarea nu consumă API; un eventual P2 ulterior păstrează
plafonul de 3 USD/fișier pentru pilot și 5 USD/PDF pentru publicare.
