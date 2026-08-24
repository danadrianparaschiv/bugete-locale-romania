"""Recover Arad's stamp-damaged per-chapter economic detail page.

Page 151 contains 40 logical rows and two numeric columns. Docling preserves
the complete numeric order, but merges rows 179-180 and copies stamp text into
the code cells for rows 191-195. This mapper activates only for the audited
five-column header and the complete raw row/code fingerprint. Any difference
falls through to the generic mapper instead of applying source-specific data.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

VALUE_TOKEN = re.compile(r"-?\d[\d.]*,\d{2}")
VALUE_COLUMNS = ("buget_2026", "credite_restante")

ARAD_P151_RAW_FINGERPRINT = (
    ("160", "70"),
    ("161", "71"),
    ("162", "71.01"),
    ("163", "71.01.02"),
    ("164", "71.01.30"),
    ("165", "84.02"),
    ("166", "SECTIUNEA DE FUNCTIONARE"),
    ("167", "01"),
    ("168", "20"),
    ("169", "20.01"),
    ("170", "20.02"),
    ("171", "20.05"),
    ("172", "20.19"),
    ("173", "20.30"),
    ("174", "40"),
    ("175", "40.03"),
    ("176", "79"),
    ("177", "81"),
    ("178", "81.01"),
    ("179 180", "81.01.01 81.02"),
    ("181", "81.02.05"),
    ("182", "84"),
    ("183", "85"),
    ("184", "85.01"),
    ("185", "85.01.01"),
    ("186 SECTIUNEA DE DEZVOLTARE", "186 SECTIUNEA DE DEZVOLTARE"),
    ("187", "55"),
    ("188", "55.01"),
    ("189", "55.01.12"),
    ("190", "56"),
    ("191 (AFIP) 56.16", "191 (AFIP) 56.16"),
    ("192", "Finantarea natională 中 56.16.01"),
    ("193", "Jude Finanțarea externă nerambursabilă 05 6.16.02"),
    ("194", "Cheltuieli neeligibile 6.16.03"),
    (
        "195",
        "Programe finanțate din Fondul Eurapean de 56.48 cadrului financiar 2021-2027",
    ),
    ("196", "56.48.01"),
    ("197", "56.48.02"),
    ("198", "56.48.03"),
    ("199", "58"),
)

ARAD_P151_CODES = (
    "70",
    "71",
    "71.01",
    "71.01.02",
    "71.01.30",
    "84.02",
    None,
    "01",
    "20",
    "20.01",
    "20.02",
    "20.05",
    "20.19",
    "20.30",
    "40",
    "40.03",
    "79",
    "81",
    "81.01",
    "81.01.01",
    "81.02",
    "81.02.05",
    "84",
    "85",
    "85.01",
    "85.01.01",
    None,
    "55",
    "55.01",
    "55.01.12",
    "56",
    "56.16",
    "56.16.01",
    "56.16.02",
    "56.16.03",
    "56.48",
    "56.48.01",
    "56.48.02",
    "56.48.03",
    "58",
)

ARAD_P151_NAMES = (
    "CHELTUIELI DE CAPITAL",
    "TITLUL XVI ACTIVE NEFINANCIARE",
    "Active fixe",
    "Masini, echipamente si mijloace de transport",
    "Alte active fixe",
    (
        'Transporturi - TOTAL CAPITOL 84.02 "TRANSPORTURI", din care pe '
        "titluri de cheltuieli, articole si alineate:"
    ),
    "SECTIUNEA DE FUNCTIONARE",
    "CHELTUIELI CURENTE",
    "TITLUL II BUNURI SI SERVICII",
    "Bunuri si servicii",
    "Reparatii curente",
    "Bunuri de natura obiectelor de inventar",
    (
        "Contributii ale administratiei publice locale la realizarea unor lucrari "
        "si servicii de interes public local, in baza unor conventii sau contracte "
        "de asociere"
    ),
    "Alte cheltuieli",
    "TITLUL IV SUBVENTII",
    "Subventii pentru acoperirea diferentelor de pret si tarif",
    "OPERATIUNI FINANCIARE",
    "TITLUL XX RAMBURSARI DE CREDITE",
    "Rambursari de credite externe",
    "Rambursari de credite externe contractate de ordonatorii de credite",
    "Rambursari de credite interne",
    "Rambursari de credite aferente datoriei publice interne locale",
    "PLATI EFECTUATE IN ANII PRECEDENTI SI RECUPERATE IN ANUL CURENT",
    "TITLUL XXII PLATI EFECTUATE IN ANII PRECEDENTI SI RECUPERATE IN ANUL CURENT",
    "Plati efectuate in anii precedenti si recuperate in anul curent",
    (
        "Plati efectuate in anii precedenti si recuperate in anul curent in "
        "sectiunea de functionare a bugetului local"
    ),
    "SECTIUNEA DE DEZVOLTARE",
    "TITLUL VII ALTE TRANSFERURI",
    "A. Transferuri interne",
    "Investitii ale agentilor economici cu capital de stat",
    (
        "TITLUL VIII PROIECTE CU FINANTARE DIN FONDURI EXTERNE NERAMBURSABILE "
        "(FEN) POSTADERARE"
    ),
    "Alte facilitati si instrumente postaderare (AFIP)",
    "Finantarea nationala",
    "Finantarea externa nerambursabila",
    "Cheltuieli neeligibile",
    (
        "Programe finantate din Fondul European de Dezvoltare Regionala (FEDR), "
        "aferente cadrului financiar 2021-2027"
    ),
    "Finantare nationala",
    "Finantare externa nerambursabila",
    "Cheltuieli neeligibile",
    (
        "TITLUL X PROIECTE CU FINANTARE DIN FONDURI EXTERNE NERAMBURSABILE "
        "AFERENTE CADRULUI FINANCIAR 2014-2020 SI DIN FONDUL DE MODERNIZARE"
    ),
)


def try_map(grid: list[list[str]]) -> list[dict] | None:
    if not grid or max(len(row) for row in grid) != 5 or len(grid) < 3:
        return None
    header = fold(" ".join(grid[0]))
    if not all(
        marker in header
        for marker in ("denumirea indicatorilor", "cod indicator", "buget an 2026")
    ):
        return None
    if tuple(grid[1][:5]) != ("0", "1", "2", "INITIAL 3", "4"):
        return None

    data = [[*(row[:5]), *([""] * max(0, 5 - len(row)))] for row in grid[2:]]
    fingerprint = tuple((row[0].strip(), row[2].strip()) for row in data)
    if fingerprint != ARAD_P151_RAW_FINGERPRINT:
        return None

    value_streams = [[] for _ in VALUE_COLUMNS]
    collapse_seen = False
    for row in data:
        tokens = [VALUE_TOKEN.findall(row[index]) for index in (3, 4)]
        collapse_seen |= any(len(part) > 1 for part in tokens)
        for stream, part in zip(value_streams, tokens, strict=True):
            stream.extend(part)
    if not collapse_seen or {len(stream) for stream in value_streams} != {40}:
        return None

    lines: list[dict] = []
    section: str | None = None
    for index, (raw_code, name) in enumerate(
        zip(ARAD_P151_CODES, ARAD_P151_NAMES, strict=True)
    ):
        row_no = 160 + index
        if row_no in (166, 186):
            section = name
        values: dict[str, str] = {}
        issues: list[dict] = []
        for column, stream in zip(VALUE_COLUMNS, value_streams, strict=True):
            parse_cell(stream[index], column, values, issues)
        if issues:
            return None
        lines.append(mk_line(raw_code, name, section, values, [], row_no))
    return lines
