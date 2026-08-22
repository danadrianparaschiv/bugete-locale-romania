"""The data/<year>/manifest.json is the corpus's identity source.

Each entry names a county-seat municipality by its official SIRUTA code,
its source URL, and its PDF path; batch conversion writes its status back
here so the manifest doubles as the corpus's progress ledger.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bgc.manifest")


@dataclass
class CityEntry:
    siruta: str
    name: str
    county_code: str
    county_name: str
    pdf: Path  # absolute path
    entry: dict  # raw manifest entry (mutated by status updates)


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text())
        self.year = self.data.get("year")
        self.root = path.parent

    def cities(self) -> list[CityEntry]:
        out = []
        seen_paths: set[str] = set()
        for e in self.data.get("entries", []):
            rel = e.get("path")
            if not rel or rel in seen_paths:
                continue  # Ilfov/Bucharest share one file — count it once
            seen_paths.add(rel)
            out.append(CityEntry(
                siruta=str(e.get("capital_siruta")),
                name=e.get("capital_name", "?"),
                county_code=str(e.get("county_code")),
                county_name=e.get("county_name", "?"),
                pdf=(self.root / rel),
                entry=e,
            ))
        return out

    def find(self, key: str) -> CityEntry | None:
        """Look up by SIRUTA code or (folded) city name."""
        key_l = key.lower()
        for c in self.cities():
            if c.siruta == key or c.name.lower() == key_l:
                return c
        return None

    def by_pdf(self, pdf: Path) -> CityEntry | None:
        rp = pdf.resolve()
        for c in self.cities():
            if c.pdf.resolve() == rp:
                return c
        return None

    def set_status(self, city: CityEntry, **fields) -> None:
        # Merge onto a fresh read of the file: a long-lived process (batch)
        # must not clobber fields another process added since our load.
        try:
            fresh = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            fresh = None
        if fresh is not None:
            rel = city.entry.get("path")
            for e in fresh.get("entries", []):
                if e.get("path") == rel:
                    conv = dict(e.get("conversion") or {})
                    conv.update(city.entry.get("conversion") or {})
                    conv.update(fields)
                    e["conversion"] = conv
                    city.entry = e  # keep later updates on the fresh dict
            self.data = fresh
        else:
            city.entry.setdefault("conversion", {}).update(fields)
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))


def find_manifest(start: Path) -> Manifest | None:
    """Manifest governing a PDF path (data/<year>/manifest.json above it)."""
    for parent in [start, *start.resolve().parents]:
        cand = parent / "manifest.json"
        if cand.exists() and parent.parent.name == "data":
            return Manifest(cand)
    return None


def default_manifest(cwd: Path | None = None) -> Manifest | None:
    root = (cwd or Path.cwd()) / "data"
    if not root.is_dir():
        return None
    years = sorted((d for d in root.iterdir() if (d / "manifest.json").exists()), reverse=True)
    return Manifest(years[0] / "manifest.json") if years else None
