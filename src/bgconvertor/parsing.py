"""Pure parsing functions: Romanian numbers and indicator codes.

These are the most bug-prone atoms of the pipeline, so they are
side-effect-free and exhaustively tested (incl. hypothesis round-trips).

Romanian budget number format: '.' thousands separator, ',' decimal comma —
"1.234.567,89". Cells may also hold "X" (not applicable), "-" or "" (empty).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

# Unicode lookalikes OCR and PDF extractors produce.
_MINUS_CHARS = "-−–—"  # -, −, –, —
_SPACE_RE = re.compile(r"[\s   ]+")

ParsedCell = Decimal | Literal["X"] | None


class NumberParseError(ValueError):
    pass


def parse_ro_number(raw: str | None, ocr: bool = False) -> ParsedCell:
    """Parse a Romanian-formatted budget cell.

    Returns Decimal for numbers, "X" for the not-applicable marker,
    None for empty/dash cells. Raises NumberParseError on garbage —
    callers turn that into an Issue, never a silent zero.

    ocr=True enables one OCR-specific leniency: a single dot followed by
    exactly two digits ("48152.87") is read as a misrecognized decimal
    comma — scans mix both styles on the same page.
    """
    if raw is None:
        return None
    s = _SPACE_RE.sub("", str(raw))
    if s == "":
        return None
    if s.upper() == "X":
        return "X"
    if ocr:
        # OCR misreads of the not-applicable 'x' marker: \x, 1x, x1, ix, |x...
        core = re.sub(r"[^0-9a-zA-Z×区]", "", s).lower()
        if core in ("x", "xx", "1x", "x1", "ix", "lx", "xi", "×", "区") and "x" in core.replace("×", "x").replace("区", "x"):
            return "X"
        if s == "?":
            return None  # OCR gave up on the cell — empty, not zero
    if s in tuple(_MINUS_CHARS):
        return None  # a lone dash is an empty cell, not zero

    negative = False
    if s[0] in _MINUS_CHARS:
        negative = True
        s = s[1:]
    elif s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    if not s:
        raise NumberParseError(f"sign without digits: {raw!r}")

    if ocr and "," not in s and s.count(".") == 1:
        intpart, _, tail = s.partition(".")
        if len(tail) in (1, 2) and tail.isdigit() and intpart.isdigit():
            # 48152.87 / 30.0 -> decimal comma misread (RO thousands are
            # always 3 digits, so a 1-2 digit tail can't be a thousands group)
            s = f"{intpart},{tail}"
    if ocr and "," in s and "." in s and s.rindex(".") > s.rindex(","):
        # US-style print (Bacau): 19,809.00 -> comma=thousands, dot=decimal
        int_part, _, dec = s.rpartition(".")
        groups = int_part.split(",")
        if dec.isdigit() and len(dec) == 2 and all(g.isdigit() for g in groups) and all(
            len(g) == 3 for g in groups[1:]
        ):
            s = "".join(groups) + "," + dec
    if ocr and s.count(",") > 1:
        # 15,735,00 -> 15.735,00 (thousands dots misread as commas)
        head, _, tail = s.rpartition(",")
        s = head.replace(",", ".") + "," + tail
    if ocr and "," not in s and s.count(".") >= 2:
        head, _, tail = s.rpartition(".")
        if len(tail) == 2 and tail.isdigit():
            s = head + "," + tail  # 91.123.00 -> 91.123,00 (misread comma)

    # Split decimal part on the LAST comma; dots are thousands separators.
    if "," in s:
        int_part, _, dec_part = s.rpartition(",")
        if not dec_part or not dec_part.isdigit():
            raise NumberParseError(f"bad decimals in {raw!r}")
    else:
        int_part, dec_part = s, ""

    groups = int_part.split(".")
    if any(g == "" for g in groups) or not all(g.isdigit() for g in groups):
        raise NumberParseError(f"bad integer part in {raw!r}")
    # Thousands groups after the first must be exactly 3 digits:
    # "1.234" is 1234, but "1.23" or "12.3456" is a misread.
    if len(groups) > 1 and any(len(g) != 3 for g in groups[1:]):
        raise NumberParseError(f"bad thousands grouping in {raw!r}")

    try:
        value = Decimal("".join(groups) + ("." + dec_part if dec_part else ""))
    except InvalidOperation:  # pragma: no cover - guarded above
        raise NumberParseError(f"unparseable number {raw!r}") from None
    return -value if negative else value


def format_ro_number(value: Decimal, decimals: int = 2) -> str:
    """Decimal -> canonical Romanian display form: 1.234.567,89."""
    q = value.quantize(Decimal(1).scaleb(-decimals)) if decimals else value
    sign = "-" if q < 0 else ""
    digits, _, dec = f"{abs(q):f}".partition(".")
    grouped = f"{int(digits):,}".replace(",", ".")
    dec = (dec + "0" * decimals)[:decimals]
    return f"{sign}{grouped}" + (f",{dec}" if decimals else "")


# -- indicator codes --------------------------------------------------------

_DOTTED_RE = re.compile(r"^\d{2}(\.\d{2}){0,3}$")
_COMPACT_RE = re.compile(r"^\d{2}(\d{2}){0,3}$")


def split_combined_code(
    raw: str | None, aggressive: bool = False
) -> tuple[str | None, str | None]:
    """Printed code -> (code, func_code).

    Combined vendor format: '5102.200130' / '7002.1001' = functional capitol
    + economic code -> ('20.01.30', '51.02'). Plain codes -> (code, None).

    aggressive=True (digital 'detaliat' vendors): any dot splits — these
    vendors never print bare dotted economic codes, and truncated prefixes
    ('02.01' for '5002.01') must split for assembly's capitol repair.
    aggressive=False (scanned): only unambiguous shapes split, because
    dotted economic codes like '59.01' are printed as-is on scans.
    """
    if not raw:
        return None, None
    if "." in raw:
        func_raw, _, econ_raw = raw.partition(".")
        func_raw, econ_raw = func_raw.strip(), econ_raw.strip()
        compact_econ = econ_raw.isdigit() and len(econ_raw) in (2, 4, 6)
        if compact_econ and func_raw.isdigit() and (
            aggressive
            or len(func_raw) == 4
            or (len(func_raw) == 2 and len(econ_raw) >= 4)
        ):
            func = normalize_indicator_code(func_raw)
            econ = normalize_indicator_code(econ_raw)
            if func and econ:
                return econ, func
    return normalize_indicator_code(raw), None


def normalize_indicator_code(raw: str | None) -> str | None:
    """Normalize a printed indicator code to dotted form.

    Handles both dotted ("42.02.93.01") and compact ("65020301") prints.
    Returns None for pseudo/form codes ("D", "01F", "F", "*") and garbage —
    the caller decides whether that is an error for the row in question.
    """
    if raw is None:
        return None
    stripped = str(raw).strip().replace("/", ".")
    # space-separated code halves ('42 55', '65. 00.60') — some vendors and
    # OCR print separators as spaces or slashes
    if re.fullmatch(r"\d{2}[ .]+\d{2}([ .]+\d{2}){0,2}", stripped):
        stripped = re.sub(r"[ .]+", ".", stripped)
    s = _SPACE_RE.sub("", stripped).rstrip("*").rstrip(")").rstrip("*")
    if not s:
        return None
    # funding-source letter glued to a capitol code (Timișoara '51.02A',
    # PMB-family '50.02A'): the letter is the source variant, not the code
    m_src = re.fullmatch(r"(\d{2}\.\d{2})[A-Z]", s)
    if m_src:
        s = m_src.group(1)
    if "," in s and "." not in s:
        s = s.replace(",", ".")  # vendor/OCR comma-printed codes: 59,40
    m3 = re.match(r"^00(\d)\.(\d{2})$", s)
    if m3:
        return f"00.0{m3.group(1)}.{m3.group(2)}"  # vendor rollup print '001.02'
    if _DOTTED_RE.match(s):
        # some vendors pad articles with a phantom '.00' alineat (20.02.00);
        # no real code has segment '00' beyond position one
        parts = s.split(".")
        if len(parts) > 2 and parts[-1] == "00":
            s = ".".join(parts[:-1])
        return s
    if _COMPACT_RE.match(s) and len(s) in (2, 4, 6, 8, 10):
        return ".".join(s[i : i + 2] for i in range(0, len(s), 2))
    return None
