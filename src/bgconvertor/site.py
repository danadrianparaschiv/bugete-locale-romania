"""Static GitHub Pages site: corpus index + one page per converted city.

Renders exclusively from committed files (manifest + analysis.json +
workbooks referenced by relative link), so it runs identically locally and
in a GitHub Action.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .manifest import Manifest

log = logging.getLogger("bgc.site")


def _ro_date(iso: str | None) -> str | None:
    """'2026-04-28' -> '28.04.2026'; passes through anything non-ISO."""
    if not iso:
        return None
    parts = iso.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        return f"{d}.{m}.{y}"
    return iso


REPO_RAW = "https://github.com/danadrianparaschiv/bugete-locale-romania/raw/main"
GITHUB_FILE_LIMIT = 100 * 1024 * 1024  # files over this are not in git (see .gitignore)


def build(manifest: Manifest, out: Path, base_url: str = "", raw_base: str = REPO_RAW) -> dict:
    env = Environment(
        loader=PackageLoader("bgconvertor", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "city").mkdir(exist_ok=True)

    cities = []
    n_converted = 0
    for c in sorted(manifest.cities(), key=lambda c: c.county_code):
        conv = c.entry.get("conversion") or {}
        analysis = None
        apath = c.pdf.with_name("analysis.json")
        if conv.get("status") == "converted" and apath.exists():
            analysis = json.loads(apath.read_text())
            n_converted += 1
        row = {
            "siruta": c.siruta,
            "name": c.name,
            "county": c.county_name,
            "county_code": c.county_code,
            "status": conv.get("status") or "pending",
            "pct_clean": conv.get("pct_clean"),
            "lines": conv.get("lines"),
            "analysis": analysis,
            "source_url": c.entry.get("source_url"),
            "pdf_rel": None,
            "xlsx_rel": None,
        }
        tl = c.entry.get("timeline") or {}
        row["debate_date"] = _ro_date(tl.get("debate_date"))
        row["debate_url"] = tl.get("debate_url")
        row["approved_date"] = _ro_date(tl.get("approved_date"))
        row["approved_url"] = tl.get("approved_url")
        row["hcl"] = tl.get("hcl")
        row["timeline_notes"] = tl.get("notes")
        # committed artifacts are served from the git repo, not the Pages
        # artifact; files over GitHub's size limit are not in git at all
        repo_root = manifest.root.parent.parent
        if c.pdf.exists() and c.pdf.stat().st_size <= GITHUB_FILE_LIMIT:
            row["pdf_rel"] = str(c.pdf.relative_to(repo_root))
        xlsx = c.pdf.with_suffix(".xlsx")
        if xlsx.exists():
            row["xlsx_rel"] = str(xlsx.relative_to(repo_root))
        cities.append(row)

        if analysis:
            page = env.get_template("city.html").render(
                city=row, a=analysis, year=manifest.year, base=base_url, raw=raw_base,
            )
            (out / "city" / f"{c.siruta}.html").write_text(page)

    index = env.get_template("index.html").render(
        cities=cities, year=manifest.year, n_converted=n_converted,
        base=base_url, raw=raw_base,
    )
    (out / "index.html").write_text(index)

    disclaimer = manifest.root.parent.parent / "DISCLAIMER.md"
    if disclaimer.exists():
        shutil.copy(disclaimer, out / "DISCLAIMER.md")
    return {"cities": len(cities), "converted_pages": n_converted, "out": str(out)}
