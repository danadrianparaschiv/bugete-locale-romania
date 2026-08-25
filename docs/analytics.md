# Analitice și augmentare

P3 transformă corpusul public într-un set de date comparabil fără să amestece
sursele. Produsul rezultat are trei straturi explicite:

1. **fapte extrase** — totalurile și capitolele din `analysis.json`, publicate
   numai din bundle-uri PDF/Excel/manifest coerente;
2. **date augmentate** — populația rezidentă RPL2021, suprafața și execuția
   Forexebug, asociate prin SIRUTA + an și păstrate cu sursa lor;
3. **indicatori derivați** — lei/locuitor, densitate, sold, variație an-la-an
   și rang. Aceștia nu sunt fapte din PDF și pot fi recalculați din coloanele
   de intrare.

Niciun strat nu suprascrie altul. `analysis.json` rămâne analiza conversiei
unui singur PDF; analiticele sunt produse separat în `analytics.*`.

## Generare

```bash
uv run bgconvertor corpus analytics \
  --data-dir data \
  --out-dir analytics
```

Comanda citește numai fișierele comise din `data/` și
`reference/municipii.json`; nu rulează OCR și nu face apeluri LLM. Costul API
este 0 USD. Produce:

- `analytics.json` — schema completă, surse, acoperire, municipiu-an și
  capitolele de cheltuieli disponibile;
- `analytics.csv` — câte un rând pentru fiecare municipiu-an, inclusiv
  intrările excluse și motivul excluderii;
- `analytics.xlsx` — registru pentru analiză manuală, cu foi `Sumar`,
  `Municipii`, `Capitole` și `Surse augmentare`. Soldurile, densitatea și
  indicatorii pe locuitor sunt formule vizibile în Excel.

`bgconvertor site build` publică aceleași trei fișiere sub `site/data/` și o
pagină `comparatii.html` pentru fiecare ediție.

În paginile municipiilor, compoziția veniturilor și cea a cheltuielilor folosesc
aceeași vizualizare cu bare interactive. Selectarea unui capitol arată codul,
valoarea și ponderea lui în total; pentru venituri se păstrează și eticheta
sursei locale, de la stat sau din fonduri UE. Nu se afișează niveluri de detaliu
care nu există în liniile verificate ale documentului.

## Contractul de eligibilitate

Un plan intră în clasamentul cheltuielilor pe locuitor numai când toate sunt
adevărate:

- statusul conversiei este `converted`, iar auditorul a confirmat că
  artefactele descriu aceeași conversie;
- există `analysis.json` și un total de cheltuieli verificabil;
- există populația rezidentă pentru aceeași unitate SIRUTA;
- comparația cu execuția nu a declanșat `plan_incomplet` (de exemplu, un
  raport semestrial mult mai mare decât totalul presupus anual).

`plan_comparison_eligible=false` nu șterge rândul. Câmpul
`plan_exclusion_reason` explică de ce nu este clasat: `status_pending`,
`total_cheltuieli_lipsa`, `plan_incomplet_fata_de_executie` etc. Rangul și
numărul membrilor cohortei sunt publicate împreună; nu există rang fără
cohortă.

Execuția are un contract separat. Ea poate rămâne comparabilă chiar dacă
planul PDF este incomplet, deoarece valorile provin din raportul structurat
Forexebug. Clasamentul execuției cere aceeași perioadă de raportare în
corpusul curent; pentru ediția 2026 toate rapoartele disponibile sunt T2 la
30 iunie 2026. Câmpurile `execution_comparison_eligible` și
`execution_exclusion_reason` păstrează decizia auditabilă.

Capitolele din foaia `Capitole` sunt doar topurile verificabile publicate de
fiecare municipiu. Lista nu este exhaustivă și nu este folosită pentru a
pretinde clasamente pe același capitol între orașe.

## Populație și suprafață

Numitorul pe locuitor este populația **rezidentă la 1 decembrie 2021**, din
RPL2021 INS, tabelul 1.22, legată prin cod SIRUTA. Sursa oficială și URL-ul
fișierului sunt în blocul `sursa.populatie` din
`reference/municipii.json` și sunt propagate în `corpus.json`,
`analytics.json` și foaia `Surse augmentare`.

La introducerea P3, valorile au fost confruntate cu tabelul oficial. Au fost
corectate trei diferențe de câte un locuitor (Constanța, Ploiești, Sibiu) și
Suceava a fost readusă de la o valoare Wikidata din 2023 la cohorta comună
RPL2021 (84.322 locuitori). Astfel, nicio comparație per capita nu combină
date de populație din ani diferiți.

Suprafața este o augmentare Wikidata și este folosită numai pentru densitate,
nu pentru eligibilitatea clasamentului bugetar. Data și descrierea sursei sunt
publicate separat.

## Interpretare și limite

- Toate valorile bugetare absolute din fișierele analitice și din paginile și
  graficele municipiilor sunt afișate în **mii lei**. Indicatorii per capita
  rămân în **lei/locuitor**, iar vizualizarea „din fiecare 100 de lei” rămâne în
  lei deoarece exprimă o pondere, nu o valoare bugetară absolută.
- Valorile bugetare sunt nominale; variațiile între ani nu sunt ajustate cu
  inflația.
- Bugetul aprobat este un plan, execuția este mișcarea efectivă de bani. Cele
  două nu trebuie adunate și nu sunt interschimbabile.
- Per capita normalizează mărimea populației, dar nu face automat comparabile
  responsabilități administrative sau servicii cu arii diferite.
- `strict_line_rate` descrie liniile extrase, nu recall-ul întregului PDF.
  `recall_measured` rămâne un câmp separat și nu este dedus din procentul de
  linii stricte.
- Un clasament mic este o vedere asupra cohortei disponibile, nu asupra tuturor
  municipiilor. Pagina și fișierele publică întotdeauna numărul cohortei.

Aceste reguli fac ieșirea utilă pentru analize de bază fără a transforma
absența datelor sau o conversie parțială într-o concluzie numerică.
