# Lecții învățate: procesarea bugetelor locale 2024–2026

Retrospectiva primelor trei ediții ale corpusului — ce am aflat
convertind bugetele municipiilor reședință de județ pe 2026, 2025 și 2024, și
de ce să ținem cont când repetăm exercițiul pentru 2027. Toate cifrele
provin din conversiile și evaluările efectuate (manifeste,
`evals/rezultate.csv`, istoricul git).

> **Notă P0 (24.08.2026):** bilanțurile de linii/procente de mai jos sunt
> instantanee istorice, produse înaintea contractului de bundle. Auditul
> reproductibil a găsit 69 intrări marcate convertite: numai 11 au valori
> legacy coerente între Excel, `analysis.json` și manifest, iar 58 diferă.
> Migrarea rapidă din aceeași zi le-a republicat pe toate: 69 bundle-uri
> moderne verificate, 0 inconsistente, în 5,6 minute și cu 0 USD cost API.
> Un control vizual ulterior a găsit și corectat etichete de secțiune
> interpretate ca formule; republicarea finală a durat încă 4,4 minute, tot cu
> 0 USD, pentru aproximativ 10 minute end-to-end.
> Nici rezultatul migrării nu măsoară recall. Vezi [quality.md](quality.md) și
> rulează `bgconvertor corpus audit data --strict --require-modern`.

**Bilanț istoric (neautoritativ):** documentația veche raporta pentru 2026
32/41 orașe, 75.407 linii și 66,3%, iar pentru 2025 37/42 intrări, 45.649
linii și 74,0%. Cifrele se păstrează doar pentru contextul retrospectiv.

## 1. Contextul anului: bugetul de stat întârziat comprimă totul

Legea bugetului de stat 43/2026 a apărut abia în aprilie, iar efectul s-a
văzut în lanț: majoritatea municipiilor au publicat proiectele în
7–9 aprilie (imediat după legea de stat) și au aprobat între 27 aprilie
și 13 mai. Coada s-a întins până pe 4 iunie (Focșani).

**Pentru 2027**: fereastra de colectare a documentelor e îngustă și
târzie — nu planifica achiziția corpusului pentru ianuarie; monitorizează
apariția legii bugetului de stat și pornește colectarea la 1–2 săptămâni
după ea, când apar simultan aproape toate proiectele.

## 2. Proveniența documentelor: sursa e jumătate din problemă

