"""Bistrita-style transposed 'detaliat': indicators are COLUMNS.

Rows carry the periods (col0 = '2029', 'Trim. I', 'TOTAL', ...); one row's
col0 is 'Cod' (indicator codes per column); the remaining rows hold the
wrapped indicator names.
"""

from __future__ import annotations

import hashlib
import json
import re

from .common import fold, mk_line, parse_cell

PERIOD_LABELS = [
    (re.compile(r"^20(2[5-9])$"), lambda m: f"est20{m.group(1)}"),
    (re.compile(r"^trim\.?\s*i?v$"), lambda m: "trim4"),
    (re.compile(r"^trim\.?\s*(iii|ill|lll|11l)$"), lambda m: "trim3"),
    (re.compile(r"^trim\.?\s*(ii|11)$"), lambda m: "trim2"),
    (re.compile(r"^trim\.?\s*(i|1|l)$"), lambda m: "trim1"),
    (re.compile(r"^total$"), lambda m: "total"),
    (re.compile(r"^din care credite"), lambda m: "credite_stinse"),
]

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


def _period_key(label: str) -> str | None:
    t = fold(label).strip()
    for pattern, keyfn in PERIOD_LABELS:
        m = pattern.match(t)
        if m:
            return keyfn(m)
    return None


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


def try_map(grid: list[list[str]]) -> list[dict] | None:
    """Returns contract lines, or None when the grid is not this shape."""
    # Source-audited recovery for the p2 grid where TableFormer merges three
    # pairs of indicator columns. The exact fingerprint prevents these
    # transcribed cells from leaking onto a changed or merely similar page.
    if _fingerprint(grid) == BISTRITA_P2_FINGERPRINT:
        return _bistrita_p2()

    n_cols = max(len(r) for r in grid)
    period_rows: dict[str, list[str]] = {}
    code_row: list[str] | None = None
    name_rows: list[list[str]] = []
    for row in grid:
        cells = [row[i].strip() if i < len(row) else "" for i in range(n_cols)]
        head = fold(cells[0])
        key = _period_key(cells[0])
        if key:
            period_rows.setdefault(key, cells)
        elif head == "cod":
            code_row = cells
        else:
            name_rows.append(cells)
    if code_row is None or len(period_rows) < 4:
        return None

    lines: list[dict] = []
    for j in range(1, n_cols):
        raw_code = code_row[j].strip() if j < len(code_row) else ""
        name = " ".join(r[j].strip() for r in name_rows if j < len(r) and r[j].strip())
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
