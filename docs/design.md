# Arhitectură

Distilat din jurnalul de dezvoltare al proiectului (`PLAN.md` conține
istoricul complet, inclusiv măsurătorile și direcțiile abandonate).

## Problema

Bugetele locale românești sunt publicate în principal ca anexe PDF la
hotărârile de consiliu; câteva administrații oferă și XLS/XLSX nativ. Unele
PDF-uri sunt generate digital, cu grile trasate, multe sunt scanate — rotite
în oricare din cele patru orientări, cu ștampile peste cifre, tipărite de
o duzină de furnizori diferiți de software bugetar cu machete de tabel
incompatibile, în două convenții locale de scriere a numerelor. Datele din
interior urmează însă un singur standard național: clasificația
indicatorilor privind finanțele publice (Ordinul MFP 1954/2005) și
aritmetica legii bugetare.

## Principiul de bază

**Extrage cu instrumente deterministe, verifică prin aritmetică, repară cu
un LLM doar sub demonstrație.**

Redundanța clasificației (sume de control pe rânduri, capitol = Σ
subcapitole, grupa = Σ titluri, identitățile pe secțiuni, formulele de
compoziție tipărite chiar în denumirile rândurilor) face ca o singură
cifră citită greșit să rupă aproape întotdeauna o ecuație. Asta oferă
*detectarea* erorilor cu precizie ridicată și cost zero — astfel încât
pasul scump și predispus la halucinații (LLM-ul cu vedere) este retrogradat
la *reparație*: recitește doar grupurile de rânduri semnalate, iar o
reparație se aplică **numai dacă recitirea face ca sumele să se închidă**.
Celulele fără nicio constrângere care să le demonstreze rămân marcate
`unverified`. Nimic nu este vreodată ghicit în tăcere.

O lecție măsurată devreme a fixat acest principiu: dacă i se spunea
modelului suma așteptată, acesta raționaliza valorile către ea. Prompturile
de reparație sunt, prin urmare, transcriere pură; toată aritmetica rămâne
de partea noastră.

## Pipeline

```
[native XLS/XLSX reader | profile -> digital grid | orient -> OCR(docling)]
        -> layout mappers / normalized page payloads
        -> assemble (documents, institutions, sections, code semantics)
        -> validate (nomenclator + arithmetic)      -> Excel + dataset
        -> LLM tiers (fallback / sum-repair / cell recovery), re-validate
```

Fiecare etapă scrie JSON per pagină într-un **run store** indexat după
`(file, page, stage, config-hash)`: rulările repetate sar peste paginile
finalizate, iar o schimbare de configurație sau de versiune de cod
invalidează exact etapele care depind de ea. Etapele scumpe (OCR) sunt
separate de cele ieftine (maparea), astfel încât iterarea pe mappere nu
replătește niciodată OCR-ul. Eșecurile sunt artefacte per pagină, cu
traceback-uri; o cădere la pagina 37 nu pierde niciodată paginile 1–36.
Workerii publică JSON-ul prin înlocuire atomică în același director, astfel
încât procesul părinte care urmărește progresul vede versiunea veche sau cea
nouă, niciodată un fișier parțial. Un artefact legacy gol/trunchiat este tratat
ca un cache miss și se reconstruiește; nu poate opri conversia întregului PDF.

Ramura Excel nativă citește valorile afișate fără să recalculeze sau să
modifice registrul oficial, păstrează codurile numerice cu zerouri inițiale și
normalizează explicit lei → mii lei. Foile sunt transformate în același
contract intermediar ca o pagină PDF; de acolo înainte, asamblarea, validarea,
analiza, exportul și auditul sunt identice. Sursa originală `buget_orig.xls[x]`
și rezultatul public `budget_file.xlsx` au roluri și hash-uri distincte.

## Registrul de machete (layouts)

