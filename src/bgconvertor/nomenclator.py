"""Nomenclator ingestion: official MF XLS/XLSX annexes -> code registry.

Sources (see PLAN.md §2): https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare
Annex filenames embed the amendment date and change URL on every update,
so `update()` scrapes the page instead of hardcoding file URLs.

Files recognized in the reference directory:
    Anexanr2_*.xls[x]    - bugete locale (.02): venituri + cheltuieli functionale
    Anexanr10_*.xls[x]   - institutii din venituri proprii (.10)
    AnexanrIec_*.xls[x]  - clasificatia economica (shared by all budgets)
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import warnings
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal

import openpyxl

# The MF files carry unparseable header/footer records; harmless, and the
# warning fires lazily on sheet iteration, so filter it module-wide.
warnings.filterwarnings("ignore", message="Cannot parse header or footer")
from pydantic import BaseModel

from . import rules
from .runstore import file_sha256

log = logging.getLogger("bgc.nomenclator")

MF_PAGE = "https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare"
ANNEX_PATTERNS = {
    "Anexanr2_": ("local", "Anexanr2_*"),
    "Anexanr10_": ("own_revenue", "Anexanr10_*"),
    "AnexanrIec_": ("economic", "AnexanrIec_*"),
}

CODE_RE = re.compile(r"^\d{2}(\.\d{2}){0,3}$")

Kind = Literal["revenue", "expense_functional", "expense_economic"]


class Entry(BaseModel):
    code: str  # normalized dotted form, markers stripped
    name: str
    kind: Kind
    level: str  # capitol|subcapitol|paragraf | titlu|articol|alineat
    budget: str  # "local" (.02) | "own_revenue" (.10) | "all" (economic)
    markers: str = ""  # raw footnote markers: "*", "**", "*)" ...
    source: str = ""  # sheet the entry came from


class Registry(BaseModel):
    generated_at: str
    effective_year: int | None = None
    sources: dict[str, str]  # filename -> sha256
    entries: list[Entry]
    rollups: list[rules.Rollup] = rules.ROLLUP_CODES
    identities: list[rules.Identity] = rules.ALL_IDENTITIES

    def model_post_init(self, __context) -> None:
        self._by_code: dict[tuple[str, str], Entry] = {}
        self._children: dict[tuple[str, str], list[str]] = {}
        for e in self.entries:
            self._by_code[(e.kind, e.code)] = e
            parent = parent_code(e.code, e.kind)
            if parent:
                self._children.setdefault((e.kind, parent), []).append(e.code)
        self._rollup_codes = {r.code for r in self.rollups}

    def get(self, kind: str, code: str) -> Entry | None:
        return self._by_code.get((kind, code))

    def exists(self, code: str, kind: str | None = None) -> bool:
        if code in self._rollup_codes:
            return True
        if kind:
            return (kind, code) in self._by_code
        return any((k, code) in self._by_code for k in ("revenue", "expense_functional", "expense_economic"))

    def children(self, kind: str, code: str) -> list[str]:
        return self._children.get((kind, code), [])

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            key = f"{e.budget}/{e.kind}"
            out[key] = out.get(key, 0) + 1
        out["rollups"] = len(self.rollups)
        out["identities"] = len(self.identities)
        return out


def parent_code(code: str, kind: str) -> str | None:
    """65.02.04.01 -> 65.02.04; economic 10.01.01 -> 10.01 -> 10.

    The kind is required: code strings alone are ambiguous — economic articol
    "20.10" (Cercetare-dezvoltare) would otherwise collide with the rule that
    a functional capitol "cc.10" is a two-segment unit with no parent.
    """
    parts = code.split(".")
    if len(parts) <= 1:
        return None
    if kind == "expense_economic":
        return ".".join(parts[:-1])
    # revenue / expense_functional: capitol "cc.bb" is one unit, no parent
    if len(parts) == 2:
        return None
    return ".".join(parts[:-1])


def normalize_code(raw: str) -> tuple[str, str]:
    """'66.02.06.04*' -> ('66.02.06.04', '*'); returns (code, markers)."""
    s = str(raw).strip()
    # xlrd exposes integer-valued numeric cells as ``51.0``.  MF also has a
    # small run of economic codes written as ``58 06 01`` instead of using
    # dots.  Both are spreadsheet representation differences, not new codes.
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    m = re.match(r"^([\d.\s]+?)\s*(\*+\)?|\))?$", s)
    if not m:
        return s, ""
    raw_code = m.group(1).strip().rstrip(".")
    if "." not in raw_code and re.search(r"\s", raw_code):
        code = ".".join(raw_code.split())
    else:
        code = re.sub(r"\s+", "", raw_code)
    markers = m.group(2) or ""
    return code, markers


LEVELS_FUNCTIONAL = ["capitol", "subcapitol", "paragraf"]
LEVELS_ECONOMIC = ["titlu", "articol", "alineat"]


def _iter_sheet(
    rows: Iterable[tuple], title: str, kind: Kind, budget: str, levels: list[str]
) -> Iterator[Entry]:
    header_seen = False
    for row in rows:
        cells = [(str(c).strip() if c is not None else "") for c in row[:4]]
        if not header_seen:
            if cells[0].lower() in ("capitol", "titlu"):
                header_seen = True
            continue
        code_cols, name = cells[:3], cells[3]
        # Historical MF workbooks put annotations such as ``din 2024`` or
        # ``modif. denumire`` in otherwise empty code columns.  Select the one
        # code-shaped cell instead of rejecting a valid row merely because an
        # adjacent annotation is populated.
        filled = []
        for index, raw in enumerate(code_cols):
            code, markers = normalize_code(raw)
            if CODE_RE.match(code):
                filled.append((index, code, markers))
        if not filled or not name:
            continue  # heading rows without codes (e.g. "A. VENITURI FISCALE")
        if len(filled) > 1:
            log.warning("row with multiple code columns skipped: %r", cells)
            continue
        idx, code, markers = filled[0]
        yield Entry(
            code=code,
            name=name,
            kind=kind,
            level=levels[idx],
            budget=budget,
            markers=markers,
            source=title,
        )


def _sheet_rows(path: Path) -> list[tuple[str, list[tuple]]]:
    """Open an official annex without converting its source format.

    The Ministry published the 2024 edition as binary Excel 97-2003 files and
    the 2026 edition as OOXML.  Parsing both directly keeps the official bytes
    and their hashes as the auditable inputs.
    """
    if path.suffix.lower() == ".xls":
        import xlrd

        workbook = xlrd.open_workbook(path, on_demand=True)
        try:
            return [
                (
                    sheet.name,
                    [tuple(sheet.row_values(index)) for index in range(sheet.nrows)],
                )
                for sheet in workbook.sheets()
            ]
        finally:
            workbook.release_resources()

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [
            (sheet.title, [tuple(row) for row in sheet.iter_rows(values_only=True)])
            for sheet in workbook.worksheets
        ]
    finally:
        workbook.close()


def _matching_sheets(
    sheets: list[tuple[str, list[tuple]]], role: str | None
) -> list[tuple[str, list[tuple]]]:
    if role is None:
        return sheets
    out = []
    for title, rows in sheets:
        folded = title.lower()
        is_revenue = "ven" in folded
        is_functional = "funct" in folded or ("ch" in folded and not is_revenue)
        if (role == "revenue" and is_revenue) or (
            role == "functional" and is_functional
        ):
            out.append((title, rows))
    return out


def parse_annex(path: Path) -> list[Entry]:
    fname = path.name
    if fname.startswith("AnexanrIec_"):
        specs = [(None, "expense_economic", "all", LEVELS_ECONOMIC)]
    elif fname.startswith("Anexanr2_"):
        specs = [
            ("revenue", "revenue", "local", LEVELS_FUNCTIONAL),
            ("functional", "expense_functional", "local", LEVELS_FUNCTIONAL),
        ]
    elif fname.startswith("Anexanr10_"):
        specs = [
            ("revenue", "revenue", "own_revenue", LEVELS_FUNCTIONAL),
            ("functional", "expense_functional", "own_revenue", LEVELS_FUNCTIONAL),
        ]
    else:
        raise ValueError(f"unrecognized annex file: {fname}")

    entries: list[Entry] = []
    sheets = _sheet_rows(path)
    titles = [title for title, _ in sheets]
    for match, kind, budget, levels in specs:
        selected = _matching_sheets(sheets, match)
        if not selected:
            raise ValueError(f"{fname}: no sheet matching {match!r} (has {titles})")
        for title, rows in selected:
            found = list(_iter_sheet(rows, title, kind, budget, levels))
            if not found:
                raise ValueError(f"{fname}/{title}: parsed 0 entries — layout changed?")
            entries.extend(found)
    return entries


def _annex_files(reference_dir: Path, pattern: str) -> list[Path]:
    return sorted(
        path
        for path in reference_dir.glob(pattern)
        if path.suffix.lower() in {".xls", ".xlsx"}
    )


def build_registry(reference_dir: Path) -> Registry:
    entries: list[Entry] = []
    sources: dict[str, str] = {}
    for prefix, (_budget, glob) in ANNEX_PATTERNS.items():
        files = _annex_files(reference_dir, glob)
        if not files:
            if prefix == "Anexanr10_":
                log.warning("no %s file — .10 budgets will validate codes as unknown", glob)
                continue
            raise FileNotFoundError(f"missing required annex {glob} in {reference_dir}")
        newest = files[-1]  # date is embedded in the name; lexicographic works within a year
        entries.extend(parse_annex(newest))
        sources[newest.name] = file_sha256(newest)
    return Registry(
        generated_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        effective_year=(
            int(reference_dir.name) if reference_dir.name.isdigit() else None
        ),
        sources=sources,
        entries=entries,
    )


def registry_path(reference_dir: Path) -> Path:
    return reference_dir / "registry.json"


def save_registry(reg: Registry, reference_dir: Path) -> Path:
    path = registry_path(reference_dir)
    path.write_text(reg.model_dump_json(indent=2))
    return path


def load_registry(reference_dir: Path) -> Registry:
    """Load cached registry; rebuild if missing or its sources changed on disk."""
    path = registry_path(reference_dir)
    if path.exists():
        reg = Registry.model_validate_json(path.read_text())
        current = {
            f.name: file_sha256(f)
            for _, (_b, glob) in ANNEX_PATTERNS.items()
            for f in _annex_files(reference_dir, glob)[-1:]
        }
        if current == reg.sources:
            # Rollups and identities are code constants (rules.py), not derived
            # from the XLSX — never trust the cached copy for them.
            return Registry(
                generated_at=reg.generated_at,
                effective_year=reg.effective_year,
                sources=reg.sources,
                entries=reg.entries,
                rollups=rules.ROLLUP_CODES,
                identities=rules.ALL_IDENTITIES,
            )
        log.info("annex files changed on disk — rebuilding registry")
    reg = build_registry(reference_dir)
    save_registry(reg, reference_dir)
    return reg


def reference_dir_for_year(reference_dir: Path, year: int | None) -> Path:
    """Choose a committed historical registry when the corpus year has one."""
    if year is not None:
        candidate = reference_dir / str(year)
        if candidate.is_dir():
            return candidate
        if year != 2026:
            log.warning(
                "no nomenclator snapshot for %s; falling back to %s",
                year,
                reference_dir,
            )
    return reference_dir


def load_registry_for_year(reference_dir: Path, year: int | None) -> Registry:
    return load_registry(reference_dir_for_year(reference_dir, year))


# -- update (network) -------------------------------------------------------

def update(reference_dir: Path) -> list[str]:
    """Scrape the MF page and download annexes newer than what we have."""
    import httpx

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh) bgconvertor/0.1"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
        page = _get_with_retries(client, MF_PAGE).text
        hrefs = set(re.findall(r'href="([^"]*clasificatii/[^"]+\.xlsx?)"', page))
        downloaded: list[str] = []
        for href in sorted(hrefs):
            fname = href.rsplit("/", 1)[-1]
            if not any(fname.startswith(p) for p in ANNEX_PATTERNS):
                continue
            target = reference_dir / fname
            if target.exists():
                continue
            url = href if href.startswith("http") else "https://mfinante.gov.ro" + href
            log.info("downloading %s", url)
            target.write_bytes(_get_with_retries(client, url).content)
            downloaded.append(fname)
    if downloaded:
        save_registry(build_registry(reference_dir), reference_dir)
    return downloaded


def _get_with_retries(client, url: str, attempts: int = 4):
    import httpx

    last: Exception | None = None
    for i in range(attempts):
        try:
            r = client.get(url)
            r.raise_for_status()
            return r
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc  # the MF server resets connections aggressively; retry
            log.debug("attempt %d for %s failed: %r", i + 1, url, exc)
    raise RuntimeError(f"failed to fetch {url} after {attempts} attempts") from last
