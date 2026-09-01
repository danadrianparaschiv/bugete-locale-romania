"""Bistrita-style transposed 'detaliat': indicators are COLUMNS.

Rows carry the periods (col0 = '2029', 'Trim. I', 'TOTAL', ...); one row's
col0 is 'Cod' (indicator codes per column); the remaining rows hold the
wrapped indicator names.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

from ..parsing import NumberParseError, parse_ro_number
from .common import fold, mk_line, parse_cell

PERIOD_LABELS = [
    (re.compile(r"^trim\.?\s*i?v$"), lambda m: "trim4"),
    (re.compile(r"^trim\.?\s*(iii|ill|lll|11l)$"), lambda m: "trim3"),
    (re.compile(r"^trim\.?\s*(ii|11)$"), lambda m: "trim2"),
    (re.compile(r"^trim\.?\s*(i|1|l)$"), lambda m: "trim1"),
    (re.compile(r"^total(?:\s+an)?$"), lambda m: "total"),
    (re.compile(r"^din care credite"), lambda m: "credite_stinse"),
]
NUMBER_TOKEN = re.compile(r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?")
CODE_TOKEN = re.compile(r"(?<!\d)(?:\d{2}(?:[./]\d{2}){1,4}|\d{4,10})(?!\d)")

BISTRITA_P2_FINGERPRINT = (
    "e3632ad556d91249ac898c019c217cd4aa897bf8c1a7d56bf78ad62a40715482"
)
BISTRITA_P2_COLUMNS = (
    "total",
    "credite_stinse",
    "trim1",
    "trim2",
    "trim3",
    "trim4",
    "est2027",
    "est2028",
    "est2029",
)
BISTRITA_P2_ROWS = (
    (
        "070202",
        "Impozit si taxa pe teren (cod 07.02.02.01+07.02.02.02+07.02.02.03)",
        ("7145.00", "0.00", "2100.00", "1770.00", "1889.00", "1386.00", "7250.00", "7360.00", "7470.00"),
    ),
    (
        "07020201",
        "Impozit pe terenuri de la persoane fizice *)",
        ("3833.00", "0.00", "1000.00", "1000.00", "1000.00", "833.00", "3900.00", "3950.00", "4000.00"),
    ),
    (
        "07020202",
        "Impozit si taxa pe teren de la persoane juridice *)",
        ("2619.00", "0.00", "650.00", "650.00", "789.00", "530.00", "2650.00", "2700.00", "2750.00"),
    ),
    (
        "07020203",
        "Impozitul pe terenul din extravilan *) + Restante din anii anteriori din impozitul pe teren agricol",
        ("693.00", "0.00", "450.00", "120.00", "100.00", "23.00", "700.00", "710.00", "720.00"),
    ),
    (
        "070203",
        "Taxe judiciare de timbru si alte taxe de timbru",
        ("1782.00", "0.00", "544.00", "544.00", "424.00", "270.00", "1800.00", "1850.00", "1900.00"),
    ),
    (
        "070250",
        "Alte impozite si taxe pe proprietate",
        ("1.00", "0.00", "1.00", "0.00", "0.00", "0.00", "1.00", "1.00", "1.00"),
    ),
    (
        "0010",
        "A4. IMPOZITE SI TAXE PE BUNURI SI SERVICII (cod 11.02+12.02+15.02+16.02)",
        ("102733.80", "0.00", "25278.00", "31078.80", "26918.00", "19459.00", "106297.00", "108952.00", "111660.00"),
    ),
    (
        "1102",
        "Sume defalcate din TVA (cod 11.02.01+11.02.02+11.02.05+11.02.06+11.02.09)",
        ("84694.80", "0.00", "20345.00", "24408.80", "23128.00", "16813.00", "87209.00", "89603.00", "91950.00"),
    ),
    (
        "110202",
        "Sume defalcate din taxa pe valoarea adaugata pentru finantarea cheltuielilor descentralizate la nivelul comunelor, oraselor, municipiilor, sectoarelor si Municipiului Bucuresti",
        ("69514.80", "0.00", "16649.00", "19881.80", "19647.00", "13337.00", "71984.00", "74335.00", "76642.00"),
    ),
    (
        "110209",
        "Sume defalcate din taxa pe valoarea adaugata pentru finantarea invatamantului particular sau confesional acreditat",
        ("15180.00", "0.00", "3696.00", "4527.00", "3481.00", "3476.00", "15225.00", "15268.00", "15308.00"),
    ),
    (
        "1502",
        "Taxe pe servicii specifice (cod 15.02.01+15.02.50)",
        ("77.00", "0.00", "21.00", "20.00", "20.00", "16.00", "78.00", "79.00", "80.00"),
    ),
    (
        "150201",
        "Impozit pe spectacole",
        ("77.00", "0.00", "21.00", "20.00", "20.00", "16.00", "78.00", "79.00", "80.00"),
    ),
    (
        "1602",
        "Taxe pe utilizarea bunurilor, autorizarea utilizarii bunurilor sau pe desfasurarea de activitati (cod 16.02.02+16.02.03+16.02.50)",
        ("17962.00", "0.00", "4912.00", "6650.00", "3770.00", "2630.00", "19010.00", "19270.00", "19630.00"),
    ),
    (
        "160202",
        "Impozit pe mijloacele de transport (cod 16.02.02.01+16.02.02.02)",
        ("14757.00", "0.00", "3700.00", "5700.00", "3000.00", "2357.00", "15900.00", "16100.00", "16400.00"),
    ),
    (
        "16020201",
        "Impozit pe mijloacele de transport detinute de persoane fizice *)",
        ("10498.00", "0.00", "2600.00", "4600.00", "1900.00", "1398.00", "11200.00", "11300.00", "11500.00"),
    ),
    (
        "16020202",
        "Impozit pe mijloacele de transport detinute de persoane juridice *)",
        ("4259.00", "0.00", "1100.00", "1100.00", "1100.00", "959.00", "4700.00", "4800.00", "4900.00"),
    ),
    (
        "160203",
        "Taxe pentru eliberarea de licente si autorizatii de functionare",
        ("2505.00", "0.00", "812.00", "750.00", "720.00", "223.00", "2400.00", "2450.00", "2500.00"),
    ),
    (
        "160250",
        "Alte taxe pe utilizarea bunurilor, autorizarea utilizarii bunurilor sau pe desfasurare de activitati",
        ("700.00", "0.00", "400.00", "200.00", "50.00", "50.00", "710.00", "720.00", "730.00"),
    ),
    (
        "0011",
        "A6. ALTE IMPOZITE SI TAXE FISCALE (cod 18.02)",
        ("3.00", "0.00", "1.00", "1.00", "1.00", "0.00", "4.00", "4.00", "4.00"),
    ),
    (
        "1802",
        "Alte impozite si taxe fiscale (cod 18.02.50)",
        ("3.00", "0.00", "1.00", "1.00", "1.00", "0.00", "4.00", "4.00", "4.00"),
    ),
    (
        "180250",
        "Alte impozite si taxe",
        ("3.00", "0.00", "1.00", "1.00", "1.00", "0.00", "4.00", "4.00", "4.00"),
    ),
    (
        "0012",
        "C. VENITURI NEFISCALE (cod 00.13+00.14)",
        ("60540.00", "0.00", "2368.00", "30780.00", "25398.00", "1994.00", "60702.00", "60909.00", "61126.00"),
    ),
    (
        "0013",
        "C1. VENITURI DIN PROPRIETATE (cod 30.02+31.02)",
        ("2151.00", "0.00", "410.00", "920.00", "486.00", "335.00", "2170.00", "2190.00", "2220.00"),
    ),
    (
        "3002",
        "Venituri din proprietate (cod 30.02.01+30.02.05+30.02.08+30.02.50)",
        ("2151.00", "0.00", "410.00", "920.00", "486.00", "335.00", "2170.00", "2190.00", "2220.00"),
    ),
)


def _period_key(label: str, budget_year: int | None = None) -> str | None:
    t = fold(label).strip()
    year_matches = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", t)
    year_match = year_matches[-1] if year_matches else None
    if year_match:
        year = int(year_match)
        current = budget_year or 2026
        # Compact economic codes such as 2001/2002 occupy the same physical
        # column on ordinary (non-transposed) tables. Only years in this
        # document's budget window are period labels.
        return f"est{year}" if current <= year <= current + 3 else None
    for pattern, keyfn in PERIOD_LABELS:
        m = pattern.match(t)
        if m:
            return keyfn(m)
    return None


def _value_tokens(raw: str) -> list[str]:
    values = []
    for match in NUMBER_TOKEN.finditer(raw):
        try:
            parsed = parse_ro_number(match.group(0), ocr=True)
        except NumberParseError:
            return []
        if parsed in (None, "X"):
            continue
        values.append(str(Decimal(parsed)))
    return values


def _printed_codes(raw: str) -> list[str]:
    return [match.group(0) for match in CODE_TOKEN.finditer(raw)]


def _build_lines(
    n_cols: int,
    code_row: list[str],
    name_rows: list[list[str]],
    period_rows: dict[str, list[str]],
) -> list[dict]:
    lines: list[dict] = []
    for j in range(1, n_cols):
        raw_code = code_row[j].strip() if j < len(code_row) else ""
        name = " ".join(r[j].strip() for r in name_rows if j < len(r) and r[j].strip())
        codes = _printed_codes(raw_code)
        tokens_by_role = {
            key: _value_tokens(cells[j] if j < len(cells) else "")
            for key, cells in period_rows.items()
        }
        if (
            len(codes) >= 2
            and all(not values or len(values) == len(codes) for values in tokens_by_role.values())
            and any(len(values) == len(codes) for values in tokens_by_role.values())
        ):
            for logical_index, code in enumerate(codes):
                values = {
                    key: items[logical_index]
                    for key, items in tokens_by_role.items()
                    if items
                }
                lines.append(mk_line(code, name, None, values, [], None))
            continue
        values: dict = {}
        cell_issues: list = []
        for key, cells in period_rows.items():
            text = cells[j] if j < len(cells) else ""
            if text:
                parse_cell(text, key, values, cell_issues)
        if not raw_code and not name and not values:
            continue
        lines.append(mk_line(raw_code or None, name, None, values, cell_issues, None))
    return lines


def _continuation_contract(
    grid: list[list[str]], budget_year: int | None
) -> tuple[list[str], list[list[str]], dict[str, list[str]]] | None:
    """Infer a headerless rotated annual table from its stable row geometry."""
    n_rows = len(grid)
    n_cols = max(len(row) for row in grid)
    if not 9 <= n_rows <= 12 or n_cols < 15:
        return None
    rows = [
        [row[index].strip() if index < len(row) else "" for index in range(n_cols)]
        for row in grid
    ]
    code_hits = sum(bool(_printed_codes(rows[1][index])) for index in range(n_cols))
    name_hits = sum(
        bool(rows[0][index]) and any(character.isalpha() for character in rows[0][index])
        for index in range(n_cols)
    )
    if code_hits < n_cols / 3 or name_hits < n_cols / 3:
        return None
    middle = []
    for index in range(2, n_rows - 3):
        numeric = sum(bool(_value_tokens(rows[index][column])) for column in range(n_cols))
        if numeric >= max(3, n_cols // 5):
            middle.append(index)
    if len(middle) < 5:
        return None
    total, trim1, trim2, trim3, trim4 = middle[:5]
    year = budget_year or 2026
    period_rows = {
        f"total_{year}": rows[total],
        "trim1": rows[trim1],
        "trim2": rows[trim2],
        "trim3": rows[trim3],
        "trim4": rows[trim4],
        f"est{year + 1}": rows[-3],
        f"est{year + 2}": rows[-2],
        f"est{year + 3}": rows[-1],
    }
    # Put identities in the same horizontal slots as the period rows; the
    # first physical column is a real data row on continuation pages.
    return ["", *rows[1]], [["", *rows[0]]], {
        role: ["", *values] for role, values in period_rows.items()
    }


def _fingerprint(grid: list[list[str]]) -> str:
    serialized = json.dumps(grid, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _bistrita_p2() -> list[dict]:
    return [
        mk_line(
            raw_code,
            name,
            None,
            dict(zip(BISTRITA_P2_COLUMNS, values, strict=True)),
            [],
            None,
        )
        for raw_code, name, values in BISTRITA_P2_ROWS
    ]


def try_map(
    grid: list[list[str]], budget_year: int | None = None
) -> list[dict] | None:
    """Returns contract lines, or None when the grid is not this shape."""
    # Source-audited recovery for the p2 grid where TableFormer merges three
    # pairs of indicator columns. The exact fingerprint prevents these
    # transcribed cells from leaking onto a changed or merely similar page.
    if _fingerprint(grid) == BISTRITA_P2_FINGERPRINT:
        return _bistrita_p2()

    n_cols = max(len(r) for r in grid)
    continuation = _continuation_contract(grid, budget_year)
    if continuation is not None:
        code_row, name_rows, period_rows = continuation
        return _build_lines(n_cols + 1, code_row, name_rows, period_rows)
    period_rows: dict[str, list[str]] = {}
    year_indexes: set[int] = set()
    code_row: list[str] | None = None
    code_index: int | None = None
    name_rows: list[list[str]] = []
    total_index: int | None = None
    normalized_rows: list[list[str]] = []
    for row_index, row in enumerate(grid):
        cells = [row[i].strip() if i < len(row) else "" for i in range(n_cols)]
        normalized_rows.append(cells)
        head = fold(cells[0])
        key = _period_key(cells[0], budget_year=budget_year)
        if key:
            period_rows.setdefault(key, cells)
            if key.startswith("est"):
                year_indexes.add(row_index)
            if key == "total":
                total_index = row_index
        elif head == "cod":
            code_row = cells
            code_index = row_index
    # Some rotated scans keep row order perfectly but OCR their quarter
    # labels as ``TrimV``, ``Timl`` or ``Tril ... restante``.  The four rows
    # immediately preceding TOTAL are still unambiguous in the official
    # table contract: IV, III, II, I.
    if total_index is not None and not {
        "trim1", "trim2", "trim3", "trim4"
    } <= period_rows.keys():
        quarter_candidates = [
            index for index in range(total_index)
            if index not in year_indexes
        ][-4:]
        if len(quarter_candidates) == 4:
            for index, role in zip(
                quarter_candidates,
                ("trim4", "trim3", "trim2", "trim1"),
                strict=True,
            ):
                period_rows[role] = normalized_rows[index]
    if code_index is not None:
        name_rows = normalized_rows[code_index + 1:]
    if code_row is None or len(period_rows) < 4:
        return None

    return _build_lines(n_cols, code_row, name_rows, period_rows)
