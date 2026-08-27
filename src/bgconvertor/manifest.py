"""The data/<year>/manifest.json is the corpus's identity source.

Each entry names a county-seat municipality by its official SIRUTA code,
its source URL, and its source path (PDF or native Excel); batch conversion
writes its status back here so the manifest doubles as the corpus's progress
ledger.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bgc.manifest")


@dataclass
class CityEntry:
    siruta: str
    name: str
    county_code: str
    county_name: str
    pdf: Path  # absolute source path (legacy attribute name; PDF or native Excel)
    entry: dict  # raw manifest entry (mutated by status updates)

    @property
    def source_format(self) -> str:
        return str(self.entry.get("source_format") or self.pdf.suffix.lstrip(".")).lower()

    @property
    def workbook(self) -> Path:
        """Normalized public workbook, distinct from a native source workbook."""
        if self.source_format in {"xls", "xlsx"}:
            return self.pdf.with_name("budget_file.xlsx")
        return self.pdf.with_suffix(".xlsx")

    @property
    def analysis(self) -> Path:
        return self.pdf.with_name("analysis.json")


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text())
        self.year = self.data.get("year")
        self.root = path.parent

    def cities(self) -> list[CityEntry]:
        # Preserve manifest order while choosing one canonical identity for a
        # shared file.  Ilfov and municipality București deliberately point at
        # the same PDF; code 42 is the document owner's identity and must win
        # over the Ilfov alias for SIRUTA/NUTS joins.
        by_path: dict[str, dict] = {}
        order: list[str] = []
        for e in self.data.get("entries", []):
            rel = e.get("path")
            if not rel:
                continue
            if rel not in by_path:
                by_path[rel] = e
                order.append(rel)
            elif str(e.get("county_code")) == "42":
                by_path[rel] = e

        out = []
        for rel in order:
            e = by_path[rel]
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
        def merge_conversion(existing: dict) -> dict:
            conversion = dict(existing)
            conversion.update(city.entry.get("conversion") or {})
            conversion.update(fields)
            if fields.get("status") == "converted":
                # Failure diagnostics describe an obsolete attempt once a
                # complete public bundle has been committed successfully.
                conversion.pop("error", None)
                conversion.pop("last_attempt_error", None)
            return conversion

        try:
            fresh = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            fresh = None
        if fresh is not None:
            rel = city.entry.get("path")
            for e in fresh.get("entries", []):
                if e.get("path") == rel:
                    e["conversion"] = merge_conversion(e.get("conversion") or {})
                    city.entry = e  # keep later updates on the fresh dict
            self.data = fresh
        else:
            city.entry["conversion"] = merge_conversion(
                city.entry.get("conversion") or {}
            )
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise


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
