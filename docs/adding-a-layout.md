# Adăugarea suportului pentru o machetă nouă

Primăriile cumpără software bugetar de la furnizori diferiți, și fiecare
furnizor tipărește tabelele altfel. Acest ghid pas cu pas este drumul
recomandat de la „PDF-ul orașului meu se convertește prost" până la un fix
integrat și măsurat — folosind ca exemplu povestea reală a machetei
transpuse de la Bistrița.

## 1. Întâi triajul — nu începe niciodată cu o conversie completă

```bash
uv run bgconvertor triage path/to/budget_file.pdf
```

Triajul profilează fiecare pagină, trece ~5 pagini eșantionate prin
pipeline-ul real (munca este pusă în cache și refolosită mai târziu) și
raportează familiile de machete găsite, calitatea OCR și o estimare
onestă de cost. Un avertisment de forma

```
⚠ layout necunoscut in esantion: scan_table_other
```

înseamnă că există pagini pe care niciun mapper nu le revendică — aceea
este ținta ta.

## 2. Uită-te la grilele propriu-zise

Etapa de OCR stochează fiecare tabel recunoscut ca o grilă de text simplu.
Inspectează o pagină cu probleme:

```bash
uv run bgconvertor inspect path/to/budget_file.pdf 2
```

sau programatic:

```python
from bgconvertor.config import RunConfig
from bgconvertor.runstore import RunStore
store = RunStore(RunConfig(), Path("path/to/budget_file.pdf"))
grid = store.get("ocr", 2)["tables_raw"][0]
for row in grid[:12]:
    print(row)
```

Pentru Bistrița, asta a arătat ceva neobișnuit: *perioadele* erau rânduri,
iar *indicatorii* erau coloane —

```
['2029',      '7470,00', '4000,00', ...]
['Trim. IV',  '1.386,00', '833,00', ...]
['TOTAL',     '7.145,00', '3.833,00', ...]
['Cod',       '070202',  '07020201', ...]
['Denumirea indicatorilor', '', 'Impozitul pe terenul...', ...]
```

un tabel transpus: câte o coloană pe indicator, cu rândul de coduri și
rândurile de denumiri rupte pe mai multe linii jos de tot.

## 3. Scrie mapperul

Mapperele de machete trăiesc în `src/bgconvertor/layouts/`, câte un modul
per strategie. Un mapper este o funcție `try_map(grid) -> list[dict] | None`
— returnează `None` când grila nu are forma ta (registrul încearcă atunci
următorul mapper; mapperul generic de tabel condus de antet este ultima
soluție garantată).

Emite linii conform contractului de extracție (documentat în
`eval_harness.py`): `raw_code`, `code` normalizat, `name`, `section`,
`values` (șiruri zecimale canonice indexate pe coloană: `total`,
`trim1..4`, `est2027..29`, …). Folosește funcțiile ajutătoare din
`layouts/common.py` — `mk_line`, `parse_cell` (tolerantă la convenția
locală de scriere a numerelor și la zgomotul de OCR), vocabularul comun de
antete.

Înregistrează-l în `layouts/__init__.py`:

```python
MAPPERS = [
    transposed.try_map,   # <- one line
    matrix.try_map,
    table.map_grid,
]
```

Detecția trebuie să fie conservatoare: un mapper care revendică grile pe
care nu le înțelege degradează alte municipalități. Ancorează-te pe o
semnătură structurală (pentru `transposed`: un rând `Cod` plus ≥4 rânduri
cu etichete de perioadă), nu pe ghicit.

## 4. Adaugă în repo un fixture de aur

Alege o pagină reprezentativă și verifică manual vreo duzină de celule față
de pagina PDF randată. Fixture-urile sunt JSON în `tests/fixtures/golden/`:

```json
{
  "id": "bistrita_p002",
  "pdf": "...", "page": 2, "layout": "scan_transposed_detail",
  "source_grid": "grids/bistrita_p002.json",
  "anchors": [
    {"raw_code": "070202", "column": "total", "value": "7145.00"},
    {"raw_code": "070202", "column": "trim4", "value": "1386.00"}
  ],
  "cell_ground_truth": [
    {
      "rows": [
        {
          "raw_code": "070202",
          "values": {"total": "7145.00", "trim4": "1386.00"}
        }
      ]
    }
  ]
}
```

Ori de câte ori se poate, alege ancore pe care aritmetica le confirmă
(TOTAL = Σ trimestre; capitol = Σ subcapitole) — atunci adevărul tău de
referință este demonstrat, nu doar apreciat din ochi. Marchează celulele
acoperite de ștampile sau degradate cu `"hard": true`.

`anchors` pot rămâne un eșantion mic pentru diagnostic. Pentru un layout
declarat suportat, `cell_ground_truth` trebuie însă să inventarieze fiecare
celulă numerică din scope-ul indicat. Forma compactă `rows` reutilizează
identitatea rândului pentru toate valorile sale. `context_contains`
disambiguizează codurile repetate și poate lipsi când scope-ul este întreaga
pagină. Forma explicită `cells` rămâne disponibilă pentru regiuni neregulate.
O grilă OCR distilată în `source_grid` face mapperul reproductibil în CI fără
PDF, OCR, rețea sau secrete. Nu include date din afara scope-ului exhaustiv în
calculul preciziei.

## 5. Validează cu eval, apoi cu suita de teste

```bash
uv run bgconvertor eval        # your fixture green, nothing else regressed
uv run bgconvertor eval --require-cell-ground-truth 9 \
  --min-layout-cell-recall 90 --min-layout-cell-precision 99.5
uv run pytest                  # includes the ab-stays-100%-clean pin
```

Dacă schimbarea ta a modificat rezultatul mapării pentru fișierele
existente, incrementează `extract_version` în `config.py` (asta invalidează
cache-ul ieftin de mapare, nu OCR-ul cel scump) și rulează din nou
`bgconvertor extract` pe fișierele din corpus înainte de a judeca eval-ul.

## 6. Ce trebuie să conțină un PR

- modulul mapperului + linia de înregistrare,
- fixture-ul de aur, scope-ul numeric exhaustiv și grila de regresie
  (+ eventualele indicii noi de clasificație),
- cifre înainte/după: estimarea de la triaj și `% curat` pentru fișierul
  țintă, recall/precizie pe celule și scorul de eval pentru tot restul.

Asta e întreaga buclă. Bistrița a trecut de la 31 de linii extrase la 163
(79% pe metrica legacy a liniilor extrase) exact prin acești pași; acest
procent nu reprezintă recall complet, vezi [quality.md](quality.md).
