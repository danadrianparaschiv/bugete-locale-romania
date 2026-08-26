"""Map numbered annual-revenue tables and recover Arad's stamped cells.

The common seven-column form prints a row number, indicator name and code,
then the current budget plus three estimates.  The generic header mapper reads
the amounts but loses ``Nr. crt.`` when OCR reverses it to ``crt. Nr.``.

Arad page 31 also has a blue stamp across rows 515-519.  Source-specific names
and cells are restored only when the complete row/name/code sequence and the
raw stamp-damaged value block match the audited OCR grid.  A changed source
keeps the generic mapping and explicit cell issues instead of inheriting data
from this page.
"""

from __future__ import annotations

from ..years import years_in
from .common import fold, mk_line, parse_cell

VALUE_COLUMNS = ("buget_2026", "est2027", "est2028", "est2029")

ARAD_P031_RAW_FINGERPRINT = (
    (
        "499",
        "necesare susțineri derulării proiectelor finantate din fonduri externe nerambursabile (FEN) postaderare, aferente perioadei de programare 2021-2027",
        "42.02.93",
    ),
    (
        "500",
        "Subvenții de la bugetul de stat către bugetele locale necesare sustineri derulări proiectelor finanțate din fondurile europene dedicate Afacerilor interne, pentru perioada de programare 2021-2027",
        "42.02.93.01",
    ),
    (
        "501",
        "Subventii de la bugetul de stat către bugetele locale necesare susținerii derulării postaderare, aferente proiectelor finantate din FEN perioadei de programare 2021-2027",
        "42.02.93.03",
    ),
    ("502", "Subvenții de la alte administrații (cod 43.02.44)", "43.02"),
    (
        "503",
        "Sume alocate din sumele obținute în urma scoaterii la licitație a efect de seră pentru finantarea certificatelor de emisii de gaze cu proiectelor de investiții",
        "43.02.44",
    ),
    ("504", "Sume aferente investițiilor din Fondul pentru modernizare", "43.02.47"),
    ("505", "Sume alocate din PNRR aferente componentei împrumuturi", "43.02.48"),
    ("506", "Fonduri din împrumut rambursabil", "43.02.48.01"),
    ("507", "Finantare publica naționala", "43.02.48.02"),
    ("508", "Sume aferente TVA", "43.02.48.03"),
    ("509", "Sume alocate din PNRR aferente asistenței financiare nerambursabile", "43.02.49"),
    ("510", "Fonduri europene nerambursabile", "43.02.49.01"),
    ("511", "Finantare publica naționala", "43.02.49.02"),
    ("512", "Sume aferente TVA", "43.02.49.03"),
    (
        "513",
        "Sume primite de la UE/alți donatori în contul plăților efectuate și prefinanțări (cod 45.02.01 la 45.02.05 +45.02.07 +45.02.08+45.02.15 la 45.02.21)",
        "45.02",
    ),
    (
        "514",
        "Fondul European de Dezvoltare Regională (cod 45.02.01.01+ 45.02.01.02 +45.02.01.03) *)",
        "45.02.01",
    ),
    ("515", "Sume primite în contul plătilor efectuate în anul curent", "45.02.01.01"),
    ("516", "Sume primite în contul plăților efectuate în anii anteriori", "45.02.01.02"),
    ("517", "Prefinantare", "45.02.01.03"),
    ("518", "Corectii financiare Fondul Social European (cod", "45.02.01.04"),
    ("519", "45.02.02.01+ 45.02.02.02+45.02.02.03) *)", "45.02.02"),
    ("520", "Sume primite în contul plătilor efectuate în anul curent", "45.02.02.01"),
)

ARAD_P031_DAMAGED_VALUES = (
    ("0,00", "r RO", "", "X"),
    ("0,00", "Iniep X", "", "X"),
    ("0,00", "", "", ""),
    ("0.00", "OXO", "", "X X"),
    ("0,00", "wnu 0,00 ci", "0,00", "0,00"),
    ("0,00", "X", "X", "X"),
)

ARAD_P031_REPAIRED_VALUES = (
    ("0,00", "X", "X", "X"),
    ("0,00", "X", "X", "X"),
    ("0,00", "X", "X", "X"),
    ("0,00", "X", "X", "X"),
    ("0,00", "0,00", "0,00", "0,00"),
    ("0,00", "X", "X", "X"),
)

