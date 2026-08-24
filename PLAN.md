# bgconvertor — Convertor PDF → Excel pentru bugetele locale din România

Analiză și plan de dezvoltare · 2026-08-21

> **Document istoric.** Procentele și numerele de teste de mai jos descriu
> etapele la data lor și nu sunt baseline-ul public curent. Contractul P0
> pentru metrici, bundle-uri și audit este în [docs/quality.md](docs/quality.md).

> **Stadiu (la sfârșitul zilei de 2026-08-21): Fazele 0–4 implementate.** Toate cele trei PDF-uri
> se convertesc cap-coadă (`bgconvertor convert <pdf> [--llm repair]`).
> Alba Iulia (digital): 100% curat la validare. Pitești + Arad (scanate):
> extrase cu docling cu corecția orientării, validate față de
> registru, reparate cu LLM sub un buget strict. Evaluare pe ancore de aur: 132/140
> pe toate cele 12 fixture-uri; 97 de teste offline. Vezi README.md pentru comenzi.
> Abateri notabile față de planul inițial: OCR-ul și maparea au fost separate în
> etape distincte, cu cache propriu; prompturile de reparare sunt transcriere pură (constrângerile
> rămân pe partea noastră — modelul raționaliza atunci când i se spunea suma așteptată);
> a fost adăugat un fallback LLM pe pagină întreagă pentru layouturile pe care TableFormer nu le poate structura;
> anexele de investiții/achiziții au fost clasificate ca fiind în afara sferei nomenclatorului.

## 1. Ce conțin de fapt cele trei PDF-uri

Toate cele trei fișiere au fost profilate programatic (număr de pagini, straturi de text, metadate, mostre randate).

| Fișier | UAT | Pagini | Tip | Familie de layout |
|---|---|---|---|---|
| `budget_file_ab.pdf` | Municipiul Alba Iulia | 70 | **Nativ digital** (PDF24), strat de text complet | „Buget detaliat" — cod + rând + TOTAL + credite stingere plăți + Trim I–IV + estimări 2027–2029 |
| `budget_file_ag.pdf` | Municipiul Pitești (Argeș) | 236 | **Scanat**, fără strat de text (re-tipărire Ghostscript a unei scanări) | Pagini de proză HCL, apoi tabele de buget (cod indicator + prevederi anuale + estimări), apoi liste de investiții rotite |
| `budget_file_ar.pdf` | Municipiul Arad | 333 | **Scanat**, fără strat de text (ieșire de copiator Konica Minolta) | Matrice „Buget general" rotită 90° în imagine; apoi tabele de detaliu pe capitole; apoi bugete pe instituții (`.10`), inclusiv școli individuale |

**569 din 639 de pagini nu au strat de text** — OCR-ul este problema dominantă, nu parsarea PDF-ului.

Pericole concrete observate în mostre:

