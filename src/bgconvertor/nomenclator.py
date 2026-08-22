"""Nomenclator ingestion: official MF XLSX annexes -> code registry.

Sources (see PLAN.md §2): https://mfinante.gov.ro/domenii/buget/clasificatiile-bugetare
Annex filenames embed the amendment date and change URL on every update,
so `update()` scrapes the page instead of hardcoding file URLs.

Files recognized in the reference directory:
    Anexanr2_*.xlsx    - bugete locale (.02): venituri + cheltuieli functionale
    Anexanr10_*.xlsx   - institutii din venituri proprii (.10)
    AnexanrIec_*.xlsx  - clasificatia economica (shared by all budgets)
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import warnings
from pathlib import Path
from typing import Iterator, Literal

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
    "Anexanr2_": ("local", "Anexanr2_*.xlsx"),
    "Anexanr10_": ("own_revenue", "Anexanr10_*.xlsx"),
    "AnexanrIec_": ("economic", "AnexanrIec_*.xlsx"),
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
    m = re.match(r"^([\d.\s]+?)\s*(\*+\)?|\))?$", s)
    if not m:
        return s, ""
    code = m.group(1).replace(" ", "").rstrip(".")
    markers = m.group(2) or ""
    return code, markers


LEVELS_FUNCTIONAL = ["capitol", "subcapitol", "paragraf"]
LEVELS_ECONOMIC = ["titlu", "articol", "alineat"]


def _iter_sheet(ws, kind: Kind, budget: str, levels: list[str]) -> Iterator[Entry]:
    header_seen = False
    for row in ws.iter_rows(values_only=True):
        cells = [(str(c).strip() if c is not None else "") for c in row[:4]]
        if not header_seen:
            if cells[0].lower() in ("capitol", "titlu"):
                header_seen = True
            continue
        code_cols, name = cells[:3], cells[3]
        filled = [(i, v) for i, v in enumerate(code_cols) if v]
        if not filled or not name:
            continue  # heading rows without codes (e.g. "A. VENITURI FISCALE")
        if len(filled) > 1:
            log.warning("row with multiple code columns skipped: %r", cells)
            continue
        idx, raw = filled[0]
        code, markers = normalize_code(raw)
        if not CODE_RE.match(code):
            log.debug("unparseable code %r (name %r) skipped", raw, name)
            continue
        yield Entry(
            code=code,
            name=name,
            kind=kind,
            level=levels[idx],
            budget=budget,
            markers=markers,
            source=ws.title,
        )


def parse_annex(path: Path) -> list[Entry]:
    fname = path.name
    if fname.startswith("AnexanrIec_"):
        specs = [(None, "expense_economic", "all", LEVELS_ECONOMIC)]
    elif fname.startswith("Anexanr2_"):
        specs = [
            ("ven", "revenue", "local", LEVELS_FUNCTIONAL),
            ("ch", "expense_functional", "local", LEVELS_FUNCTIONAL),
        ]
    elif fname.startswith("Anexanr10_"):
        specs = [
            ("ven", "revenue", "own_revenue", LEVELS_FUNCTIONAL),
            ("ch", "expense_functional", "own_revenue", LEVELS_FUNCTIONAL),
        ]
    else:
        raise ValueError(f"unrecognized annex file: {fname}")

    entries: list[Entry] = []
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        for match, kind, budget, levels in specs:
            sheets = [
                ws for ws in wb.worksheets if match is None or match in ws.title.lower()
            ]
            if not sheets:
                raise ValueError(f"{fname}: no sheet matching {match!r} (has {wb.sheetnames})")
            for ws in sheets:
                found = list(_iter_sheet(ws, kind, budget, levels))
                if not found:
                    raise ValueError(f"{fname}/{ws.title}: parsed 0 entries — layout changed?")
                entries.extend(found)
    finally:
        wb.close()
    return entries


def build_registry(reference_dir: Path) -> Registry:
    entries: list[Entry] = []
    sources: dict[str, str] = {}
    for prefix, (_budget, glob) in ANNEX_PATTERNS.items():
        files = sorted(reference_dir.glob(glob))
        if not files:
            if prefix == "Anexanr10_":
                log.warning("no %s file — .10 budgets will validate codes as unknown", glob)
                continue
            raise FileNotFoundError(f"missing required annex {glob} in {reference_dir}")
        newest = files[-1]  # date is embedded in the name; lexicographic works within a year
        entries.extend(parse_annex(newest))
        sources[newest.name] = file_sha256(newest)
    return Registry(
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
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
            for f in sorted(reference_dir.glob(glob))[-1:]
        }
        if current == reg.sources:
            # Rollups and identities are code constants (rules.py), not derived
            # from the XLSX — never trust the cached copy for them.
            return Registry(
                generated_at=reg.generated_at,
                sources=reg.sources,
                entries=reg.entries,
                rollups=rules.ROLLUP_CODES,
                identities=rules.ALL_IDENTITIES,
            )
        log.info("annex files changed on disk — rebuilding registry")
    reg = build_registry(reference_dir)
    save_registry(reg, reference_dir)
    return reg


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
