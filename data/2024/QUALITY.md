# Campania de calitate 2024

Campania a pornit de la cele zece conversii care aveau sub 70% în snapshot-ul
public anterior. Toate cele 41 de surse disponibile au fost republicate cu
extractorul 48 și schema de calitate 3; numai Brăila, Deva și Zalău au primit
recuperare LLM, iar candidatul a fost acceptat numai dacă a mărit numărul de
celule numerice strict verificate.

| Indicator | Rezultat |
|---|---:|
| Ținte inițiale sub 70% | 10 |
| Ținte inițiale rămase sub 70% | 8 |
| Piloți P2 acceptați | 3/3 |
| Celule strict verificate adăugate de P2 | +1.515 |
| Apeluri API facturabile | 438 |
| Candidați cross-year pentru recitire | 792 |
| Cost real / buget autorizat | 2,2130 / 20 USD |
| Buget experimental rămas | 17,7870 USD |

## Matricea baseline → rezultat public

| Municipiu | Familie de problemă | Rată veche | Rată finală | Celule stricte finale | Δ celule stricte* | Mod | Cost USD |
|---|---|---:|---:|---:|---:|---|---:|
| Alba Iulia | `scanned_grid_and_numeric_ocr` | 68,8% | 64,0% | 1.844 | -259 | determinist | 0,0000 |
| Pitești | `continuation_headers_and_functional_context` | 58,8% | 77,9% | 15.876 | +1564 | determinist | 0,0000 |
| Bistrița | `continuation_sections_and_candidate_selection` | 36,2% | 90,3% | 4.813 | +2830 | determinist | 0,0000 |
| Brăila | `dense_scanned_tables_and_merged_rows` | 65,8% | 66,2% | 11.224 | +185 | P2 acceptat | 0,8311 |
| Buzău | `scanned_structure_and_numeric_ocr` | 58,3% | 54,4% | 1.889 | -73 | determinist | 0,0000 |
| Călărași | `scanned_structure_and_numeric_ocr` | 53,1% | 42,6% | 2.460 | +50 | determinist | 0,0000 |
| Deva | `low_contrast_scan_and_merged_cells` | 47,9% | 52,6% | 14.459 | +4996 | P2 acceptat | 0,9009 |
| Zalău | `collapsed_remainder_column_and_decimal_glyphs` | 40,3% | 57,5% | 2.121 | +857 | P2 acceptat | 0,4810 |
| Sibiu | `recovered_rows_expand_observed_denominator` | 57,6% | 48,0% | 2.263 | +125 | determinist | 0,0000 |
| Vaslui | `scanned_structure_and_numeric_ocr` | 50,4% | 61,1% | 1.905 | -1063 | determinist | 0,0000 |

**Notă.** Baseline-ul folosește schema 2, iar rezultatul schema 3. Pentru intrările cu
anexe, diferența absolută include eliminarea anexelor din numitor și nu este o
măsură pură a recall-ului. `annex_lines` și `annex_numeric_cells` sunt publicate
separat în Excel, `analysis.json` și manifest. O rată finală mai mică poate
însoți mai multe rânduri recuperate, deoarece numitorul devine mai complet.

## Randamentul pilotului P2

| Municipiu | Celule stricte determinist | Celule stricte recuperat | Câștig | Cost USD | Celule/USD |
|---|---:|---:|---:|---:|---:|
| Brăila | 10.816 | 11.224 | +408 | 0,8311 | 490,9 |
| Deva | 13.382 | 14.459 | +1.077 | 0,9009 | 1195,5 |
| Zalău | 2.091 | 2.121 | +30 | 0,4810 | 62,4 |

Modelul a fost `google:gemini-3.6-flash`, cu plafon 3 USD/fișier și plafon
public absolut 5 USD/fișier. Costul este cel lifetime din ledger, nu doar costul
incremental al ultimei reluări din cache. Matricea machine-readable completă,
inclusiv bundle-urile și metricile deterministe intermediare, este
[`quality-campaign.json`](quality-campaign.json).

Lista [`recovery-candidates.csv`](recovery-candidates.csv) păstrează cele
792 de diferențe 2024↔2025 care au
prioritizat recitirea înaintea recuperării. Este o listă de outlieri pentru
revizuire, nu o sursă de adevăr: nicio valoare din ea nu este aplicată automat.

## Reproducere offline

```bash
uv run python data/2024/quality_campaign.py --check
uv run bgconvertor eval --min-anchors 148 --min-text-assertions 19 \
  --require-cell-ground-truth 10 --min-layout-cell-recall 90 \
  --min-layout-cell-precision 99.5
uv run bgconvertor corpus audit data --strict --require-modern
```

Cele patru pagini noi Bistrița p30, Brăila p167, Deva p226 și Zalău p24 sunt
fixture-uri OCR sanitizate și rulează fără PDF-uri brute, chei API sau cache-uri
private. PDF-urile sunt excluse din Git; URL-urile oficiale și SHA-256-urile
rămân în manifestul și verificarea ediției.
