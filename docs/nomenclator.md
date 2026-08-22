# The nomenclator (Ordinul MFP 1954/2005)

The validator checks every extracted code and sum against the official
"Clasificația indicatorilor privind finanțele publice", published as XLSX
annexes at <https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare>.
Annex filenames embed the amendment date and change URL on every update —
`bgconvertor nomenclator update` scrapes the page and rebuilds the local
registry (`reference/nomenclator/registry.json`). The data.gov.ro mirror is
frozen at 2018; do not use it.

## Code grammar

- **Revenues**: `cc.SS[.ss[.pp]]` — capitol, subcapitol, paragraf; the
  second segment is the budget suffix.
- **Functional expenses**: capitol/subcapitol/paragraf, e.g. `65.02.04.01`.
- **Economic expenses**: titlu/articol/alineat, e.g. `10.01.01`; grupa
  codes (`01`, `70`, `79`, `84`) are heading rows, not annex entries.
- **Budget suffixes**: `.02` local budget, `.10` own-revenue institutions
  (Anexa 10), `.06/.07/.08` credit/FEN budgets (no annex of their own —
  validated against the `.02` structure).
- Print variants handled: compact (`65020301`), dotted (`65.02.03.01`),
  combined vendor codes (`5102.200130` = capitol 51.02 × economic
  20.01.30, including PDF-truncated prefixes), comma decimals in codes,
  phantom trailing `.00`.

## Rollups and identities

Report-form pseudo-codes (`00.xx` revenue cascade, `49.90` venituri
proprii, `98.02/99.02` excedent/deficit, `49.02/50.02` total-cheltuieli
variants, economic grupe) are not in the annexes; they are seeded in
`rules.py` together with the arithmetic identities the validator enforces:

- `00.01 = 00.02+00.15+00.16+00.17+45.02+46.02+48.02` and the full cascade
- `49.90 = 00.02 − 11.02 − 37.02 + 00.15`
- parts (`50.02 = 51.02+54.02+55.02+56.02`, …), grupa compositions
  (`01 = Σ titluri`, `70 = 71+72+75`)
- `SECTIUNEA TOTAL = FUNCTIONARE + DEZVOLTARE` per code;
  `37.02.03 = −37.02.04`
- row checksums where the layout carries them (`TOTAL = Σ Trim I–IV`)

Exceptions encoded: "din care" memo lines never sum; title 85 is negative;
`*)`-flagged codes appear only in execution; estimări (2027–2029) are often
approved at aggregate level only, so all-zero children don't count as a
breach. Identities not yet confirmed against primary sources carry
`verified: false` and demote their findings to warnings.
