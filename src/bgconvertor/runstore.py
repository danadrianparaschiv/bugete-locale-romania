"""Persistent per-page, per-stage artifact store.

Layout:
    runs/<pdf-stem>/
        meta.json                 # pdf sha256, page count, config snapshot
        <stage>/p0001.json        # envelope {config_hash, payload, ...}
        <stage>/p0001.failure.json
        debug/<stage>/p0001.*     # PNG overlays etc. (only with --debug)

A stored page result is a cache hit only if its config_hash matches the
current RunConfig's stage_hash — changing a setting or prompt invalidates
exactly the stages that depend on it, nothing else.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import traceback
from pathlib import Path
from typing import Any

from .config import RunConfig


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_key(pdf_path: Path) -> str:
    """Stable per-file key for the run store.

    Corpus-tree files are all named budget_file.pdf, so the key is the
    relative path slug: data/2026/01-alba/1017-alba-iulia/budget_file.pdf
    -> "2026-01-alba-1017-alba-iulia". Flat files keep their stem
    (backward compatible with existing stores).
    """
    try:
        rel = pdf_path.resolve().relative_to(Path.cwd())
    except ValueError:
        rel = pdf_path
    parts = list(rel.parts)
    if len(parts) > 1 and parts[0] == "data":
        slug_parts = parts[1:-1]  # year/county/city
        if rel.stem not in ("budget_file",):
            slug_parts = [*slug_parts, rel.stem]
        return "-".join(slug_parts)
    return pdf_path.stem


class RunStore:
    def __init__(self, config: RunConfig, pdf_path: Path):
        self.config = config
        self.pdf_path = pdf_path
        self.root = config.runs_dir / store_key(pdf_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_meta()

    # -- meta ---------------------------------------------------------------

    def _ensure_meta(self) -> None:
        meta_path = self.root / "meta.json"
        sha = file_sha256(self.pdf_path)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("pdf_sha256") != sha:
                # Same stem, different file: refuse to silently mix artifacts.
                raise RuntimeError(
                    f"{self.root} holds artifacts for a different PDF "
                    f"(stored sha {meta.get('pdf_sha256', '?')[:12]}, current {sha[:12]}). "
                    "Move or delete the run directory."
                )
        meta = {"pdf_sha256": sha, "pdf_path": str(self.pdf_path), "updated_at": _now()}
        meta_path.write_text(json.dumps(meta, indent=2))
        self.pdf_sha = sha
        self.config.dump(self.root / "config.json")

    # -- paths --------------------------------------------------------------

    def _page_path(self, stage: str, page: int) -> Path:
        d = self.root / stage
        d.mkdir(parents=True, exist_ok=True)
        return d / f"p{page:04d}.json"

    def _failure_path(self, stage: str, page: int) -> Path:
        return self._page_path(stage, page).with_suffix(".failure.json")

    def debug_dir(self, stage: str) -> Path:
        d = self.root / "debug" / stage
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- get/put ------------------------------------------------------------

    def get(self, stage: str, page: int) -> Any | None:
        """Return the cached payload for (stage, page), or None on miss/stale."""
        path = self._page_path(stage, page)
        if not path.exists():
            return None
        envelope = json.loads(path.read_text())
        if envelope.get("config_hash") != self.config.stage_hash(stage):
            return None
        return envelope["payload"]

    def put(self, stage: str, page: int, payload: Any) -> None:
        envelope = {
            "pdf_sha256": self.pdf_sha,
            "stage": stage,
            "page": page,
            "config_hash": self.config.stage_hash(stage),
            "created_at": _now(),
            "payload": payload,
        }
        path = self._page_path(stage, page)
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2))
        # A success clears any previous failure record.
        self._failure_path(stage, page).unlink(missing_ok=True)

    def record_failure(self, stage: str, page: int, exc: BaseException) -> None:
        record = {
            "stage": stage,
            "page": page,
            "config_hash": self.config.stage_hash(stage),
            "created_at": _now(),
            "error": repr(exc),
            "traceback": "".join(traceback.format_exception(exc)),
        }
        self._failure_path(stage, page).write_text(json.dumps(record, indent=2))

    def failures(self, stage: str | None = None) -> list[dict]:
        pattern = f"{stage or '*'}/p*.failure.json"
        return [json.loads(p.read_text()) for p in sorted(self.root.glob(pattern))]

    def record_timing(self, stage: str, pages: int, seconds: float) -> None:
        """Accumulate measured throughput; feeds data-driven ETAs."""
        path = self.root / "timings.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        entry = data.setdefault(stage, {"pages": 0, "seconds": 0.0})
        entry["pages"] += pages
        entry["seconds"] += round(seconds, 1)
        path.write_text(json.dumps(data, indent=2))

    def timing_rate(self, stage: str) -> float | None:
        """Measured seconds/page for a stage, if this file has history."""
        path = self.root / "timings.json"
        if not path.exists():
            return None
        entry = json.loads(path.read_text()).get(stage)
        if not entry or entry["pages"] < 5:
            return None
        return entry["seconds"] / entry["pages"]

    def pages_done(self, stage: str) -> list[int]:
        done = []
        for p in sorted(self.root.glob(f"{stage}/p[0-9]*.json")):
            if p.name.endswith(".failure.json"):
                continue
            envelope = json.loads(p.read_text())
            if envelope.get("config_hash") == self.config.stage_hash(stage):
                done.append(envelope["page"])
        return done


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
