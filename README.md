# Bugete locale România

Bugetele locale ale municipiilor reședință de județ, extrase din PDF-urile
oficiale în format analizabil — cu verificare aritmetică a fiecărei linii.

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
   aritmetica sunt recitite din imaginea paginii de către Claude; o
   reparare este acceptată **doar dacă face sumele să se închidă**.
   Transcrierea integrală a paginii acoperă formatele pe care OCR-ul nu le
   poate structura. Un plafon ferm în dolari și un cache de răspunsuri
   guvernează fiecare apel; rulările se reiau gratuit.

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
```

Fișierul Excel rezultat conține foi de date pentru fiecare document
bugetar și secțiune, o foaie „Probleme" care localizează fiecare problemă
(pagină + cod + coloană) și un scor de calitate în „Sumar calitate".

## Alegerea modelelor LLM

Straturile LLM pot rula pe preseturi predefinite «furnizor:model» —
implicit `anthropic:claude-sonnet-5`, cu o scară Anthropic de la
`claude-fable-5` (scanările cele mai grele) până la `claude-haiku-4-5`
(cel mai ieftin), plus furnizori alternativi: OpenAI, Google, Mistral
(rezidență UE a datelor), Qwen (greutăți deschise). Lista completă, cu
prețuri: `bgconvertor models`.

```bash
uv sync --extra vendors   # necesar o singură dată pentru furnizorii non-Anthropic
uv run bgconvertor convert <pdf> --llm repair --model-preset google:gemini-2.5-flash
```

Poarta de acceptare aritmetică face alegerea sigură: un model mai slab
repară mai puține grupuri, dar nu poate corupe datele — o corecție se
aplică doar dacă face sumele să se închidă. Cheile API per furnizor sunt
în `.env.example`.

## La ce să vă așteptați

| Tip de fișier | Rezultat tipic | Cost LLM |
|---|---|---|
| Digital, cu grilă liniată | 94–100% linii verificate | 0 $ |
| Scanare bună, formate cunoscute | 60–80% verificate | 1–4 $ |
| Scanare dificilă (ștampile, rotiri, OCR de copiator) | 55–70% verificate | 3–8 $ |

„Verificat" înseamnă că linia a trecut toate verificările de nomenclator
și aritmetice — stratul sigur de analizat fără a deschide PDF-ul. Restul
rămâne și el în rezultat, marcat cu motivul. Vezi
[DISCLAIMER.md](DISCLAIMER.md).

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
uv run bgconvertor eval  # scor la nivel de celulă față de fixture-uri etalon verificate manual
```

Două porți obligatorii pentru orice modificare: suita de teste trece, iar
evaluarea pe ancora etalon nu regresează (fișierul digital de referință
trebuie să rămână 100% curat — este fixat printr-un test). Vezi
[CONTRIBUTING.md](CONTRIBUTING.md) și [docs/design.md](docs/design.md).

## Licență

Apache-2.0 pentru cod ([LICENSE](LICENSE) — textul licenței rămâne în
engleză, fiind versiunea canonică). Datele terților incluse — anexele de
clasificație ale Ministerului Finanțelor, PDF-urile bugetare municipale,
codurile SIRUTA — sunt creditate în [NOTICE](NOTICE).
