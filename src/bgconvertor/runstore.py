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
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any

from .config import RunConfig


def _read_json(path: Path) -> Any | None:
    """Read a JSON artifact, returning ``None`` for an incomplete cache file.

    OCR workers publish page artifacts concurrently while the parent polls the
    run store.  Legacy versions wrote directly to the destination, so an
    interrupted or in-progress write can be empty/truncated.  Such an artifact
    is a cache miss, never a reason to abort the whole document conversion.
    """
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, payload: Any, *, ensure_ascii: bool = True) -> None:
    """Publish JSON atomically so concurrent readers see old-or-new, never half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=ensure_ascii, indent=2)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


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
    from .config import project_root

    try:
        rel = pdf_path.resolve().relative_to(project_root(pdf_path.resolve().parent))
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
            meta = _read_json(meta_path)
            if not isinstance(meta, dict):
                raise RuntimeError(
                    f"{meta_path} is malformed. Move the run directory aside "
                    "before rebuilding it."
                )
            if meta.get("pdf_sha256") != sha:
                # Same stem, different file: refuse to silently mix artifacts.
                raise RuntimeError(
                    f"{self.root} holds artifacts for a different PDF "
                    f"(stored sha {meta.get('pdf_sha256', '?')[:12]}, current {sha[:12]}). "
                    "Move or delete the run directory."
                )
        meta = {"pdf_sha256": sha, "pdf_path": str(self.pdf_path), "updated_at": _now()}
        _write_json(meta_path, meta)
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
        envelope = _read_json(path)
        if not isinstance(envelope, dict):
            return None
        if envelope.get("config_hash") != self.config.stage_hash(stage):
            return None
        return envelope.get("payload")

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
        _write_json(path, envelope, ensure_ascii=False)
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
        _write_json(self._failure_path(stage, page), record)

    def failures(self, stage: str | None = None) -> list[dict]:
        pattern = f"{stage or '*'}/p*.failure.json"
        return [
            record
            for p in sorted(self.root.glob(pattern))
            if isinstance((record := _read_json(p)), dict)
        ]

    def record_timing(self, stage: str, pages: int, seconds: float) -> None:
        """Accumulate measured throughput; feeds data-driven ETAs."""
        path = self.root / "timings.json"
        data = _read_json(path) or {}
        entry = data.setdefault(stage, {"pages": 0, "seconds": 0.0})
        entry["pages"] += pages
        entry["seconds"] += round(seconds, 1)
        _write_json(path, data)

    def timing_rate(self, stage: str) -> float | None:
        """Measured seconds/page for a stage, if this file has history."""
        path = self.root / "timings.json"
        if not path.exists():
            return None
        data = _read_json(path)
        if not isinstance(data, dict):
            return None
        entry = data.get(stage)
        if not entry or entry["pages"] < 5:
            return None
        return entry["seconds"] / entry["pages"]

    def pages_done(self, stage: str) -> list[int]:
        done = []
        for p in sorted(self.root.glob(f"{stage}/p[0-9]*.json")):
            if p.name.endswith(".failure.json"):
                continue
            envelope = _read_json(p)
            if not isinstance(envelope, dict):
                continue
            if envelope.get("config_hash") == self.config.stage_hash(stage):
                done.append(envelope["page"])
        return done


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
