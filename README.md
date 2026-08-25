# Bugete locale România

Bugetele locale ale municipiilor reședință de județ, extrase din PDF-urile
oficiale în Excel și format analizabil — cu proveniență și verificări explicite.

Acest depozit conține un corpus deschis al bugetelor locale din România
(PDF-uri oficiale + fișiere Excel/seturi de date validate, codificate
SIRUTA) și **bgconvertor**, instrumentul care îl construiește: convertește
PDF-urile bugetare în date validate, gata de analiză — chiar și atunci
când PDF-ul este o scanare rotită și ștampilată, scoasă la copiator.

Fiecare linie extrasă este verificată față de clasificația oficială a
indicatorilor privind finanțele publice (**Ordinul MFP 1954/2005**,
anexele în vigoare pentru anul bugetar) și față de aritmetica pe care
bugetul însuși trebuie să o respecte (sume trimestriale, ierarhii de
capitole, identități între secțiuni). Ce nu poate fi verificat este
*marcat, niciodată ghicit*.

`data/<an>/` conține PDF-urile bugetare oficiale ale municipiilor
reședință de județ (codificate SIRUTA, cu sursa fiecărui fișier) alături
de fișierele Excel convertite; site-ul generat publică o pagină de analiză
pentru fiecare buget convertit.

Datele augmentate (populație, suprafață, execuție bugetară și viitoare surse
de referință) se leagă prin SIRUTA + an și rămân separate de faptele extrase
din PDF. Nu suprascriu valori din document; analizele implicite folosesc doar
linii strict verificate și afișează acoperirea disponibilă.

## Cum funcționează — trei straturi

1. **Extragere deterministă.** PDF-urile digitale sunt citite din grila
   liniată, pe coordonate (mai multe variante de format detectate
   automat). Scanările trec prin detecția orientării (0/90/180/270°) și
   prin OCR-ul docling + TableFormer, apoi printr-un registru de mapări de
   format (tabele transpuse, matrice de buget centralizat, pagini de
   continuare fără antet, …).
2. **Verificare aritmetică.** Redundanța nomenclatorului — sume de control
   pe linie, capitol = Σ subcapitole, identități între secțiuni, formule
   de compoziție tipărite — transformă aproape orice cifră citită greșit
   într-o inconsistență detectabilă, la cost zero.
3. **Reparare LLM cu buget limitat** *(opțional)*. Celulele care au rupt
   aritmetica sunt recitite din imaginea paginii de un model configurat; o
   reparare este acceptată **doar dacă face sumele să se închidă**.
   Transcrierea integrală a paginii acoperă formatele pe care OCR-ul nu le
   poate structura. Un planner consumă plafonul întâi pe recuperările cu cel
   mai mare câștig estimat per dolar; registrul rezervă costul worst-case al
   fiecărui apel/retry înainte de rețea, iar cache-ul permite reluări gratuite.

## Pornire rapidă