Strategiile de mapare grilă → linii sunt modulare (`layouts/`): tabele
transpuse (indicatorii pe coloane), matrici de buget consolidat (sub-rânduri
pe ani, rândul tipărit cu indecșii coloanelor ca semantică de rezervă) și
mapperul generic de tabel condus de antet (vocabular comun; rezervă
pozițională pentru paginile de continuare fără antet, în ambele ordini de
coloane). Particularitățile furnizorilor trăiesc în date și în module mici:
coduri combinate `capitol.economic` (cu prefixe trunchiate de PDF reparate
din contextul documentului), sufixe-fantomă `.00`, întreaga menajerie de
marcaje `x` din OCR, două convenții locale de scriere a numerelor, stiluri
per pagină de rupere a denumirilor pe rânduri, împărțirea documentelor pe
instituții condusă de antetele paginilor.

P1 adaugă explicit starea de mapare între pagini consecutive: familia,
numărul coloanelor, rolul fiecărei coordonate și anul bugetar. Un antet absent
sau deteriorat poate astfel moșteni schema precedentă numai când geometria și
forma rândurilor sunt compatibile; starea se resetează la un gol de pagini sau
la o schemă nouă. Asamblarea unește un rând rupt la schimbarea paginii numai
pentru două forme complementare și neechivoce (identitate fără valori urmată
de valori anonime, respectiv nume fără valori urmat de cod + valori).

Cheile anuale sunt derivate din anul corpusului și din antet, nu dintr-o
constantă 2026: `total_<an>`/`buget_<an>` și `est<an+1>...`. Același contract
alimentează Excelul, analiza JSON, fallback-ul LLM și graficele multianuale.
Fișierele 2026 rămân compatibile semantic cu vechile chei.

Rutarea OCR este deterministă și limitată la doi candidați. O pagină digitală
fără caroiaj încearcă întâi TableFormer peste stratul de text; dacă scorul
structural nu trece pragul, se randează și se face OCR. Pentru o pagină
scanată slabă, singura alternativă comută cell matching și alege adaptiv
eliminarea pixelilor de ștampilă plus un deskew mic. Câștigătorul este ales
după acoperirea celulelor, identitatea rândurilor și igiena parse-ului;
egalitatea păstrează baseline-ul. Limba OCR, motorul (`auto`, RapidOCR,
EasyOCR, Tesseract Python sau CLI) și modul TableFormer (`fast`/`accurate`)
sunt aplicate efectiv opțiunilor Docling și fac parte din cheia cache-ului.

## Modelul de verificare

Problemele sunt tipizate (`V1` validitatea codului … `V7` igienă), cu
severități, iar fiecare linie poartă proveniența: pagina, sursa
(`digital`/`ocr`/`native_excel`/`llm`). În export, `verified=true` înseamnă că linia nu
poartă nicio problemă, inclusiv `warning` sau `info`. Metrica agregată
`observed_strict_line_rate` are ca numitor numai liniile extrase și declară
explicit `recall_measured=false`; nu poate demonstra rândurile absente.
Contractul complet este în [quality.md](quality.md).

## Publicarea artefactelor

În corpus, Excelul, `analysis.json` și blocul `conversion` din manifest sunt
un singur bundle versionat. Excelul și analiza sunt produse în fișiere
temporare cu același ID; manifestul, scris atomic ultimul, înregistrează
SHA-256-ul sursei, formatul ei și hash-urile ambelor artefacte. Un eșec restaurează versiunea
anterioară. `bgconvertor corpus audit` recalculează hash-urile și compară
metricile din toate cele trei locuri; agregatul refuză orice bundle
inconsistent. Conversiile cu `--pages` sunt experimente și nu pot înlocui
ieșirile publice.

## Garduri de siguranță pentru LLM

Un registru contabil per fișier consemnează fiecare apel (tokeni, cost,
scop); un buget dur în dolari per rulare oprește pasurile LLM, niciodată
pipeline-ul; apelurile identice se redau la nesfârșit dintr-un cache de
răspunsuri (care servește și drept casete de test offline); apelurile
rulează într-un pool de thread-uri; ieșirile mari sunt transmise în flux;
modul Batch API înjumătățește costul pentru rulările nesupravegheate;
recitirile pentru repararea sumelor decupează imaginea la grupul de rânduri
atunci când sunt disponibile bounding box-uri.

