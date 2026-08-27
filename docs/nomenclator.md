# Nomenclatorul (Ordinul MFP 1954/2005)

Validatorul verifică fiecare cod și fiecare sumă extrasă față de
„Clasificația indicatorilor privind finanțele publice" oficială, publicată
ca anexe XLSX la <https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare>.
Numele fișierelor de anexă încorporează data modificării, iar URL-ul se
schimbă la fiecare actualizare — `bgconvertor nomenclator update` extrage
datele din pagină și reconstruiește registrul local
(`reference/nomenclator/registry.json`). Oglinda de pe data.gov.ro este
înghețată la 2018; nu o folosi.

Validarea istorică nu folosește automat registrul curent. Când există
`reference/nomenclator/<an>/registry.json`, comenzile `convert`, `batch`,
`corpus export` și auditul selectează instantaneul anului din calea
`data/<an>/`. Pentru 2024 sunt păstrate local anexele oficiale XLS publicate de
Ministerul Finanțelor și hash-urile lor; registrul rezultat conține 2.106
poziții. Cititorul acceptă atât XLS, cât și XLSX și identifică foile după
conținut, nu după un nume fragil. În lipsa unui instantaneu istoric, fluxurile
legacy păstrează compatibilitatea cu registrul curent, dar emit un avertisment;
o ediție publică nouă nu trebuie acceptată la audit înainte de adăugarea
instantaneului anului respectiv.

## Gramatica codurilor

- **Venituri**: `cc.SS[.ss[.pp]]` — capitol, subcapitol, paragraf; al
  doilea segment este sufixul de buget.
- **Cheltuieli funcționale**: capitol/subcapitol/paragraf, de ex.
  `65.02.04.01`.
- **Cheltuieli economice**: titlu/articol/alineat, de ex. `10.01.01`;
  codurile de grupă (`01`, `70`, `79`, `84`) sunt rânduri-titlu, nu
  intrări în anexe.
- **Sufixe de buget**: `.02` buget local, `.10` instituții finanțate din
  venituri proprii (Anexa 10), `.06/.07/.08` bugete de credite/FEN (fără
  anexă proprie — validate față de structura `.02`).
- Variante de tipărire tratate: compact (`65020301`), cu puncte
  (`65.02.03.01`), coduri combinate ale furnizorilor (`5102.200130` =
  capitol 51.02 × economic 20.01.30, inclusiv prefixele trunchiate de
  PDF), zecimale cu virgulă în coduri, sufixe-fantomă `.00` la final.

## Agregări și identități

Pseudo-codurile din formularele de raportare (cascada de venituri `00.xx`,
`49.90` venituri proprii, `98.02/99.02` excedent/deficit, variantele de
total-cheltuieli `49.02/50.02`, grupele economice) nu se află în anexe; ele
sunt predefinite în `rules.py` împreună cu identitățile aritmetice pe care
validatorul le impune:

- `00.01 = 00.02+00.15+00.16+00.17+45.02+46.02+48.02` și cascada completă
- `49.90 = 00.02 − 11.02 − 37.02 + 00.15`
- părți (`50.02 = 51.02+54.02+55.02+56.02`, …), compozițiile grupelor
  (`01 = Σ titluri`, `70 = 71+72+75`)
- `SECTIUNEA TOTAL = FUNCTIONARE + DEZVOLTARE` pentru fiecare cod;
  `37.02.03 = −37.02.04`
- sume de control pe rânduri acolo unde macheta le conține
  (`TOTAL = Σ Trim I–IV`)

Excepții codificate: liniile-memorandum „din care" nu se însumează
niciodată; titlul 85 este negativ; codurile marcate `*)` apar doar în
execuție; estimările (2027–2029) sunt adesea aprobate doar la nivel
agregat, așa că descendenți cu toate valorile zero nu contează drept
încălcare. Identitățile neconfirmate încă din surse primare poartă
`verified: false` și își retrogradează constatările la avertismente.