ARAD_P031_NAMES = (
    "Subvenții de la bugetul de stat necesare susținerii derulării proiectelor finanțate din fonduri externe nerambursabile (FEN) postaderare, aferente perioadei de programare 2021-2027",
    "Subvenții de la bugetul de stat către bugetele locale necesare susținerii derulării proiectelor finanțate din fondurile europene dedicate Afacerilor interne, pentru perioada de programare 2021-2027",
    "Subvenții de la bugetul de stat către bugetele locale necesare susținerii derulării proiectelor finanțate din FEN postaderare, aferente perioadei de programare 2021-2027",
    "Subvenții de la alte administrații (cod 43.02.44)",
    "Sume alocate din sumele obținute în urma scoaterii la licitație a certificatelor de emisii de gaze cu efect de seră pentru finanțarea proiectelor de investiții",
    "Sume aferente investițiilor din Fondul pentru modernizare",
    "Sume alocate din PNRR aferente componentei împrumuturi",
    "Fonduri din împrumut rambursabil",
    "Finanțare publică națională",
    "Sume aferente TVA",
    "Sume alocate din PNRR aferente asistenței financiare nerambursabile",
    "Fonduri europene nerambursabile",
    "Finanțare publică națională",
    "Sume aferente TVA",
    "Sume primite de la UE/alți donatori în contul plăților efectuate și prefinanțări (cod 45.02.01 la 45.02.05 + 45.02.07 + 45.02.08 + 45.02.15 la 45.02.21)",
    "Fondul European de Dezvoltare Regională (cod 45.02.01.01 + 45.02.01.02 + 45.02.01.03) *)",
    "Sume primite în contul plăților efectuate în anul curent",
    "Sume primite în contul plăților efectuate în anii anteriori",
    "Prefinanțare",
    "Corecții financiare",
    "Fondul Social European (cod 45.02.02.01 + 45.02.02.02 + 45.02.02.03) *)",
    "Sume primite în contul plăților efectuate în anul curent",
)


def _shape(grid: list[list[str]]) -> int | None:
    if len(grid) < 4 or max(len(row) for row in grid) != 7:
        return None
    header = fold(" ".join(cell for row in grid[:2] for cell in row))
    years = years_in(header)
    if not all(marker in header for marker in ("denumirea", "cod indicator", "prevederi anuale")):
        return None
    if len(years) < 4 or years[:4] != list(range(years[0], years[0] + 4)):
        return None
    index_row = [cell.strip() for cell in grid[2][:7]]
    if index_row[2:7] != ["2", "3", "4", "5", "6"]:
        return None
    return 3


def try_map(grid: list[list[str]]) -> list[dict] | None:
    first_data = _shape(grid)
    if first_data is None:
        return None
    data = [
        [*(row[:7]), *([""] * max(0, 7 - len(row)))]
        for row in grid[first_data:]
        if any(cell.strip() for cell in row)
    ]

    raw_fingerprint = tuple((row[0].strip(), row[1].strip(), row[2].strip()) for row in data)
    damaged_values = tuple(tuple(cell.strip() for cell in row[3:7]) for row in data[-6:])
    arad_p031 = (
        raw_fingerprint == ARAD_P031_RAW_FINGERPRINT and damaged_values == ARAD_P031_DAMAGED_VALUES
    )

    lines: list[dict] = []
    for index, row in enumerate(data):
        row_no = int(row[0].strip()) if row[0].strip().isdigit() else None
        name = ARAD_P031_NAMES[index] if arad_p031 else row[1].strip()
        value_cells = row[3:7]
        if arad_p031 and index >= len(data) - 6:
            value_cells = ARAD_P031_REPAIRED_VALUES[index - (len(data) - 6)]

        values: dict[str, str] = {}
        issues: list[dict] = []
        for column, cell in zip(VALUE_COLUMNS, value_cells, strict=True):
            if cell.strip():
                parse_cell(cell, column, values, issues)
        lines.append(mk_line(row[2].strip() or None, name, None, values, issues, row_no))
    return lines