- **Nu orice PDF „de buget" e anexa bugetară.** Satu Mare a publicat la
  loc de cinste broșura de prezentare pentru cetățeni („STABILITATE ÎN
  VREMURI DE CRIZĂ") — zero tabele pe Ordinul 1954. Am descoperit-o abia
  la conversie (0 linii extrase). Semnalul de alarmă exista în triaj:
  toate paginile „unknown". **Regulă pentru 2027: triajul face parte din
  achiziție** — un fișier cu layout-uri necunoscute pe tot eșantionul se
  verifică manual înainte de a intra în corpus.
- **Versiunile documentului contează și trebuie înregistrate.** Corpusul
  2026 amestecă `official_proposal` (proiectul pus în dezbatere — 5 din
  8 orașe ale batch-ului digital), `approved_initial` (anexa HCL) și
  `approved_rectification` (Baia Mare — prima versiune găsită era deja
  rectificarea din 20 mai, nu bugetul inițial din 17 aprilie). Cifrele
  diferă între versiuni; orice analiză comparativă trebuie să citeze
  `document_status`. **Pentru 2027**: revenire programată la 2–3 luni
  după aprobare pentru a înlocui proiectele cu anexele aprobate.
- **Șase municipii nu au publicat deloc un PDF utilizabil** (Reșița,
  Târgu Jiu, Iași, Slatina, Drobeta-Turnu Severin, Râmnicu Vâlcea) — la
  Iași am găsit HCL-ul de aprobare, dar nu și anexa. Drobeta a publicat
  **XLSX** (singura!) — de altfel formatul ideal; merită cerut explicit
  prin solicitări 544/2001 acolo unde PDF-ul lipsește.
- **Cronologia adoptării e o sursă de date în sine.** Cercetarea datelor
  de dezbatere/aprobare a scos constatări de transparență: Giurgiu a
  înregistrat proiectul cu o zi înainte de vot, Miercurea Ciuc l-a pus pe
  ordinea de zi în ziua aprobării, Reșița nu are niciun anunț de
  consultare găsibil. **Pentru 2027**: colectarea cronologiei se face
  odată cu documentul (anunțul de dezbatere + HCL-ul), nu retroactiv —
  retroactiv a costat o campanie separată de cercetare web.
- **Sursele oficiale sunt fragile**: linkuri Lotus-Notes cu ID-uri
  opace, TLS stricat (alexandria.ro), fișiere de 156MB (Sibiu, peste
  limita GitHub), site-uri care resping fetch-urile automate (403 la
  primăriile Constanța, Craiova, pmb.ro). Manifest-ul cu sume de control
  și `download.py` pentru fișierele mari s-au dovedit alegeri corecte.

## 3. Calitatea documentelor: un spectru, nu o dihotomie

Distribuția istorică raportată pe 2026 (înaintea auditului de bundle):

| Clasă | Exemple | Rezultat tipic |
|---|---|---|
| Digital cu grilă | Alba Iulia, Târgoviște | 100% verificat, $0 |
| Digital cu anexă pe unități | Târgu Mureș (38 instituții) | 84% după separarea instituțiilor |
| Scanare bună, layout cunoscut | Suceava, Sfântu Gheorghe | 92–96% |
| Scanare cu layout necunoscut | Miercurea Ciuc (2.5% determinist) | ~90% doar cu transcriere LLM |
| Scanare aproape nestructurabilă | Galați (0% determinist) | ~73%, în întregime LLM |
| Maraton de copiator | Cluj-Napoca (753 pagini) | 44% și platou — restul cere alt tip de recuperare |

Lecții concrete:

- **Un fallback LLM trebuie evaluat la nivel de celulă, nu după numărul de
  rânduri.** În pilotul Călărași, o ordine greșită a coloanelor dinamice a
  deplasat totalul anual după trimestre, iar potrivirea numai după cod a dublat
  rândurile fără cod. Etalonul exhaustiv a făcut regresia vizibilă imediat:
  recall-ul creștea marginal, dar precizia cobora la 70,56%. Ordinea semantică
  este acum total anual → trimestre → prognoze, iar îmbinarea folosește cod,
  context și denumire normalizată și refuză rândurile fără identitate sigură pe
  pagini deja productive.
- **Acceptarea trebuie să fie selectivă și reproductibilă.** Citirea corectată
  a recuperat opt celule validate pe pagina 27; o reparație aritmetică a adăugat
  nouă predicții nevalidate pe pagina 60. Bundle-ul public a fost reconstruit
  din același cache cu reparațiile țintite dezactivate, păstrând doar câștigul
  măsurat și fără cost suplimentar.

- **Grădina zoologică a furnizorilor e nesfârșită**: formate numerice
  românești și americane, marcaje „X" în zeci de variante OCR, coduri
  combinate capitol+economic, tabele transpuse, matrice de buget
  centralizat, pagini de continuare fără antet, denumiri rupte pe două
  rânduri. Fiecare oraș nou a adus măcar o variație; registrul de
  machete + fixture-urile etalon au făcut extinderile sigure (evalul
  130/148 n-a regresat nicio dată).
- **Anexele pe unități cer separare pe instituții**, altfel codurile
  repetate produc mii de avertismente false (Târgu Mureș: 13.5% → 84.3%
  doar din separare). Delimitatorii diferă per furnizor („Instituția
  publică:", nume în majuscule + CUI) — de căutat activ la orașe noi.
- **Triajul înainte de orice cheltuială** a fost cel mai bun instrument
  economic: estimările de cost au prăbușit repetat presupunerile
  (Brăila de la $25 estimat inițial la $0.50 real) și au prins
  documentul greșit de la Satu Mare.

## 4. Adaptarea procesului de conversie

- **Poarta aritmetică e fundația.** O corecție LLM se aplică doar dacă
  face sumele să se închidă — aceasta reduce puternic riscul, dar nu
  demonstrează celulele fără constrângeri și nu măsoară ce lipsește. Nicio decizie din 2026 nu a
  slăbit această regulă și nici în 2027 nu trebuie s-o facă vreuna.
- **Cache + reluare gratuită au plătit chirie de multe ori**: blocaje,
  chei expirate, plafoane atinse — toate reluate la $0. Promovarea celor
  3 orașe din evaluare direct în corpus (cache integral) a făcut un
  batch aproape gratuit.
- **Plafonul de buget s-a putut depăși** cu apeluri mari concurente
  (maxim observat: $7.29 la plafon $3 — apeluri de transcriere de ~$1+
  lansate în paralel). Remediat la 24.08.2026: costul estimat este rezervat
  înaintea lansării apelului.
- **Rulările lungi au nevoie de watchdog**: un request agățat spre un
  furnizor a înghețat o rulare 1.5 ore fără nicio eroare. Acum există
  deadline per apel și abandon la lipsă de progres — obligatorii pentru
  batch-uri peste noapte.
- **Proveniența trebuie salvată la producere, nu reconstituită.**
  Am ajuns să știm per linie ce model a citit-o (`llm:<model>`), per
  oraș ce preset a rulat (`llm_preset` în manifest) și per apel costul
  (ledger). Reconstituirea retroactivă a fost posibilă doar parțial
  („model neînregistrat" pentru cache-urile vechi). În 2027 totul se
  naște cu proveniență.
- **Manifestul e o resursă partajată**: un proces de batch cu manifestul
  în memorie a șters de două ori date adăugate între timp (cronologia).
  Scrierile trebuie să fie mereu «citește proaspăt + îmbină» — regulă
  valabilă pentru orice câmp nou adăugat în manifest.

## 5. Modelele LLM: economia bate ierarhia

Evaluarea sistematică (5 orașe × 12 preseturi, 65 de rulări, $76 —
detalii în [eval-modele.md](eval-modele.md)) a răsturnat intuițiile:

- **La buget fix, prețul per apel bate calitatea marginală**: modelele
  premium au transcris cel mai bine per pagină, dar și-au permis prea
  puține pagini; modele medii le-au bătut pe orașele grele.
- **`gemini-3.6-flash` a livrat 94% din randamentul lui
  `claude-sonnet-5` la ~40% din cost** (nu 8%, cum arătase evaluarea
  înainte de corecția de facturare). Clasamentul corectat linii/$:
  gpt-5-mini 228, gpt-5.1 161, gemini-3.6-flash 158, sonnet-4-5 129,
  sonnet-5 69, gemini-3.1-pro 50 — **randamentul absolut nu s-a
  schimbat** (sonnet-5 +737 linii, flash +696), doar prețul. Costul real
  al evaluării: ~$120, nu $76.
- **Sarcinile diferă**: repararea punctuală e accesibilă și modelelor
  ieftine; transcrierea integrală de pagină separă brutal capabilitatea
  (haiku: 0 linii verificate pe Galați). De aici slotul separat
  `fallback_model` și presetul mixt.
- **Fiabilitatea de integrare e o axă separată de capabilitate**: erori
  de schemă (rezolvate ulterior cu parser tolerant), ID-uri de model
  retrase peste noapte (`gemini-2.5-flash`), blocaje de rețea. Faza de
  fum de ~$0.40 înaintea oricărei cheltuieli serioase și-a plătit
  costul de zeci de ori.
- **Contabilitatea tokenilor diferă între furnizori** — Gemini își exclude
  tokenii de „thinking" din `completion_tokens` pe endpoint-ul compatibil
  OpenAI, dar îi facturează: registrul nostru a subestimat costul real de
  ~4× până la corecție. Reconciliați periodic registrul cu factura
  furnizorului; un plafon de buget e atât de bun cât e contorul lui.
  Reconcilierea din 24.08 a arătat două cauze suprapuse: gândirea
  ascunsă (69% din outputul facturat pe apeluri reale) ȘI un preț de
  listă de 2× față de estimarea noastră. **Un preț „de verificat" rămas
  neverificat e o eroare de buget, nu o notă de subsol** — verificați
  prețurile pe factura primei zile de rulare, nu la final.
- **Prețurile și ID-urile modelelor sunt perisabile** — de verificat la
  zi înaintea oricărei campanii 2027; plafoanele conservatoare din
  `ledger.py` opresc devreme, niciodată târziu.

## 6. Ce a adăugat ediția 2025: corpusul multi-an

Conversia retroactivă a anului 2025 (batch pe `google:gemini-3.6-flash`,
~$25–30 real) a confirmat lecțiile de mai sus și a adăugat trei:

- **Calitatea documentului e o proprietate a ANULUI, nu a orașului.**
  Aceleași primării publică radical diferit de la un an la altul:
  Craiova 99.8% pe 2025 vs. 60.5% pe 2026, Pitești 76.4% vs. 44.7%,
  Cluj-Napoca 95.4% (digital, 38 pagini) vs. 70.9% (scanare de 753 de
  pagini) — dar și invers: București 0% pe 2025 (sinteză de 4 pagini)
  vs. 81.8% pe 2026, Botoșani 0% vs. 72.8%, Arad 3% vs. 62.2%.
  **Pentru 2027**: nu presupuneți nimic din experiența anului trecut cu
  un oraș; triajul se reface de fiecare dată.
- **Anul secundar acoperă golurile primului.** Șase orașe fără sursă
  utilizabilă pe 2026 au fișiere bune pe 2025 (Iași 74.9%, Slatina
  89.3%, Baia Mare 78.9%, Târgu Jiu 64.2%, Râmnicu Vâlcea 47.7%,
  Ploiești 42.9%). Un corpus multi-an nu e doar istorie — e redundanță
  de surse.
- **Mapările se amortizează pe toate edițiile.** Fixurile deterministe
  făcute pentru 2026 (grile dual-cod, separare pe instituții, sufixul-
  literă de sursă, rânduri derivate din formule) au rulat gratuit pe
  2025 și explică scorurile legacy raportate atunci — 74,0% față de 66,3%.
  Investiția în mapare bate investiția în tokeni.

30 de orașe există în ambele ediții — baza pentru comparații an-la-an
(deja pe paginile de oraș ale site-ului) și pentru validarea încrucișată
propusă: o valoare 2026 nereparată a cărei pereche 2025 e verificată
poate fi prioritizată pentru re-citire prin plauzibilitate (fără a fi
vreodată corectată automat).

## 7. Ce a adăugat ediția 2024: achiziție istorică reproductibilă

Colectarea retroactivă a pornit la peste doi ani de la publicare. Din cele 42
de intrări (41 reședințe plus București), 41 au o sursă oficială verificată:
38 PDF-uri și trei registre Excel native. Arhivele oficiale au permis
recuperarea anexelor pentru Bistrița, Slatina și Vaslui, iar API-ul public și
vizualizatorul CityOn au rezolvat HCL 44 cu anexele Tulcei. Pentru
Drobeta-Turnu Severin, eMOL confirmă aprobarea prin HCL 39/12.02.2024, dar nu
publică documentul sau anexele; aceasta rămâne singura lipsă declarată în
manifest, fără înlocuire cu un document aproximativ. Inventarul complet,
inclusiv versiunea documentului și pagina de înregistrare, este în
`data/2024/SOURCES.md`.

Șase lecții noi:

- **Un nomenclator curent nu este dovadă istorică.** Anexele MF aflate în
  vigoare în 2024 au fost recuperate ca XLS, fixate prin SHA-256 și compilate
  într-un registru separat cu 2.106 poziții. Anul din calea corpusului selectează
  registrul; astfel un cod adăugat ulterior nu validează retroactiv o celulă.
- **„Excel nativ” nu înseamnă o singură schemă.** Slobozia și Timișoara
  publică foi apropiate de formularul MF; Satu Mare publică un buget general
  consolidat în care codurile de rând nu sunt coduri de clasificație. Cititorul
  selectează numai coloana explicită `Bugetul local`, mapează capitolele prin
  numele oficial și nu inventează clasificări pentru rândurile narative.
- **Sursa și rezultatul trebuie păstrate separat.** `buget_orig.xls[x]` rămâne
  registrul municipal byte-for-byte; `budget_file.xlsx` este proiecția
  normalizată în mii lei. Ambele au hash separat în bundle.
- **PDF-urile brute nu trebuie să umfle istoricul Git.** Cele aproximativ 372 MB
  sunt reproduse de downloader din URL-uri oficiale și verificate față de
  `checksums.sha256`; site-ul trimite la sursa oficială, nu la o cale GitHub
  inexistentă. Numai sursele Excel native și ieșirile normalizate sunt
  versionate.
<!-- BEGIN GENERATED:2024_LESSONS_METRICS -->
- **Achiziția completă nu înseamnă automat calitate uniformă.** Conversia
  deterministă a tuturor celor 41 de intrări disponibile a produs 66.328 de
  linii, iar recuperarea P2 selectivă a adăugat 1.515 celule strict
  verificate pentru 2,9424 USD. Mediana ratei stricte observate este 81,6% și 13
  intrări rămân sub 70%. Schema 3 separă anexele și investițiile, iar procentul
  rămâne consistență pe ieșirea extrasă, nu recall complet; numărul absolut de
  celule și fixture-urile exhaustive trebuie citite împreună cu el.
- **Absența graficului este și ea un rezultat de calitate.** Ediția publică 40
  de pagini municipale și 38 de tabele de capitole, dar numai 11 blocuri
  complete de grafice trec poarta de acoperire 90–110% față de totalul
  tipărit. Analiticele transversale păstrează 27 de municipiu-ani eligibili
  pentru comparația planului; restul rămân vizibili cu motivul excluderii, fără
  estimări care să umple golurile.
<!-- END GENERATED:2024_LESSONS_METRICS -->

## 8. Checklist pentru campania 2027

1. Așteaptă legea bugetului de stat; pornește colectarea la 1–2
   săptămâni după — recolta e concentrată în ~3 săptămâni.
2. La achiziție, pentru fiecare oraș: anexa aprobată (nu broșura, nu
   doar HCL-ul), anunțul de dezbatere + data, HCL nr./data, sursa
   arhivată în manifest cu sumă de control; triaj imediat — layout
   „unknown" generalizat = verificare manuală.
3. Preferă formate mașină-lizibile unde există (XLSX); cere prin
   544/2001 unde nu există nimic.
4. Actualizează nomenclatorul (`bgconvertor nomenclator update`) —
   clasificația în vigoare pentru 2027 poate diferi.
5. Verifică ID-urile și prețurile modelelor; rulează faza de fum
   (~$0.50) înainte de batch-uri.
6. Convertește cu presetul economic validat (astăzi:
   `google:gemini-3.6-flash`); păstrează referința premium pentru
   orașele unde procentul contează.
7. Planifică de la început a doua trecere: înlocuirea proiectelor cu
   anexele aprobate + reconvertirea țintită (proveniența per linie
   spune exact unde merită un model mai bun).
8. ~~Rezervarea bugetului la lansarea apelului și ordonarea fallback-ului după
   randament~~ (implementate; planner comun pe fișier, benzi dense și
   escaladare premium rezervată worst-case).
9. Reconciliere factură↔registru după fiecare campanie: `bgconvertor
   costuri --csv` lângă exportul de facturare al furnizorului.
10. Triaj complet pentru fiecare an, chiar și la orașe „cunoscute" —
    calitatea documentului variază de la an la an mai mult decât de la
    oraș la oraș.
11. Când un oraș lipsește pe anul curent, verificați edițiile anterioare
    înainte de a-l declara indisponibil: aceeași primărie a publicat
    adesea un fișier utilizabil în alt an.

---

<!-- BEGIN GENERATED:2024_LESSONS_FOOTER -->
*Document viu — se actualizează pe măsură ce corpusul crește. Ultima
actualizare: 28 august 2026, după campania de calitate a ediției 2024:
41/42 intrări convertite, 40 de pagini municipale de analiză, 66.328 de linii,
2,9424 USD cost API real și o singură sursă indisponibilă declarată. Auditul public trece
pentru toate cele 110 conversii existente din 2024–2026, fără neconcordanțe de
bundle; rezultatele detaliate și limitele metricilor sunt în
[`data/2024/README.md`](../data/2024/README.md).*
<!-- END GENERATED:2024_LESSONS_FOOTER -->
