# Execuția bugetară trimestrială

Pe lângă bugetele *aprobate* (PDF-uri convertite), corpusul conține execuția
*realizată*: cât s-a încasat și cât s-a plătit efectiv, din rapoartele oficiale
**Forexebug** ale Ministerului Finanțelor.

Spre deosebire de PDF-uri, acestea sunt deja structurate — nu există risc de
extragere. Munca stă în două locuri: potrivirea codurilor pe clasificația
bugetară și obținerea linkurilor.

## Procedura pentru un trimestru nou

Rapoartele se aduc manual, când vrei — nimic nu rulează programat.

**1. Caută rapoartele pe portal.** Pe
[portalul ANAF](https://extranet.anaf.mfinante.gov.ro/anaf/extranet/EXECUTIEBUGETARA/Rapoarte_Forexe/):
raport `FXB-EXB-901` (execuție agregată la nivel de ordonator principal),
sector bugetar 02, perioada trimestrului, pentru fiecare CIF din corpus.
Căutarea cere CAPTCHA, deci pasul rămâne manual — iar URL-urile rezultate nu
se pot deriva: identificatorul lotului (`LOT690` la T1/2026, `LOT724` la T2)
e constant pe trimestru, dar numărul final de secvență e distinct pentru
fiecare raport.

**2. Pune fișierele în structura existentă**, cu numele standard:

```text
data/execution/<an>/<județ>/<oraș>/q<N>/forexebug_execution.xlsx
```

Directoarele de județ și oraș sunt cele din trimestrele anterioare.

**3. Rulează scriptul de populare și publicare:**

```bash
python3 scripts/publica_executie.py                      # verifică și raportează
python3 scripts/publica_executie.py --publica            # + commit și push
```

Scriptul verifică fiecare fișier (identitatea entității față de CIF-ul
cunoscut, data raportului față de trimestru, sumele de control ale
raportului), îl înregistrează cu checksum în `manifest.json`,
`verification.json` și `checksums.sha256`, regenerează instantaneele și
agregatul, apoi arată exact ce s-a schimbat. **Un fișier care pică o
verificare oprește publicarea** — nimic nu intră în corpus nemarcat.

După push, workflow-ul `pages` reconstruiește site-ul singur.

Starea corpusului, oricând:

```bash
uv run bgconvertor execution status --exec-dir data/execution/2026
```

Descărcarea prin URL rămâne disponibilă, dacă preferi să completezi
`source_url` în manifest: `python3 data/execution/2026/download.py --quarter N`.

## Cum se citesc datele

Rapoartele sunt **cumulate de la 1 ianuarie**: T2 înseamnă ianuarie–iunie, nu
doar trimestrul al doilea.

Valorile din raport sunt în **lei**; corpusul le normalizează în **mii lei**,
ca restul datelor.

Forexebug omite sufixul de buget din codurile funcționale și de venituri
(`510103`) și îl poartă în coloana sursei de finanțare. Maparea:

| Sursă | Buget | Sufix |
|---|---|---|
| A – integral de la buget | bugetul local | `.02` |
| B – credite externe | bugetul local | `.02` |
| C – credite interne | bugetul local | `.02` |
| D – fonduri externe nerambursabile | bugetul local | `.02` |
| E, F, G – venituri proprii | instituții autofinanțate | `.10` |

Deci `510103` + sursa `A` devine `51.02.01.03`. Site-ul raportează doar bugetul
local (sursele A–D), ca să fie comparabil cu bugetul aprobat.

## Verificări

- **suma de control a raportului**: totalurile tipărite (`TOTAL VENITURI`,
  `TOTAL CHELTUIELI`) trebuie să egaleze suma liniilor, cu toleranță 0,1%;
- **coduri neenumerate**: codurile de adâncime pe care anexele Ordinului
  1954/2005 nu le conțin (împrumuturi, prefinanțări — sub 0,5% din linii) se
  numără și se raportează, nu se inventează;
- **procent față de plan**: se afișează doar dacă raportul execuție/plan e
  plauzibil. Peste 130%, planul extras din PDF e considerat parțial și
  procentele se rețin pentru toate trimestrele — cifra oficială de execuție
  rămâne afișată.
