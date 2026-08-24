# Arhitectură

Distilat din jurnalul de dezvoltare al proiectului (`PLAN.md` conține
istoricul complet, inclusiv măsurătorile și direcțiile abandonate).

## Problema

Bugetele locale românești sunt publicate ca anexe PDF la hotărârile de
consiliu: unele generate digital, cu grile trasate, multe scanate — rotite
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
profile -> [digital grid | orient -> OCR(docling) -> layout mappers]
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

## Modelul de verificare

Problemele sunt tipizate (`V1` validitatea codului … `V7` igienă), cu
severități, iar fiecare linie poartă proveniența: pagina, sursa
(`digital`/`ocr`/`llm`). În export, `verified=true` înseamnă că linia nu
poartă nicio problemă, inclusiv `warning` sau `info`. Metrica agregată
`observed_strict_line_rate` are ca numitor numai liniile extrase și declară
explicit `recall_measured=false`; nu poate demonstra rândurile absente.
Contractul complet este în [quality.md](quality.md).

## Publicarea artefactelor

În corpus, Excelul, `analysis.json` și blocul `conversion` din manifest sunt
un singur bundle versionat. Excelul și analiza sunt produse în fișiere
temporare cu același ID; manifestul, scris atomic ultimul, înregistrează
SHA-256-ul sursei și al ambelor artefacte. Un eșec restaurează versiunea
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

## Rezultate negative măsurate (păstrate intenționat)

- Straturile de text încorporate din PDF-urile de copiator păreau
  utilizabile, dar au obținut scoruri **mai slabe** decât re-OCR-ul la
  curățenia validată (−8pp pe testul A/B de la Bacău) — livrate dezactivate
  implicit, în spatele opțiunii `prefer_native_text`.
- Un filtru cromatic de eliminare a ștampilelor nu a mișcat nicio ancoră de
  aur — OCR-ul citea deja prin ștampilele din corpus; livrat dezactivat
  implicit.

Harness-ul de evaluare combină ancore de aur pentru toate familiile cu
inventare exhaustive de celule pentru familiile migrate în P1
(`bgconvertor eval`). Raportează separat recall și precizie numai în scope-urile
inventariate integral; astfel ipotezele primesc cifre fără ca un eșantion să
fie prezentat drept acoperire completă.
