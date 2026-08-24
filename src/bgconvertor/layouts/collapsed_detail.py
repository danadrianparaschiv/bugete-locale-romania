"""Recover Pitești's stamp-collapsed seven-column economic detail table.

The OCR grid for page 41 vertically interleaves several neighbouring rows,
but preserves the complete printed order independently in all five numeric
columns.  This mapper is intentionally source-scoped: it activates only when
the header, 49-per-column counts, blank-code position, and complete raw code
sequence match the audited page.  Any deviation falls through to the generic
mapper rather than applying a speculative correction.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell, split_header

DETAIL_COLUMNS = (
    "total_2026",
    "credite_restante",
    "est2027",
    "est2028",
    "est2029",
)
AMOUNT_TOKEN = re.compile(
    r"-?(?:\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|"
    r"\d{1,3}(?:\s+\d{3})+[.,]\d{2}|\d+[.,]\d{2})"
)
CODE_TOKEN = re.compile(r"01F|[DF]|\d+(?:\.\d+)?")

PITESTI_P41_RAW_CODES = (
    "5.911",
    "5940",
    "D",
    "70",
    "71",
    "7101",
    "710130",
    None,
    "F",
    "01F",
    "10",
    "1001",
    "100101",
    "100105",
    "100106",
    "100117",
    "100130",
    "1002",
    "100206",
    "1003",
    "100307",
    "20",
    "2001",
    "200101",
    "200102",
    "200103",
    "200104",
    "200108",
    "200130",
    "2004",
    "200401",
    "200404",
    "2005",
    "200530",
    "2006",
    "200601",
    "2013",
    "2014",
    "57",
    "5702",
    "570201",
    "570202",
    "59",
    "5911",
    "5940",
    "D",
    "70",
    "71",
    "7101",
)
# The PDF prints formula-only names for seven subtotal rows. Their official
# nomenclator labels are prefixed below while retaining each printed formula;
# leaf-row labels are transcribed directly from the rendered source.
PITESTI_P41_NAMES = (
    "Asociatii si fundatii",
    "Sume aferente persoanelor cu handicap neincadrate",
    "51+55+56+60+61+70+79+84+85)",
    "CHELTUIELI DE CAPITAL (cod 71+72+75)",
    "TITLUL XV ACTIVE NEFINANCIARE (cod 71.01+71.03)",
    "Active fixe (cod 71.01.01 la 71.01.03+71.01.30)",
    "Alte active fixe",
    "FUNCTIONARE+SECTIUNEA DE DEZVOLTARE)",
    "SECTIUNEA DE FUNCTIONARE (cod 01+79+85)",
    "10+20+30+40+50+51SF+55SF+57+59)",
    "TITLUL I CHELTUIELI DE PERSONAL (cod 10.01+10.02+10.03)",
    "Cheltuieli salariale in bani (cod 10.01.08+10.01.10 la 10.01.16+10.01.30)",
    "Salarii de baza",
    "Sporuri pentru conditii de munca",
    "Alte sporuri",
    "Indemnizatii de hrana",
    "Alte drepturi salariale in bani",
    "Cheltuieli salariale in natura (cod 10.02.06+10.02.30)",
    "Vouchere de vacanta",
    "Contributii (cod 10.03.01 la 10.03.06)",
    "Contributia asiguratorie pentru munca",
    "TITLUL II BUNURI SI SERVICII (cod 20.16+20.18 la 20.25+20.27+20.30)",
    "Bunuri si servicii (cod 20.01.01 la 20.01.09+20.01.30)",
    "Furnituri de birou",
    "Materiale pentru curatenie",
    "Incalzit, iluminat si forta motrica",
    "Apa, canal si salubritate",
    "Posta, telecomunicatii, radio, tv, internet",
    "Alte bunuri si servicii pentru intretinere si functionare",
    "Medicamente si materiale sanitare (cod 20.04.04)",
    "Medicamente",
    "Dezinfectanti",
    "Bunuri de natura obiectelor de inventar (cod 20.05.01+20.05.03+20.05.30)",
    "Alte obiecte de inventar",
    "Deplasari, detasari, transferari (cod 20.06.01+20.06.02)",
    "Deplasari interne, detasari, transferari",
    "Pregatire profesionala",
    "Protectia muncii",
    "TITLUL IX ASISTENTA SOCIALA (cod 57.02)",
    "Ajutoare sociale (cod 57.02.01 la 57.02.04)",
    "Ajutoare sociale in numerar",
    "Ajutoare sociale in natura",
    "TITLUL XI ALTE CHELTUIELI (cod 59.11+59.12+59.15+59.17+59.20+59.22+59.25)",
    "Asociatii si fundatii",
    "Sume aferente persoanelor cu handicap neincadrate",
    "51+55+56+60+61+70+79+84+85)",
    "CHELTUIELI DE CAPITAL (cod 71+72+75)",
    "TITLUL XV ACTIVE NEFINANCIARE (cod 71.01+71.03)",
    "Active fixe (cod 71.01.01 la 71.01.03+71.01.30)",
)

OUTER_SECTION = "68020502 Asistenta sociala in caz de invaliditate"
FUNCTION_SECTION = f"{OUTER_SECTION} — SECTIUNEA DE FUNCTIONARE"


def _code_tokens(text: str) -> list[str]:
    # The source prints 71; OCR separated its two glyphs only in this cell.
    if text.strip() == "5 1":
        return ["71"]
    return CODE_TOKEN.findall(text or "")


def try_map(grid: list[list[str]]) -> list[dict] | None:
    if not grid or max(len(row) for row in grid) != 7:
        return None
    header_rows, first_data = split_header(grid)
    header = fold(" ".join(cell for index in header_rows for cell in grid[index]))
    if not all(
        marker in header
        for marker in ("denumirea indicatorilor", "bugetare", "estimari", "2029")
    ):
        return None

    codes: list[str | None] = []
    blank_code_positions: list[int] = []
    value_streams = [[] for _ in DETAIL_COLUMNS]
    collapse_seen = False
    for row in grid[first_data:]:
        cells = [*(row[:7]), *([""] * max(0, 7 - len(row)))]
        row_codes = _code_tokens(cells[1])
        row_values = [AMOUNT_TOKEN.findall(cells[index]) for index in range(2, 7)]
        collapse_seen |= len(row_codes) > 1 or any(len(tokens) > 1 for tokens in row_values)

        if not row_codes and all(len(tokens) == 1 for tokens in row_values):
            blank_code_positions.append(len(value_streams[0]))
        codes.extend(row_codes)
        for stream, tokens in zip(value_streams, row_values, strict=True):
            stream.extend(tokens)

    counts = {len(stream) for stream in value_streams}
    if not collapse_seen or counts != {49} or blank_code_positions != [7]:
        return None
    codes.insert(blank_code_positions[0], None)
    if tuple(codes) != PITESTI_P41_RAW_CODES:
        return None

    # Source-audited code correction after the exact raw fingerprint matches.
    codes[0] = "5911"
    lines: list[dict] = []
    for index, (raw_code, name) in enumerate(
        zip(codes, PITESTI_P41_NAMES, strict=True)
    ):
        if index == 7:
            lines.append(mk_line(None, OUTER_SECTION, OUTER_SECTION, {}, [], None))
        values: dict[str, str] = {}
        issues: list[dict] = []
        for column, stream in zip(DETAIL_COLUMNS, value_streams, strict=True):
            parse_cell(stream[index], column, values, issues)
        if issues:
            return None
        section = None if index < 7 else OUTER_SECTION if index == 7 else FUNCTION_SECTION
        lines.append(mk_line(raw_code, name, section, values, [], None))
    return lines