Necesită Python 3.12+, [uv](https://docs.astral.sh/uv/) și ~2GB pentru
modelele OCR la prima rulare.

```bash
uv sync

# verificare prealabilă: ce este acest fișier, cât va costa?
uv run bgconvertor triage data/2026/01-alba/1017-alba-iulia/budget_file.pdf

# conversie (complet offline — nu necesită cheie API)
uv run bgconvertor convert data/2026/01-alba/1017-alba-iulia/budget_file.pdf

# cu OCR paralel și reparare LLM (necesită ANTHROPIC_API_KEY, vezi .env.example)
uv run bgconvertor convert <pdf> --workers 4 --llm repair --max-llm-cost 3.00

# raport de calitate/cost, inspecție per pagină, evaluare pe fixture-uri etalon
uv run bgconvertor report <pdf>
uv run bgconvertor inspect <pdf> <pagina>
uv run bgconvertor eval

# un set de date normalizat pentru toate fișierele convertite
uv run bgconvertor corpus export corpus.csv
uv run bgconvertor corpus report

# indicatori comparabili + proveniență: JSON, CSV și Excel
uv run bgconvertor corpus analytics --data-dir data --out-dir analytics

# coerența Excel + analysis.json + manifest (și hash-urile bundle-urilor noi)
uv run bgconvertor corpus audit data --json-out artifact-audit.json
```

Fișierul Excel rezultat conține foi de date pentru fiecare document
bugetar și secțiune, o foaie „Probleme" care localizează fiecare problemă
(pagină + cod + coloană) și un scor de calitate în „Sumar calitate". Pentru
fișierele corpusului, Excelul, analiza și manifestul sunt publicate ca un
singur bundle cu ID comun și hash-uri SHA-256.

## Alegerea modelelor LLM

Straturile LLM pot rula pe preseturi predefinite «furnizor:model» —
Anthropic (implicit), OpenAI, Google, Mistral, Qwen. Lista completă, cu
prețuri: `bgconvertor models`.

```bash
uv sync --extra vendors   # necesar o singură dată pentru furnizorii non-Anthropic
uv run bgconvertor convert <pdf> --llm repair --model-preset google:gemini-3.6-flash
```

Recomandări din evaluarea pe corpus (5 orașe × 12 preseturi, 65 de
rulări — [docs/eval-modele.md](docs/eval-modele.md)):

- **`anthropic:claude-sonnet-5`** *(implicit)* — cele mai multe linii
  verificate în absolut (~740 în evaluare); alegerea când calitatea
  primează, ~$10 pe cele 5 orașe de test.
- **`google:gemini-3.6-flash`** — aproape același randament (~700 linii)
  la ~40% din cost; alegerea pentru conversii în masă.
- **`openai:gpt-5-mini`** — cel mai bun raport linii/dolar (~230 față de
  ~160 la flash și ~70 la implicit), dar recuperează mai puțin în
  absolut; potrivit când bugetul, nu acoperirea, e constrângerea.
- Modelele premium (fable-5, opus-5) merită doar cu plafoane generoase pe
  fișiere mici: la buget fix, costul lor per apel le lasă fără pagini.

Notă de cost — două capcane descoperite pe facturi reale: unii furnizori
(Gemini) facturează tokenii interni de „gândire" fără să-i afișeze în
răspuns (69% din outputul facturat, chiar cu `reasoning_effort: low`), iar
prețurile de listă se schimbă. Registrul le contorizează acum corect, dar
verificați prețul din `ledger.py` pe factura primei zile de rulare,
reconciliați cu `bgconvertor costuri --csv` și setați bugete de alertă la
furnizor ca plasă independentă.

Poarta de acceptare aritmetică face alegerea sigură: un model mai slab
repară mai puține grupuri, dar nu poate corupe datele — o corecție se
aplică doar dacă face sumele să se închidă. Cheile API per furnizor sunt
în `.env.example`.

## Calitate, țintă și cost

| Tip de fișier | Țintă API | Regula de publicare |
|---|---:|---|
| Digital, format suportat | 0 $ | toate paginile procesate, bundle auditat |
| Scanare, format suportat | mediană ≤3 $ | LLM numai țintit |
| Scanare dificilă | plafon dur ≤5 $ | ce nu poate fi demonstrat rămâne marcat |

Scorul curent este `observed_strict_line_rate`: procentul liniilor **deja
extrase** fără nicio problemă (`error`, `warning` sau `info`). El nu măsoară
rândurile/celulele omise și nu trebuie citit ca recall al conversiei. Ținta de
produs este ≥90% `validated_cell_recall` pentru fiecare familie suportată,
după construirea unor etaloane exhaustive. Definițiile, porțile și auditul
baseline sunt în [docs/quality.md](docs/quality.md); limitările de utilizare,
în [DISCLAIMER.md](DISCLAIMER.md).

Poarta P1 acoperă acum toate cele nouă familii numerice reprezentate în suita
golden: 1.355/1.355 celule regăsite și corecte în scope-urile exhaustive.
Aceasta este acoperire pe pagini reprezentative, nu recall măsurat pentru
fiecare PDF complet. P2 prioritizează recuperarea LLM sub același plafon
public de 5 USD/PDF și publică planul de cheltuire în run store.

P3 publică un set analitic separat (`analytics.json`, `.csv`, `.xlsx`) și
comparații pe site. Fiecare municipiu-an păstrează intrările extrase,
augmentările și indicatorii derivați în câmpuri distincte; intrările
necomparabile rămân vizibile cu motivul excluderii. Populația folosită drept
numitor este cohorta unică RPL2021 INS, asociată prin SIRUTA. Contractul și
limitele sunt în [docs/analytics.md](docs/analytics.md).

## Corpusul

`data/2026/` — PDF-urile bugetare oficiale ale celor 41 de municipii
reședință de județ plus București, organizate ca
`<județ>-<slug>/<siruta>-<oraș>/budget_file.pdf`, cu un manifest (surse,
sume de control, status). Un fișier depășește limita de 100MB a GitHub și
este descărcat de `data/2026/download.py`. Documentele sunt acte
administrative publice; atribuirea fiecărui fișier este în manifest și în
[NOTICE](NOTICE).

## Extinderea la un format nou

Municipiile folosesc furnizori diferiți de software bugetar, iar formate
noi apar mereu. Pipeline-ul este construit pentru asta: rulați `triage`,
inspectați grilele mapate greșit, adăugați un modul de mapare în
`src/bgconvertor/layouts/`, adăugați un fixture etalon și blocați regresiile
cu `bgconvertor eval`. Ghidul complet este în
[docs/adding-a-layout.md](docs/adding-a-layout.md).

## Dezvoltare

```bash
uv run pytest            # suită de teste offline (testele LLM redau casete înregistrate)
uv run bgconvertor eval  # ancore + recall/precizie în scope-urile exhaustive
uv run bgconvertor corpus audit data --strict --require-modern  # poarta de release
```

Trei porți obligatorii pentru orice modificare: suita de teste trece,
evaluarea pe ancora etalon nu regresează (fișierul digital de referință
trebuie să rămână 100% curat — este fixat printr-un test), iar auditul strict
al bundle-urilor publice trece. Vezi [CONTRIBUTING.md](CONTRIBUTING.md) și
[docs/design.md](docs/design.md).

## Licență

Apache-2.0 pentru cod ([LICENSE](LICENSE) — textul licenței rămâne în
engleză, fiind versiunea canonică). Datele terților incluse — anexele de
clasificație ale Ministerului Finanțelor, PDF-urile bugetare municipale,
codurile SIRUTA — sunt creditate în [NOTICE](NOTICE).