P2 adaugă două niveluri distincte de control. Plannerul moale ordonează la
nivelul întregului fișier benzile de fallback, ierarhiile, checksum-urile
trimestriale, identitățile, codurile, duplicatele și recitirile neconfirmate
după beneficiul estimat per dolar și scrie decizia în `llm_plan.json`.
Plannerul contabilizează worst-case și al doilea apel posibil. Ledgerul este
autoritatea dură: rezervă costul și slotul fiecărei cereri reale înainte de
lansare, inclusiv retry, Batch și escaladarea premium atunci când devine
eligibilă, astfel încât apelurile concurente să nu depășească plafonul. Cache
hits nu consumă sloturi API.

Fallback-ul folosește schema paginii (până la 12 coloane) și împarte tabelele
dense în benzi de cel mult 32 de rânduri. Rezultatul plătit este unit cu
rezultatul determinist la nivel de rând/celulă: deterministul câștigă orice
conflict, LLM-ul completează numai goluri și fiecare valoare păstrează propria
proveniență. Orice coloană inventată este respinsă și semnalată.

Pentru egalități, acceptarea nu poate folosi valori OCR nerecitite: fiecare
rând și fiecare coloană participantă trebuie să apară exact o dată în citirea
independentă completă. Pentru coduri și duplicate există porți separate de
nomenclator/nume/coliziune, respectiv două citiri identice. Presetul mixt este
cheap-first; premium-ul este permis doar după eșecul porții ieftine și peste
pragul de beneficiu. La final, V1–V5 și duplicatele V7 sunt reconstruite din
starea reparată, ca o corecție locală să nu ascundă o încălcare nouă. Pentru
artefactele corpusului, CLI refuză în continuare un plafon mai mare de
5 USD/PDF.

## Rezultate negative măsurate (păstrate intenționat)

- Straturile de text încorporate din PDF-urile de copiator au obținut scoruri
  **mai slabe** decât re-OCR-ul la curățenia validată (−8pp pe testul A/B de la
  Bacău). De aceea nu sunt acceptate automat: calea nativă este ieftină și se
  încearcă prima, dar trebuie să treacă scorul structural; altfel OCR-ul raster
  rămâne candidatul de bază.
- Un filtru cromatic de eliminare a ștampilelor nu a mișcat singur nicio ancoră
  de aur. Nu este aplicat global; intră numai în candidatul adaptiv al paginilor
  slabe, împreună cu deskew, și rezultatul este păstrat numai la îmbunătățire.

Harness-ul de evaluare combină ancore de aur pentru toate familiile cu
inventare exhaustive de celule pentru familiile migrate în P1
(`bgconvertor eval`). Raportează separat recall și precizie numai în scope-urile
inventariate integral; astfel ipotezele primesc cifre fără ca un eșantion să
fie prezentat drept acoperire completă.

## Analitice și augmentare

Analiza transversală este o proiecție separată a agregatului public, nu o
extensie a `analysis.json`. `analytics.py` leagă populația și execuția prin
SIRUTA + an, clasificarea NUTS 2024 prin codul județului și inflația HICP prin
an. Calculează indicatorii derivați și publică pentru fiecare rând
eligibilitatea și motivul excluderii. Rangurile se calculează numai pentru
totaluri publice coerente și publică dimensiunea cohortei. Creșterea reală
este calculată numai când există rata medie anuală HICP observată pentru anul
curent; prognozele nu umplu lipsa.

Site-ul și exporturile JSON/CSV/Excel sunt construite din același obiect
`AnalyticsDataset`; astfel pagina nu poate afișa un clasament diferit de
fișierul descărcabil. Proveniența augmentărilor este propagată din fișierele
versionate din `reference/`, cu sursă, dată, licență și chei de asociere, în
timp ce faptele extrase rămân în bundle-ul PDF/Excel/analysis. Contractul
complet este în [analytics.md](analytics.md).