1. **Rotire în interiorul imaginii** (`ar`): tabele landscape scanate în pagini portret. Docling *nu* corectează automat acest lucru (issue-uri deschise #1376, #2343) — un pas prealabil de deskew/rotire este obligatoriu.
2. **Ștampile oficiale suprapuse peste celule cu date** (`ag` p9: ștampilă peste coloana 65.02.04; `ar` p31, p151). OCR-ul va citi greșit sau va pierde acele cifre.
3. **Denumiri de indicatori trunchiate** la marginile coloanelor și valori tăiate/corectate (`ag`).
4. **Unități de document eterogene în același PDF**: proză de hotărâre HCL, tabele de buget conforme nomenclatorului (mai multe familii de layout), liste de obiective de investiții („Denumire obiectiv / surse de finanțare / verde-maro-mixt-neutru"), bugete de școli pe instituții cu etichete laterale verticale. Instrumentul trebuie să **segmenteze și să clasifice înainte de a extrage**.
5. **Chiar și PDF-ul digital este înșelător**: extragerea de text cu pypdf amestecă ordinea coloanelor (numărul de rând, codul și TOTAL se concatenează; coloanele numerice ies în ordinea din stream). Extragerea trebuie să fie bazată pe coordonate (cuvinte + poziții x/y), niciodată text simplu.
6. **Mai multe bugete în același fișier**: `ab` = buget local (p1–54) + buget centralizat instituții din venituri proprii (p55–70). Sufixul de cod `.02` vs `.10` le distinge; fiecare are serii SECȚIUNEA TOTAL / FUNCȚIONARE / DEZVOLTARE.
7. **Numere în format românesc** `1.234.567,89`, unitatea *mii lei*, marcaje `X` în unele celule de estimări (`ar`), linii memo negative (37.02.03), umpluturi `0,00` peste tot.

## 2. Nomenclatorul (Ordinul 1954/2005, în vigoare pentru 2026)

Sursa oficială, curentă și prelucrabilă automat este
**https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare** — anexe XLS/XLSX ale căror nume de fișiere includ data modificării și își schimbă URL-ul la fiecare actualizare (se scrapează pagina, nu se hardcodează URL-uri). Deja descărcate în [reference/nomenclator/](reference/nomenclator/):

- `Anexanr2_08052026.xlsx` — **Clasificația indicatorilor privind bugetele locale**: foaia „venituri bl. 2026" (567 rânduri, Capitol|Subcap.|Paragraf|Denumire) + foaia „ch. funct. bl. 2026" (172 rânduri, coduri funcționale de cheltuieli).
- `AnexanrIec_28052026.xlsx` — **Clasificația economică** (titlu/articol/alineat, ~1048 rânduri) — o singură clasificație comună pentru toate bugetele.
- `AnexanrI_29072026.xlsx`, `Cuprins2026.xls` — clasificația funcțională generală + cuprins.

Observații importante pentru validator:

- Copia de pe data.gov.ro este **înghețată la 2018** — a nu se folosi.
- Nu există nicio digitizare open-source întreținută; parsarea XLSX-urilor MF de către noi înșine (trivială cu openpyxl) este alegerea corectă.
- Gramatica codurilor: venituri `cc.02[.ss[.pp]]`; cheltuieli funcționale capitol/subcapitol/paragraf `65.02.04.01`; economic titlu/articol/alineat `10.01.01`. Sufixul `.02` = buget local, `.10` = instituții finanțate din venituri proprii. Pseudo-codurile de agregare `00.xx`, `49.90` (venituri proprii), `98.02`/`99.02` (excedent/deficit) **nu se află în Anexa 2** — ele există doar în formularele de raportare și trebuie adăugate separat.
- Reguli de agregare (coloana vertebrală aritmetică a validatorului):
  - `TOTAL VENITURI (00.01) = 00.02+00.15+00.16+00.17+45.02+46.02+48.02`; cascada completă 00.xx documentată în `reference/` (00.02=00.03+00.12 etc.).
  - `VENITURI PROPRII (49.90) = 00.02 − 11.02 − 37.02 + 00.15`.
  - capitol = Σ subcapitole; subcapitol = Σ paragrafe; economic: titlu = Σ articole = Σ alineate; grupa 01 = Σ titluri 10..65.
  - `TOTAL = SECȚIUNEA FUNCȚIONARE + SECȚIUNEA DEZVOLTARE`; secțiunile sunt legate prin `37.02.03` (negativ, funcționare) = −`37.02.04` (pozitiv, dezvoltare).
  - În layoutul `ab`: `TOTAL anual = Trim I + II + III + IV` — o sumă de control pe rând, extrem de valoroasă pentru detectarea erorilor OCR.
  - Excepții: liniile memo „din care:" nu se însumează niciodată; codurile `*)` apar doar în execuție; titlul 85 este negativ; codurile noi din 2026 marcate `*`/`**` (42.02.98, 54.02.18, 65.02.06, …) sunt valabile doar pentru 2026+.

## 3. Stiva de extragere — constatări și decizii

Rezumatul cercetării (ecosistemul docling, aug. 2026):

- **Pipeline-ul clasic docling** este extractorul primar potrivit: OCR interschimbabil (implicit **RapidOCR**, modelele latine PP-OCR v5/v6 acoperă româna `ro`; Tesseract `ron` este alternativa — EasyOCR este un fallback de calitate de 10× mai lent; **ocrmac/Apple Vision nu suportă româna** — de evitat), modul **TableFormer ACCURATE** este aproape de nivelul SOTA pe tabele financiare dense (~94% pe tabele complexe), și totul ajunge într-un `DoclingDocument` Pydantic fără pierderi, cu indici rând/coloană per celulă, spanuri și bounding box-uri, plus `TableItem.export_to_dataframe()`.
- Limitări docling de care trebuie ținut cont în proiectare: fără corecția rotirii în imagine (pre-rotim noi înșine; Tesseract OSD sau o euristică ieftină de profil de proiecție), fără scor de încredere OCR per celulă (doar note de încredere la nivel de pagină, începând cu v2.34 — folosite pentru rutarea paginilor), celule fuzionate/pierdute pe tabele foarte dense (atenuare cu `images_scale≈2.0`, `force_full_page_ocr=True` și testarea `do_cell_matching=False`).
- Debit: de planificat ~3–8 s/pagină pe CPU Apple Silicon pentru OCR + TableFormer ACCURATE (≈ 45–90 min pentru cele 569 de pagini scanate; paralelizabil per pagină).
- Alternative luate în calcul și respinse ca soluție primară: **marker/surya** (gestionează rotirea și are benchmarkuri bune, dar ponderile Open Rail-M restricționează utilizarea comercială), **unstructured** (fidelitate mai slabă a tabelelor), **camelot/tabula** (doar pentru PDF-uri digitale — deși camelot în mod lattice este o bună verificare încrucișată pentru `ab`), **Azure/Google Document AI** (cel mai bun prag de acuratețe + scoruri de încredere per cuvânt, dar serviciu gestionat cu costuri de furnizor — păstrat ca opțiune de rezervă), **extragere pură LLM-vision** (cea mai bună înțelegere semantică, dar halucinare neverificabilă a cifrelor pe tabele numerice dense — folosită ca *strat de validare/reparare*, nu ca extractor primar).

**Principiul arhitectural central: extragem cu unelte deterministe, verificăm cu aritmetică, reparăm cu LLM-ul.** Redundanța nomenclatorului (sume de control pe rânduri, sume de ierarhie, identități de secțiune) face ca o singură cifră citită greșit să rupă aproape întotdeauna o ecuație — oferindu-ne *detectare* de erori cu precizie mare și cost zero, astfel încât LLM-ul trebuie doar să *repare* celulele semnalate, nu să citească totul.

## 4. LLM: rol și alegerea modelului

LLM-ul (Claude API, SDK Python) este folosit pentru patru sarcini înguste, toate cu input vizual (decupaje de pagini randate) și ieșiri structurate validate cu Pydantic (`client.messages.parse()` / `output_config.format`):

1. **Clasificarea paginilor/documentelor** — etichetează fiecare pagină: proză HCL / tabel de buget (ce familie de layout, ce buget `.02`/`.10`, ce secțiune) / listă de investiții / altele. Ieftin, cu risc scăzut.
2. **Repararea țintită a celulelor** — pentru celulele semnalate de validator (sume rupte, zone acoperite de ștampile, celule goale unde OCR-ul a eșuat): se trimite o imagine decupată + vecinii, se cer cifrele. Reparația se verifică încrucișat prin re-rularea sumei.
3. **Canonicalizarea antetelor/denumirilor** — potrivirea denumirilor de indicatori extrase prin OCR (trunchiate, cu diacritice stricate) cu intrările oficiale din nomenclator atunci când codul însuși este deteriorat; mai întâi potrivire fuzzy pe șiruri, LLM doar pentru restul ambiguu.
4. **Extragere fallback pe pagină întreagă** — pentru paginile unde nota de încredere docling este POOR sau structura tabelului este inutilizabilă (de ex. cele mai proaste pagini din `ar`): extragere vizuală a întregii pagini în aceeași schemă Pydantic, marcată ca provenind din LLM în raportul de calitate.

**Recomandare de model** (prețuri Claude API, aug. 2026):

| Model | Input/Output $/MTok | Rol |
|---|---|---|
| **Claude Sonnet 5** (`claude-sonnet-5`) | $3/$15 (introductiv **$2/$10 până la 2026-08-31**) | Implicit pentru reparare + extragere fallback — cel mai bun raport acuratețe/cost pe viziune numerică densă |
| **Claude Haiku 4.5** (`claude-haiku-4-5`) | $1/$5 | Clasificarea paginilor; opțional prima încercare de reparare, cu escaladare la Sonnet |
| Batch API | **−50%** la orice model | Toate trecerile non-interactive (întregul pipeline este compatibil cu batch) |

Anvelopa de cost pentru acest corpus (569 de pagini scanate, ~2,5K tokeni de imagine + ~1,5K tokeni de ieșire/pagină): chiar și o trecere dublă **completă** cu Sonnet 5 prin Batch API este ≈ $6–12; modul intenționat de reparare țintită (LLM-ul atinge ~20–30% din pagini) este ≈ $2–4 per corpus. Modelul este un flag CLI — nimic hardcodat.

## 5. Arhitectura propusă

```
bgconvertor/
├── pyproject.toml            # uv-managed; typer CLI entry point
├── reference/nomenclator/    # official XLSX annexes (committed) → parsed cache
├── src/bgconvertor/
│   ├── cli.py                # typer app: convert / validate / nomenclator / report
│   ├── profilepdf.py         # stage 0: text-layer census, orientation, rendering
│   ├── classify.py           # stage 1: page → document unit + layout family
│   ├── extract/
│   │   ├── digital.py        # pdfplumber words+coords → rows (ab-type)
│   │   ├── scanned.py        # pre-rotate → docling (RapidOCR ro, TableFormer ACCURATE)
│   │   └── llm.py            # Claude structured-output repair + fallback extraction
│   ├── model.py              # Pydantic: BudgetDocument / BudgetSection / BudgetLine
│   ├── nomenclator.py        # XLSX → code registry + hierarchy + aggregation rules
│   ├── validate.py           # code checks, name fuzzy-match, sum engine, section identities
│   ├── export.py             # openpyxl: canonical sheets + annotated issues
│   └── report.py             # quality scorecard (per file / page / line)
└── tests/                    # golden pages from all 3 files, unit tests for sums/parsing
```

Pipeline-ul per PDF: `profile → classify → extract (per page, path chosen by class) → normalize → validate → [LLM repair loop, re-validate] → export + report`.

Modelul de date principal (`model.py`):

```python
class BudgetLine(BaseModel):
    code: str | None            # normalized dotted form "65.02.04.01"
    raw_code: str | None        # as printed, e.g. "65020401"
    name: str
    row_no: int | None          # "rând"
    kind: Literal["revenue", "expense_functional", "expense_economic", "rollup", "memo"]
    values: dict[str, Decimal]  # column key → value (mii lei), e.g. {"total_2026": ..., "trim1": ...}
    provenance: Provenance      # page, bbox, source: digital|ocr|llm, confidence
    issues: list[Issue]         # populated by validator
```

`Decimal` peste tot (niciodată float); parserul de numere în format românesc (`1.234,56`, `X`, `-`, celule goale) ca o singură funcție auditată.

### Validarea = măsurarea calității

Fiecare verificare emite un `Issue` tipizat cu severitate; raportul de calitate este o agregare a acestora:

- **V1 validitatea codului**: există în nomenclator (anexa potrivită pentru `.02`/`.10`), sau este un pseudo-cod de agregare cunoscut.
- **V2 concordanța denumirii**: scor rapidfuzz față de denumirea oficială (insensibil la diacritice); scor mic → canonicalizare LLM → tot mic → avertisment.
- **V3 sume de control pe rânduri** (layoutul `ab`): TOTAL = ΣTrim.
- **V4 sume de ierarhie**: Σ copii = părinte, sărind liniile memo/„din care", respectând codurile negative (37.02.03, titlul 85).
- **V5 identități de secțiune**: TOTAL = FUNCȚIONARE + DEZVOLTARE per indicator; 37.02.03 = −37.02.04; formula veniturilor proprii; cascada TOTAL VENITURI.
- **V6 verificări între documente**: totalurile bugetului local vs bugetul general (ar); cifrele principale din HCL vs totalurile din tabele (opțional, citite de LLM din proză).
- **V7 igiena extragerii**: celule neparsabile, coloane obligatorii goale, coduri duplicate într-o secțiune, nota de încredere docling la nivel de pagină.

Ieșirea Excel (openpyxl): un workbook per PDF — foi `Venituri` / `Cheltuieli` per buget și secțiune cu coloane canonice (cod, denumire, valori per coloană, sursă, încredere), o foaie `Probleme` care listează fiecare Issue cu referință de pagină/celulă și culoare de severitate, și o foaie `Sumar calitate` (statistici per pagină, % linii complet validate, rata de trecere a verificărilor de sumă, numărul de intervenții LLM, semnalări nerezolvate). Listele de investiții și bugetele de școli pe instituții merg în foi separate, etichetate clar (sunt în afara sferei stricte a nomenclatorului).

## 6. Robustețe și depanabilitate — integrate de la prima linie

Aceste fișiere sunt suficient de eterogene încât experimentarea este modul normal de dezvoltare. Obiectivul ingineresc este: **nicio rulare eșuată nu are voie să coste mai mult de o pagină de muncă și zero dolari LLM pentru diagnosticare.** Fiecare regulă de mai jos există pentru a preveni buclele lungi de eșec care ard timp de perete și tokeni API.

### 6.1 Unități de lucru la nivel de pagină, cu un depozit de rulări persistent

- Totul operează pe **o pagină o dată**; o „rulare" este doar o orchestrare peste unități per pagină.
- Fiecare etapă își scrie ieșirea ca JSON într-un depozit adresat prin conținut:
  `runs/<pdf-stem>/<stage>/<page>.json`, cu cheia `hash(pdf) + page + stage + hash(stage config + prompt version)`.
- Re-rularea este **întotdeauna incrementală**: tuplele (pdf, page, stage, config) finalizate sunt sărite. Schimbarea unui prompt sau a unei opțiuni docling invalidează doar etapa afectată, nu și pe cele din amonte.
- `--pages 1-10`, `--pages 9,31,151`, `--sample 12` (stratificat pe clasa paginii) permit rularea oricărui experiment pe o felie. Implicit, dezvoltarea se face pe o felie; rulările complete sunt explicite.
- **Fail-soft per pagină**: o excepție pe pagina 37 este prinsă, înregistrată ca artefact `PageFailure` (traceback + etapă + hash de config), iar rularea continuă. `--fail-fast` inversează acest comportament pentru depanare. Un sumar de rulare se încheie întotdeauna cu „N ok / M failed / K cached".

### 6.2 Artefacte de depanare, nu arheologie prin loguri

- `--debug` scrie, lângă JSON-ul fiecărei pagini: PNG-ul paginii randate, casetele de cuvinte OCR suprapuse pe imagine (o randare ieftină matplotlib/PIL), grila de tabel reconstruită și parsarea la nivel de rând. O pagină extrasă greșit se diagnostichează *deschizând două imagini*, nu re-rulând cu instrucțiuni print.
- Logare structurată (`logging` din stdlib + handler rich): `-v` = progresul etapelor per pagină, `-vv` = detaliu per decizie (alegerea granițelor de coloane, scoruri de potrivire fuzzy, intrările verificărilor de sumă). Logurile poartă contextul `(pdf, page, stage)` pe fiecare linie.
- Fiecare `Issue` și fiecare valoare extrasă poartă **proveniență** (pagină, bbox, sursă, hash de config) din prima zi — asta face posibile suprapunerile de depanare și adnotările Excel fără re-calculare.
- `bgconvertor inspect <pdf> <page>` — randează o pagină cu toate artefactele într-un folder și îl deschide; instrumentul principal al buclei de dezvoltare.

### 6.3 Garduri de protecție LLM: buget, cache, replay

- **Registru de cheltuieli**: fiecare apel API adaugă o înregistrare JSONL — scop, model, pagină, tokeni input/output, cost (din câmpurile `usage` ale răspunsului), durată. Fiecare rulare își afișează costul; `bgconvertor report` agregă cheltuielile istorice.
- **Buget strict**: `--max-llm-cost 2.00` (și `--max-llm-calls`) oprește trecerile LLM — niciodată pipeline-ul determinist — la atingerea limitei. Bugetul implicit este mic; mărirea lui este un act conștient.
- **Cache de apeluri**: răspunsurile sunt cache-uite după `hash(model + prompt version + image bytes + schema)`. Re-rularea unui experiment nu plătește niciodată din nou pentru un apel identic. Buclele de reparare sunt plafonate (maximum 2 încercări per celulă), iar o celulă care eșuează de două ori devine `UNRESOLVED`, niciodată o furtună de reîncercări.
- **`--llm off | repair | full`** cu `off` ca implicit în dezvoltare. Întregul pipeline trebuie să ruleze și să producă ieșire (cu mai multe semnalări `UNRESOLVED`) cu LLM-ul dezactivat.
- **Casete înregistrate**: perechile brute cerere/răspuns din apeluri reale sunt salvate ca fixture-uri; testele și dezvoltarea offline le redau. Niciun test nu atinge vreodată API-ul.
- Prompturile sunt fișiere versionate, nu șiruri inline — o schimbare de prompt este un commit diff-abil și o schimbare de cheie de cache.

### 6.4 Teste înaintea funcționalităților, măsurate, nu apreciate din ochi

- **Setul de fixture-uri de aur mai întâi** (Faza 0.5, mai jos): ~12–15 pagini alese manual, acoperind fiecare familie de layout și fiecare pericol (digital curat, suprapunere de ștampilă, text tăiat, matrice rotită, buget de școală, listă de investiții, proză HCL). Pentru fiecare: PNG-ul paginii + un JSON așteptat verificat manual. Acesta este corpusul față de care este punctat fiecare experiment.
- **Nucleu de funcții pure**: parserul de numere (`1.234,56`, `X`, `-`, celule goale, negative), normalizatorul de coduri (`65020401` → `65.02.04.01`), motorul de sume, potrivitorul fuzzy — toate fără efecte secundare, testate unitar exhaustiv (teste de proprietate hypothesis pentru parser: parse∘format = id).
- **`bgconvertor eval`**: rulează pipeline-ul pe paginile de aur și raportează precizia/recall la nivel de celulă față de JSON-ul așteptat, per familie de layout. Reglarea opțiunilor docling sau a prompturilor = rulezi eval, compari numerele. Fără „arată mai bine".
- **Teste snapshot per etapă** pe fixture-uri, astfel încât o schimbare în amonte care mută ieșirea din aval să fie vizibilă la review, nu descoperită în producție.
- Totul rulează offline în CI (casete + fixture-uri comise); singura comandă care atinge rețeaua este `nomenclator update`.

### 6.5 Configurația ca date

Un singur `RunConfig` (pydantic-settings): opțiuni docling, motor/limbi OCR, scară de randare, nume de modele, bugete, versiuni de prompturi. Serializat în fiecare director de rulare și inclus în hash-ul fiecărei chei de cache — astfel orice artefact poate răspunde la „ce setări te-au produs?", iar două rulări sunt comparabile prin diff-ul configurațiilor lor.

## 7. Fazele de dezvoltare

**Faza 0 — Fundații (1 zi)**
Proiect uv, schelet typer și **eșafodajul de robustețe din §6 înainte de orice cod de extragere**: depozit de rulări + cache la nivel de pagină, logare structurată, `RunConfig`, orchestrare fail-soft pe pagini, stub-uri pentru registrul de cheltuieli/bugetul LLM, scheletul comenzii `inspect`. Plus ingestia nomenclatorului: parsarea celor trei anexe XLSX într-un registru cache-uit (JSON) cu ierarhie + coduri de agregare adăugate + tabelul regulilor de agregare; `bgconvertor nomenclator update` re-scrapează pagina MF (numele fișierelor se schimbă la fiecare modificare; serverul are nevoie de UA de browser + reîncercări).

**Faza 0.5 — Fixture-uri de aur + harnașament de evaluare (½–1 zi)**
Alegerea manuală și verificarea a ~12–15 pagini din toate familiile de layout și pericolele; comiterea PNG-urilor de pagini + JSON-urilor așteptate; construirea `bgconvertor eval`. Tot aici, nucleul de funcții pure cu testele sale unitare/de proprietate (parserul de numere, normalizatorul de coduri, motorul de sume). De aici înainte, fiecare schimbare este punctată față de fixture-uri.

**Faza 1 — Calea digitală cap-coadă (1–2 zile)**
Fișierul `ab` → extragere bazată pe coordonate (cuvinte pdfplumber grupate în rânduri/coloane; liniile de riglaj sunt prezente, deci granițele x ale coloanelor sunt detectabile) → model → validator → Excel + raport. Aceasta exersează întregul schelet fără zgomot OCR și produce primul livrabil real. Dezvoltată și evaluată pe felii de fixture-uri (`--pages`), apoi o rulare completă pe 70 de pagini.

**Faza 2 — Calea scanată (2–4 zile)**
Integrarea docling (RapidOCR `ro`, `force_full_page_ocr`, ACCURATE, `images_scale=2`, accelerator MPS), pasul prealabil de orientare, clasificarea paginilor (euristici + Haiku), `ag` cap-coadă. Reglaj pe paginile-fixture cu ștampile/text tăiat folosind `eval` + suprapunerile de depanare; conectarea buclei de reparare LLM conduse de validator (decupare → ieșire structurată Sonnet → re-validare) în spatele registrului/bugetului/cache-ului din Faza 0, înregistrând casete ca fixture-uri pe măsură ce au loc apeluri reale.

**Faza 3 — Cazurile grele (2–3 zile)**
`ar`: gestionarea rotirii în imagine, layoutul matricial al bugetului general (semantică diferită a coloanelor), bugetele `.10` pe instituții, celulele cu marcaj `X`. Fallback LLM pe pagină întreagă pentru paginile pe care docling nu le poate structura. Extragerea listelor de investiții în foi laterale (best-effort, semnalate).

**Faza 4 — Consolidare (1–2 zile)**
Mod Batch API pentru trecerile LLM, procesare paralelă a paginilor, șlefuirea UX-ului CLI (progres, afișarea costului cumulat), README. (Reluabilitatea, cache-ul și modurile `--llm` există deja din Faza 0 — această fază doar le reglează.)

Total: aproximativ 8–12 zile lucrătoare până la un v1 robust.

## 8. Riscuri și atenuări

| Risc | Atenuare |
|---|---|
| Erori OCR de cifre care *nu* rup nicio sumă (celule compensatorii/izolate) | Raportare onestă: scorul de calitate numără liniile „confirmate aritmetic" vs „neverificate"; opțional, diff de extragere dublă (docling + LLM) la cerere |
| Ștampilele distrug cifre dincolo de posibilitatea de reparare | Promptul de reparare vede contextul rândului + constrângerile de sumă; dacă rămâne inconsistent → celula semnalată `UNRESOLVED`, niciodată ghicită în tăcere |
| Familii de layout dincolo de cele 4 găsite | Clasificatorul are o clasă `unknown` → pagina rutată la fallback-ul LLM + avertisment în raport |
| Modificări ale nomenclatorului în cursul anului | Comanda `nomenclator update` + registru ștampilat cu datele anexelor; raportul înregistrează ce versiune a validat fișierul |
| Performanța docling pe fișierul de 333 de pagini | Paralelism per pagină, cache reluabil; ~1h în cel mai rău caz este acceptabil pentru un CLI batch |
| Buclele de experimentare care ard timp/tokeni | §6 în întregime: rulări pe felii de pagini, cache la nivel de etapă, cache de apeluri LLM + buget strict de cost, casete offline, schimbări punctate prin eval |

## 9. Faza 5 — Extinderea la nivel de corpus (planificată 2026-08-21)

Motivația: al doilea lot de mostre (Bistrița, Oradea, Bacău) a confirmat o
variație largă de calitate, format și structură, iar scopul final este
**analiza între municipii**. Tema: rutarea fiecărui fișier pe calea cea mai ieftină
care îl gestionează, și transformarea integrării unui format nou într-o schimbare izolată, măsurată.

### Valul 1 — rutare și corectitudine (~3–4 zile)

**5.1 Comanda `triage` (verificare prealabilă)** — ~1 zi
Profilează toate paginile; orientează + OCR-izează un eșantion stratificat (~5 pagini); clasifică layouturile.
Raport: familiile de layout găsite, avertisment de layout necunoscut, note de calitate a scanării,
durată estimată + cost LLM, comandă recomandată. Stocat ca
`runs/<stem>/triage.json`. *Acceptare: triajul celor 6 fișiere curente corespunde
realității cunoscute; un layout nemaivăzut este semnalat ca necunoscut, nu mutilat în tăcere.*

**5.2 Registrul de layouturi** — ~1–2 zile
Refactorizarea ramificațiilor acumulate ale mapper-ului în `layouts/`: un modul per
familie = detector (grilă+text → încredere) + mapper (grilă → linii) + schemă de
coloane + hook-uri de identitate; un registru ordonat pe priorități face dispecerizarea. Portarea tuturor celor ~9
familii cunoscute cu testele lor. *Acceptare: evaluarea de aur ≥ 132/140 neschimbată;
adăugarea unei familii fictive nu atinge niciun cod partajat.*

**5.3 Generalizarea grilei digitale (Oradea)** — ~1–2 zile
Extractorul de grilă v2: orice N coloane riglate, semantica din cuvintele din antet (refolosind
vocabularul de antete de la calea scanată). Se adaugă fixture-uri Oradea. UAT-urile din România se grupează în jurul
câtorva furnizori de software de buget, deci fiecare șablon digital deblochează probabil multe
municipii cu cost OCR zero. *Acceptare: Oradea se convertește pe calea digitală
la nivel de curățenie digitală; Alba Iulia rămâne la 100%.*

### Valul 2 — viteză și calitate pe calea scanată (~2–3 zile)

> **Rezultatele Valului 2 (măsurate, 2026-08-21):** 5.4 construit și măsurat A/B pe
> Bacău — de 3× mai rapid, dar −8pp curățenie validată (stratul de text al copiatorului
> este corupt); livrat în spatele `prefer_native_text=False`, OCR-ul pe imagine rămâne
> implicit. 5.5 filtrul de ștampile construit și măsurat pe cele trei fixture-uri cu ștampile —
> nicio îmbunătățire a ancorelor (OCR-ul curent citește deja prin aceste ștampile);
> livrat în spatele `stamp_filter=False`. 5.6 livrat: timpii măsurați per fișier
> alimentează ETA-urile planului; orientarea adaptivă (prior de serie verticală cu
> verificări complete periodice) reduce costul orientării pe fișierele majoritar verticale.

**5.4 Calea stratului de text nativ pentru PDF-urile de copiator (Bacău)** — ~1 zi
Când o pagină are un strat de text încorporat, dar nu are grilă, i se dă docling-ului pagina PDF
direct (fără OCR pe pagină întreagă), astfel încât să folosească textul copiatorului + TableFormer.
Măsurat A/B pe fixture-uri noi Bacău. *Acceptare: acuratețe a ancorelor ≥ egală la
viteză ≥3× față de calea randare-și-OCR.*

**5.5 Preprocesarea scanărilor: filtru de ștampile + deskew** — ~1 zi
Ștampilele sunt cerneală albastră/violet saturată peste text negru: un filtru de crominanță HSV
înainte de OCR ar trebui să le șteargă aproape gratuit; se adaugă deskew pentru unghiuri mici.
Controlat prin config, măsurat pe ancorele „grele" acoperite de ștampile. *Acceptare: rata de trecere a
ancorelor grele se îmbunătățește fără regresii în altă parte.*

**5.6 Orientare adaptivă + ETA-uri învățate** — ~1 zi
Prior de orientare per fișier (după N pagini verticale consecutive, verificare prin sondaj
în loc de OCR complet pe 4 rotații; de luat în calcul clasificatorul de unghi din rapidocr).
run_stage înregistrează timpii reali în `runs/<stem>/timings.json`; planul/ETA
folosește istoricul măsurat în loc de constante (Bacău le-a expus pe ambele). *Acceptare:
orientarea de tip Bacău ≥3× mai rapidă; ETA în limita ±30% la re-rulări.*

### Valul 3 — costul LLM și analiza corpusului (~3–4 zile)

> **Rezultatele Valului 3 (2026-08-21):** 5.7 livrat — OCR-ul stochează acum extinderile
> y per rând, citirea de reparare decupează la rândurile grupului de sumă (măsurat live:
> input mediu 3.600 → 1.507 tokeni), recuperarea de celule se rutează la Haiku
> (`llm.cell_model`), iar `llm.batch=True` trimite citirile de reparare prin
> Batch API (−50%) prin același cache + registru. Decupajele se aplică paginilor
> OCR-izate după această schimbare (payload-urile mai vechi revin la pagina întreagă). Dezambiguizarea
> tipului pe bază de denumire a rezolvat ambiguitatea clasei 51.02 (ab fixat la
> 100% printr-un test de regresie). 5.8 livrat — `corpus export` (61.599 rânduri
> pe 6 municipii, 94% verificate aritmetic) și `corpus report`.

**5.7 Eficiență LLM: reparare pe decupaje + Batch API + rutarea modelelor** — ~2–3 zile
Păstrarea bbox-urilor per celulă în payload-ul OCR (schimbarea de câmp se programează odată cu un
lot planificat de re-OCR — invalidează cache-ul OCR); repararea trimite decupaje de rânduri
în loc de pagini întregi (de 5–10× mai ieftin, mai precis). `--llm-batch` trimite
seturile de fallback/reparare prin Batch API (−50%) pentru rulările nesupravegheate pe corpus.
Citirile de o singură celulă se rutează la Haiku. *Acceptare: repararea de tip Arad ≤ $2.5 cu
același număr de reparări aplicate pe o felie de evaluare.*

**5.8 Ieșiri de corpus: set de date consolidat + tablou de bord** — ~1–2 zile
`bgconvertor corpus export`: un set de date normalizat în format lung (CSV/Parquet)
pentru toate fișierele convertite — municipiu, document/buget, secțiune, tip,
cod, func_code, denumire, coloană, valoare, sursă (digital/ocr/llm), indicator de verificare
aritmetică, pagină. `bgconvertor corpus report`: tabel de calitate și cheltuieli
între municipii. *Acceptare: setul de date se încarcă în pandas și totalurile per municipiu
se reconciliază cu foaia Sumar a fiecărui workbook.*

Reguli transversale: fiecare element se livrează cu fixture-uri de aur + porți de evaluare; schimbările
de câmpuri care invalidează cache-ul (bbox-urile din 5.7) se grupează în loturi pentru a evita re-OCR-uri masive
neplanificate; fișierele-mostră noi alimentează fixture-uri pentru familiile lor pe măsură ce sosesc.

## 10. Faza 6 — Pregătirea lansării publice (planificată 2026-08-22)

Obiectiv: un repo public pe care un străin îl poate clona, rula pe PDF-ul
propriului municipiu și extinde cu un layout nou — fără să citească istoricul acestei sesiuni.
Repo-ul nu este încă sub git, deci istoricul se poate naște curat.

### P1 — trebuie să se întâmple înainte ca repo-ul să devină public (~4–5 zile, cu integrarea corpusului)

**6.1 Git + igienă (jumătate de zi)**
`git init`; primul commit conține doar cod. `.gitignore` acoperă deja
`.env`, `runs/`, `.venv`; se adaugă ieșirile `*.xlsx` și `corpus.csv`. Cheia API
din `.env` nu intră niciodată în istoric — și oricum se ROTEȘTE înainte de publicare
(a trăit într-un transcript de sesiune). Se adaugă `.env.example`. Se decide numele public
(păstrăm `bgconvertor` sau redenumim) înainte să existe remote-ul.

**6.2 Licență + notificări privind datele/aspectele legale (jumătate de zi)**
- Cod: MIT sau Apache-2.0 (Apache-2.0 recomandat — grant de brevet, uzual
  pentru unelte de date).
- `reference/nomenclator/*.xlsx`: publicații oficiale MF — se păstrează, cu un
  NOTICE citând pagina-sursă și avertismentul propriu al anexei („nu reprezintă
  temei legal"), plus `nomenclator update` drept cale de reîmprospătare.
- PDF-urile de buget și XLSX-urile convertite rămân ÎN repo, în arborele
  corpusului `data/` (decizie 2026-08-22; arborele, manifestul SIRUTA, sumele de control și
  download.py există deja). Verificarea realității stocării: corpusul are ~589MB pentru
  36/42 PDF-uri; **PDF-ul Sibiului are 156MB — peste limita strictă GitHub de 100MB** —
  iar fișierul de 61MB al Bucureștiului este comis de două ori (Ilfov + București).
  Abordare: git simplu pentru tot ce e sub 100MB (acceptăm un repo de ~0,7GB;
  documentăm clonarea grea + indicația `--filter=blob:none`); fișierele de peste 100MB
  NU se comit — `data/<year>/download.py` + sumele de control le descarcă
  (manifestul le marchează `oversize: true`); Bucureștiul se deduplică la o singură copie,
  cu ambele intrări din manifest arătând spre ea. Git LFS este soluția de rezervă dacă
  GitHub se plânge, dar lățimea sa de bandă gratuită de 1GB/lună moare sub clonări
  publice — se revizuiește doar cu un buget. La scară multi-anuală (~3GB+),
  PDF-urile se migrează la Releases/seturi de date HF și doar XLSX-urile rămân în arbore.
- `DISCLAIMER.md`: ieșirile sunt extrageri cu indicator de verificare, nu
  cifre oficiale; erorile rămân posibile; verificați foaia Probleme.

**6.3 Setul de documentație (1 zi)**
- Rescrierea `README.md` pentru străini: ce/de ce (1 paragraf + o captură de ecran
  a tabelului de plan și a unui workbook), pornire rapidă (uv, o comandă pe un PDF-mostră),
  designul în trei straturi în 10 rânduri (extragere deterministă →
  verificare aritmetică → reparare LLM plafonată la buget), tabel cu așteptările de cost,
  familiile de layout suportate, limitări. Engleza ca limbă primară; se menționează că ieșirea CLI
  este în română (utilizatorii săi sunt români).
- `docs/design.md`: distilat din acest PLAN (arhitectură, depozitul de rulări,
  metodologia de evaluare, deciziile măsurate, inclusiv rezultatele negative). PLAN.md
  însuși se mută la `docs/history.md` sau se scurtează — încadrarea de jurnal de sesiune
  („azi", „diseară") trebuie să dispară.
- `docs/adding-a-layout.md` — POVESTEA extensibilității: triajezi un PDF nou →
  inspectezi grilele → scrii modulul de layout + linia de înregistrare → adaugi un
  fixture de aur → `bgconvertor eval`. Se parcurge cu un exemplu real
  (familia transpusă a Bistriței).
- `docs/nomenclator.md`: gramatica codurilor, agregările, identitățile, sursele.
- `.env.example`, `CONTRIBUTING.md` (configurarea mediului de dezvoltare, porțile de teste/eval, regula
  „ab rămâne la 100%"), `CHANGELOG.md` început la v0.1.0.

**6.4 CI + consolidarea testării (1 zi)**
- GitHub Actions: `uv sync` + `ruff check` + `pytest` pe 3.12/3.13. Suita
  este deja sigură offline (casete; testele dependente de PDF-uri se sar când
  fișierele lipsesc) — CI rulează subsetul PDF-urilor mici comise, inclusiv
  fixarea ab-100% și evaluarea pe fixture-urile ale căror PDF-uri sunt comise.
- Teste noi prietenoase cu CI, care NU au nevoie de PDF-uri: **fixture-uri de grilă** per familie
  (grilele de text OCR ca JSON — transpusă, matrice, fără antet, cu cod combinat,
  în format american) care aserționează ieșirea mapper-ului; teste unitare de asamblare/validare pe
  documente sintetice (comutarea regiunilor, repararea trunchierilor, dezambiguizarea
  tipurilor); teste smoke CLI prin runner-ul typer.
- `ruff` (lint + format) adăugat la dependențele de dezvoltare și la configul pre-commit; o singură
  trecere de formatare peste întregul cod.

**6.5 Integrarea arborelui de corpus (1 zi) — precondiție pentru tot ce urmează**
- Rezolvarea coliziunii de chei din depozitul de rulări: fiecare fișier de corpus este `budget_file.pdf`,
  deci `runs/<stem>` trebuie să devină `runs/<relative-path-slug>` (de ex.
  `runs/2026-01-alba-1017-alba-iulia`). Shim de migrare pentru depozitele existente
  cu fișiere plate; mostrele plate `budget_file_*.pdf` se mută în
  arbore (ele SUNT reședințe de județ: ab=1017-alba-iulia, ar=arad, …), iar fixture-urile
  de aur/testele se re-îndreaptă spre căile `data/`.
- Manifestul devine sursa de identitate: rândurile din `corpus export` poartă
  `siruta`, `county_code`, `city` din `data/<year>/manifest.json` în loc de
  ghicire după numele fișierului; workbook-ul convertit ajunge LÂNGĂ PDF-ul său
  (`budget_file.xlsx`) și este comis.
- `triage`/`convert`/`report` acceptă o intrare de manifest (`--city 1017` sau o
  cale din arbore), pe lângă o simplă cale de PDF.

**6.6 Rulator de loturi (1 zi)**
`bgconvertor batch data/2026 [--group 5] [--llm repair --max-llm-cost N
per-file] [--only pending|failed]`: parcurge manifestul, procesează N orașe
odată (workeri de extragere în cadrul fiecăruia; group = granularitatea
commit/checkpoint), fail-soft per oraș, reluabil printr-un bloc `conversion_status`
scris înapoi în manifest (status, pct_clean, errors, spend,
converted_at, versiunea uneltei). Se încheie cu tabelul raportului de corpus. Proiectat
astfel încât un GitHub Action sau un om să poată rula „următoarele 5" în siguranță.

**6.7 Site GitHub Pages, varianta minimală (1–1,5 zile)**
`bgconvertor site build` → `site/` static din manifest + rezultatele
conversiilor: o pagină index (42 de reședințe de județ: badge de status, % curat, linkuri)
și câte o pagină per oraș convertit (totaluri principale venituri/cheltuieli per
secțiune, tabelul capitolelor principale doar din rândurile VERIFICATE, fișă de calitate,
proveniență/cheltuieli, linkuri de descărcare la xlsx + PDF-ul sursă, DISCLAIMER-ul).
Șabloane Jinja2, fără lanț de build JS; grafice ca SVG inline sau PNG static.
Publicare prin GitHub Actions (reconstruirea site-ului la schimbări în `data/**` → deploy
pe Pages). Profunzimea analizei per oraș dincolo de asta (per capita, an-la-an,
clasamente între orașe) este P2.

### P2 — la scurt timp după publicare (~2 zile, poate urma ulterior)

- **Șlefuirea documentației**: galerie de layouturi (câte o imagine de pagină randată per familie, cu
  mapper-ul său numit), FAQ (costuri, configurarea cheii, „PDF-ul meu are un layout nou").
- **Șabloane de issue-uri**: șablonul „municipiu/layout nou" care cere
  ieșirea `bgconvertor triage` + o pagină-mostră; șablonul de bug care cere
  `bgconvertor report`.
- **Împachetare**: publicare pe PyPI (`uv build`), limite inferioare fixate și un
  flag `--version`; Dockerfile opțional pentru lanțul de unelte OCR.
- **UX pentru cheia API**: mesaj la prima rulare care explică faptul că `--llm off` funcționează complet
  offline și ce adaugă o cheie; se documentează cheltuiala tipică per clasă de fișier.
- **Profunzimea analizei pe site**: cifre per capita (populația INS după SIRUTA),
  pagini de comparație între orașe (structura cheltuielilor pe capitole, clasamente),
  an-la-an odată ce un al doilea an ajunge în `data/`; un index cu harta județelor.
- **Automatizarea loturilor**: un GitHub Action programat/manual care rulează
  `batch --group 5` pe runneri? Probabil NU merită (OCR-ul are nevoie de ~30-60
  min-CPU/oraș, iar repararea LLM are nevoie de cheie ca secret) — se documentează rulările
  locale ca fiind calea intenționată, Action doar pentru reconstruirea site-ului + CI-ul de evaluare.

### Non-obiective explicite pentru prima versiune publică
GUI, serviciu găzduit, nomenclatoare din afara României, CI pe Windows (se documentează
macOS/Linux; docling funcționează pe Windows, dar netestat aici).

## 11. Rezumatul stivei recomandate

Python 3.12+/uv · **typer** (CLI) · **pypdfium2 + pdfplumber** (profilare/digital) · **docling** (scanate: RapidOCR-ro + TableFormer ACCURATE) · **pydantic v2 + pydantic-settings** (schemă + RunConfig peste tot) · **anthropic** SDK (Sonnet 5 reparare/fallback, Haiku 4.5 clasificare, Batch API, ieșiri structurate) · **rapidfuzz** (potrivirea denumirilor) · **openpyxl** (Excel) · **rich** (raport în terminal + logare) · **pytest + hypothesis** (teste unitare/de proprietate, teste snapshot, redare de casete, evaluare pe pagini de aur).
