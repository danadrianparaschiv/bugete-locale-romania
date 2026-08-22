"""Fail-soft page orchestration.

Runs a stage function over a set of pages: cached pages are skipped, a
page that raises is recorded as a failure artifact and the run continues
(unless fail_fast). Every run ends with an honest summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import time

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .logging_setup import page_logger
from .runstore import RunStore


@dataclass
class StageSummary:
    stage: str
    ok: list[int] = field(default_factory=list)
    cached: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"{self.stage}: {len(self.ok)} ok / {len(self.cached)} cached / "
            f"{len(self.failed)} failed"
            + (f" (pages {_compact(self.failed)})" if self.failed else "")
        )


def run_stage(
    store: RunStore,
    stage: str,
    pages: list[int],
    fn: Callable[[int], Any],
    show_progress: bool = True,
    concurrency: int = 1,
) -> StageSummary:
    """Apply fn(page) -> payload for each page not already cached.

    concurrency > 1 runs fn in a thread pool — safe for network-bound
    stages (LLM calls); keep 1 for CPU-bound work (use process workers).
    """
    summary = StageSummary(stage=stage)
    log = page_logger(store.pdf_path.stem, stage)

    progress_ctx = (
        Progress(
            TextColumn(f"[bold]{stage}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            transient=True,
        )
        if show_progress
        else None
    )

    start = time.monotonic()
    heartbeat = {"last": start}

    def process(page: int) -> None:
        if store.get(stage, page) is not None:
            summary.cached.append(page)
            return
        try:
            payload = fn(page)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            store.record_failure(stage, page, exc)
            summary.failed.append(page)
            page_logger(store.pdf_path.stem, stage, page).warning("failed: %r", exc)
            if store.config.fail_fast:
                raise
            return
        store.put(stage, page, payload)
        summary.ok.append(page)
        # heartbeat every 60s: visible in terminals AND in redirected logs,
        # with the live rate and a data-driven ETA
        now = time.monotonic()
        if now - heartbeat["last"] >= 60 and summary.ok:
            heartbeat["last"] = now
            done = len(summary.ok) + len(summary.cached) + len(summary.failed)
            rate = (now - start) / max(1, len(summary.ok))
            remaining = (len(pages) - done) * rate
            log.info(
                "%d/%d pages (%d cached, %d failed) — %.1fs/page, ETA %s",
                done, len(pages), len(summary.cached), len(summary.failed),
                rate, f"{remaining/60:.0f} min" if remaining > 90 else f"{remaining:.0f}s",
            )

    def _run_all(advance=lambda: None):
        if concurrency > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for _ in pool.map(lambda p: (process(p), advance()), pages):
                    pass
        else:
            for page in pages:
                process(page)
                advance()

    if progress_ctx:
        with progress_ctx as progress:
            task = progress.add_task(stage, total=len(pages))
            _run_all(lambda: progress.advance(task))
    else:
        _run_all()

    if summary.ok:
        store.record_timing(stage, len(summary.ok), time.monotonic() - start)
    log.info(summary.line())
    return summary


def parse_pages(spec: str | None, total: int) -> list[int]:
    """Parse a --pages spec like '1-10', '9,31,151', '5-' into 1-based page numbers."""
    if not spec:
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, _, hi = part.partition("-")
            start = int(lo) if lo else 1
            end = int(hi) if hi else total
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    out = sorted(p for p in pages if 1 <= p <= total)
    if not out:
        raise ValueError(f"--pages {spec!r} selects no pages (document has {total})")
    return out


def _compact(pages: list[int]) -> str:
    """Render [1,2,3,7] as '1-3,7'."""
    if not pages:
        return ""
    pages = sorted(pages)
    ranges: list[str] = []
    start = prev = pages[0]
    for p in pages[1:] + [None]:  # type: ignore[list-item]
        if p is not None and p == prev + 1:
            prev = p
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        if p is not None:
            start = prev = p
    return ",".join(ranges)
