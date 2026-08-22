"""Structured logging with rich output.

Every log line carries (pdf, stage, page) context so a failure can be
located without re-running anything. Verbosity: 0 = warnings, 1 (-v) =
stage progress, 2 (-vv) = per-decision detail.
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler


def setup_logging(verbosity: int = 0) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        force=True,
    )
    # Third-party noise stays at WARNING even under -vv.
    for noisy in ("httpx", "httpcore", "PIL", "openpyxl", "pypdf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['where']}] {msg}", kwargs


def page_logger(pdf_stem: str, stage: str, page: int | None = None) -> logging.LoggerAdapter:
    where = f"{pdf_stem}:{stage}" + (f":p{page}" if page is not None else "")
    return _ContextAdapter(logging.getLogger(f"bgc.{stage}"), {"where": where})
