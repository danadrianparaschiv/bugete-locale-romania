# Bugetele municipiilor reședință de județ — 2024

Acest director conține ediția 2024 a corpusului: cele 41 de municipii
reședință de județ și București, în aceeași structură folosită pentru 2025 și
2026.

```text
2024/
  <cod-județ>-<județ>/
    <siruta>-<municipiu>/
      budget_file.pdf       # sursă locală, exclusă din Git
      sau buget_orig.xls[x] # sursă Excel oficială, păstrată în Git
      budget_file.xlsx      # rezultat normalizat
      analysis.json         # analiză din același ConversionResult
```

## Descărcare și verificare

```bash
uv run python data/2024/download.py
uv run python data/2024/download.py --only 08,13,42
```

Scriptul acceptă PDF, XLS și XLSX, verifică semnătura reală, calculează
SHA-256 și scrie `verification.json`, `checksums.sha256` și `SOURCES.md`.
PDF-urile brute nu intră în istoricul Git; sursele Excel publicate nativ se
păstrează byte-for-byte sub numele `buget_orig.xls` sau `buget_orig.xlsx`.

Inventarul auditat acoperă 41 din 42 de intrări: 38 au PDF oficial și trei
au registru Excel nativ. Singura lipsă este Drobeta-Turnu Severin: registrul
eMOL confirmă HCL 39/12.02.2024, dar nu publică documentul sau anexele. Lipsa
este păstrată explicit ca `not_available`, nu înlocuită cu valori din presă
sau cu un document aproximativ.

## Politica surselor

Este preferat bugetul inițial aprobat și detaliat. Când anexa inițială nu mai
este disponibilă pe domeniul oficial, manifestul etichetează explicit o
rectificare aprobată ori un proiect oficial. București apare și ca reședință a
județului Ilfov, conform structurii corpusului; ambele intrări refolosesc
același document oficial.

Sursele istorice ascunse în arhive sunt descrise reproductibil: Bistrița
folosește membrul exact al unei arhive ZIP oficiale, Iași combină HCL-ul cu
publicația detaliată, iar Tulcea folosește ruta publică expusă de
vizualizatorul CityOn. `checksums.sha256` fixează conținutul obținut, inclusiv
atunci când adresa municipalității nu conține anul în cale.

Nomenclatorul de validare este versiunea Ministerului Finanțelor în vigoare în
2024, păstrată separat în `reference/nomenclator/2024/`.

## Conversie și publicare

```bash
# rulează numai conversia deterministă; nu cheltuiește buget API
uv run bgconvertor batch data/2024 --llm off --workers 8 --only pending

# verifică faptul că Excel, analysis.json și manifestul descriu aceeași rulare
uv run bgconvertor corpus audit data --strict --require-modern
```

Pentru o sursă Excel nativă, `buget_orig.xls[x]` rămâne artefactul oficial,
iar `budget_file.xlsx` este ieșirea normalizată. Valorile tipărite în lei sunt
convertite explicit în unitatea comună `mii lei`, cu proveniența
`native_excel`; codurile afișate prin formatare numerică Excel sunt păstrate ca
text înainte de validare. Cele două fișiere nu sunt interschimbabile și au
hash-uri separate în bundle-ul public.

## Rezultatul publicat

<!-- BEGIN GENERATED:2024_EDITION_METRICS -->
Rularea deterministă din 27 august 2026 a convertit toate cele 41 de intrări
cu sursă disponibilă, fără apeluri LLM și cu 0 USD cost API. București apare în
manifest atât ca reședință pentru Ilfov, cât și în poziția separată a
municipiului București; cele două intrări folosesc aceeași sursă verificată și
au produs bundle-uri identice ca date. Drobeta-Turnu Severin rămâne singura
intrare fără document publicabil.

| Indicator auditat | Rezultat |
|---|---:|
| Intrări în manifest | 42 |
| Surse oficiale verificate și convertite | 41 |
| PDF / Excel nativ | 38 / 3 |
| Scope-uri de pagină procesate complet | 41/41 |
| Pagini PDF din scope-urile manifestului | 4.174 |
| Linii extrase / strict verificate | 73.428 / 56.049 |
| Celule numerice / strict verificate | 223.278 / 171.868 |
| Mediana `observed_strict_line_rate` | 84,7% |
| Intrări cu rată strictă ≥90% / ≥70% | 14 / 31 |
| Pagini municipale cu analiză publică | 40 |
| Municipiu-ani eligibili pentru comparația planului | 24 |
| Cost API incremental | 0 USD |

Cele 40 de pagini municipale provin din 41 de intrări convertite deoarece
București este duplicat intenționat în manifest. 39 de intrări au un tabel de
capitole, iar 11 trec și poarta de acoperire necesară blocului complet de
grafice. Lipsa unui grafic nu este umplută prin estimare: pagina păstrează
tabelul disponibil și avertismentul de acoperire.

Aceste procente sunt rate de consistență pentru liniile și celulele deja
extrase, nu `validated_cell_recall`. Niciun etalon exhaustiv nu există încă
pentru toate cele 41 de documente; prin urmare ediția declară explicit
`recall_measured=false` și nu pretinde 90% recall la nivel de corpus. Poarta
`corpus audit data --strict --require-modern` verifică însă că toate cele 110
conversii existente din corpusul 2024–2026 sunt bundle-uri moderne coerente,
fără nicio neconcordanță între Excel, analiză și manifest.
<!-- END GENERATED:2024_EDITION_METRICS -->
