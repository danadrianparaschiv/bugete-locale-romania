"""Institution budget tables whose row spans collapse under OCR.

Some scanned Arad pages contain several school budgets in one ruled table.
Docling preserves the order of indicator-code and numeric tokens, but merged
vertical labels can shift those tokens between adjacent rows.  This mapper
segments named institution blocks, then aligns the ordered code/value streams.
It fails closed unless every recovered code has exactly one recovered value.
"""

from __future__ import annotations

import re

from .common import fold, mk_line, parse_cell

INSTITUTION_HEADING = re.compile(
    r"\b\d{1,3}\.\s+(?:liceul|colegiul|scoala|școala|gradinita|grădinița)"
    r".{2,120}?\barad\b",
    re.IGNORECASE,
)
CODE_TOKEN = re.compile(r"(?<![\d.,])(?:\*|\d{1,2}(?:\.\d{1,2}){0,3})(?![\d.,])")
VALUE_TOKEN = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?|-?\d+,\d+")
TRAILING_PREFIX = re.compile(
    r"^\s*(?P<code>\*|\d{1,2}(?:\.\d{1,2}){0,3})\s+"
    r"(?P<value>-?\d{1,3}(?:\.\d{3})*,\d+)\s*$"
)

NAMES = {
    "*": "TOTAL VENITURI",
    "01": "CHELTUIELI CURENTE",
    "10": "TITLUL I CHELTUIELI DE PERSONAL",
    "10.01": "Cheltuieli salariale in bani",
    "10.03": "Contributii",
    "20": "TITLUL II BUNURI SI SERVICII",
    "20.01": "Bunuri si servicii",
    "20.02": "Reparatii curente",
    "20.03": "Hrana",
    "20.05": "Bunuri de natura obiectelor de inventar",
    "20.11": "Carti, publicatii si materiale documentare",
    "20.13": "Pregatire profesionala",
    "20.30": "Alte cheltuieli",
    "33.10.14": "Contributia elevilor si studentilor pentru internate, camine si cantine",
    "43.10.09": "Subventii pentru institutii publice",
    "65.10.11": "Servicii auxiliare pentru educatie",
    "65.10.11.03": "Internat si cantina pentru elevi",
    "90": "TITLUL XXIII REZERVE, EXCEDENT/DEFICIT",
    "93.01": "DEFICIT (cod 93.01)",
    "93.01.96": "Deficitul sectiunii de functionare (cod 93.01.96)",
}
ECONOMIC_CODES = re.compile(r"^(?:01|20(?:\.|$)|90$|93(?:\.|$))")


def _unique_row_text(row: list[str]) -> str:
    values = []
    for cell in row:
        text = cell.strip()
        if text and text not in values:
            values.append(text)
    return " ".join(values)


def _codes(text: str) -> list[str]:
    # Remove common single-character OCR debris without changing token order.
    clean = re.sub(r"[^\d.*\s]", " ", text)
    return CODE_TOKEN.findall(clean)


def _values(text: str) -> list[str]:
    return VALUE_TOKEN.findall(text)


def _canonical_name(code: str, occurrence: int) -> str:
    if code == "96":
        if occurrence == 1:
            return "VENITURILE SECTIUNII DE FUNCTIONARE"
        if occurrence == 2:
            return "CHELTUIELILE SECTIUNII DE FUNCTIONARE cod (01)"
        return "Internat si cantina pentru elevi - sectiunea de functionare"
    if code == "65.10":
        return "TOTAL CHELTUIELI" if occurrence == 1 else (
            "Total, din care pe forme de invatamant si sectiuni"
        )
    return NAMES.get(code, "")


def _normalize_code_order(codes: list[str]) -> list[str]:
    out = list(codes)
    # One noisy Arad cell reads "20 01" across the CHELTUIELI CURENTE and
    # TITLUL II rows.  The following 20.01 child proves the printed order.
    for index in range(len(out) - 2):
        if out[index:index + 3] == ["20", "01", "20.01"]:
            out[index:index + 2] = ["01", "20"]
    return out


def _map_block(
    heading: str,
    rows: list[list[str]],
    trailing_prefix: str,
) -> list[dict] | None:
    codes: list[str] = []
    values: list[str] = []

    # A vertically merged cell can make the first institution heading appear
    # before the final value-only rows of the preceding block.  The real block
    # begins with its value-only TOTAL VENITURI row immediately before the
    # first indicator-code row, so retain only that last leading value.
    first_code_row = next((
        index
        for index, row in enumerate(rows)
        if _codes(row[1] if len(row) > 1 else "")
    ), len(rows))
    leading_values = [
        tokens
        for row in rows[:first_code_row]
        if (tokens := _values(row[2] if len(row) > 2 else ""))
    ]
    if leading_values:
        values.extend(leading_values[-1])

    for row in rows[first_code_row:]:
        codes.extend(_codes(row[1] if len(row) > 1 else ""))
        values.extend(_values(row[2] if len(row) > 2 else ""))

    if trailing_prefix:
        prefix_match = TRAILING_PREFIX.fullmatch(trailing_prefix)
        if prefix_match is None:
            return None
        codes.append(prefix_match.group("code"))
        values.append(prefix_match.group("value"))

    codes = _normalize_code_order(codes)
    if not codes or codes[0] != "*":
        codes.insert(0, "*")
    if len(codes) != len(values):
        return None

    lines = [mk_line(None, heading, heading, {}, [], None)]
    occurrences: dict[str, int] = {}
    for code, raw_value in zip(codes, values, strict=True):
        occurrences[code] = occurrences.get(code, 0) + 1
        parsed: dict[str, str] = {}
        issues: list[dict] = []
        parse_cell(raw_value, "buget_2026", parsed, issues)
        if issues or "buget_2026" not in parsed:
            return None
        line_section = (
            f"{heading} — EXCEDENT/DEFICIT"
            if code == "90" or code.startswith("93.")
            else heading
        )
        line = mk_line(
            code,
            _canonical_name(code, occurrences[code]),
            line_section,
            parsed,
            [],
            None,
            func_ctx="65.10" if ECONOMIC_CODES.match(code) else None,
        )
        if code == "96":
            # Printed section marker (functionare), not functional chapter
            # 96.02. Keeping it as raw provenance prevents assembly from
            # turning it into a false budget code and polluting analytics.
            line["code"] = None
        lines.append(line)
    return lines


def try_map(grid: list[list[str]]) -> list[dict] | None:
    joined = fold(" ".join(_unique_row_text(row) for row in grid))
    if "buget initial" not in joined or "total venituri" not in joined:
        return None

    blocks: list[tuple[str, list[list[str]], str]] = []
    heading: str | None = None
    rows: list[list[str]] = []
    for row in grid:
        text = _unique_row_text(row)
        match = INSTITUTION_HEADING.search(text)
        if match:
            if heading is not None:
                blocks.append((heading, rows, text[:match.start()].strip()))
            heading = match.group(0).strip()
            rows = []
        elif heading is not None:
            rows.append(row)
    if heading is not None:
        blocks.append((heading, rows, ""))
    if not blocks:
        return None

    lines: list[dict] = []
    for block_heading, block_rows, trailing_prefix in blocks:
        mapped = _map_block(block_heading, block_rows, trailing_prefix)
        if mapped is None:
            return None
        lines.extend(mapped)
    return lines
