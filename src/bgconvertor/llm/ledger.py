"""LLM call ledger and hard budget.

Every API call — before any is ever made — goes through this module:
it appends a JSONL record per call and enforces a hard cost/call budget.
The deterministic pipeline never depends on it; only LLM passes do.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass
from pathlib import Path

# USD per 1M tokens (input, output). Update alongside model choices.
# Prices marked "verificați" are conservative placeholders — check the
# vendor's current list price before a large run; the budget stops earlier,
# never later, when a placeholder overshoots.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-fable-5": (25.00, 125.00),  # verificați
    "claude-opus-5": (15.00, 75.00),  # verificați
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI
    "gpt-5.1": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    # Google
    "gemini-3.1-pro-preview": (2.50, 15.00),  # verificați
    "gemini-3.6-flash": (0.50, 3.00),  # verificați
    # Mistral
    "mistral-medium-latest": (0.40, 2.00),
    "mistral-small-latest": (0.10, 0.30),
    # Qwen (DashScope)
    "qwen3-vl-plus": (0.20, 1.60),  # verificați
}
BATCH_DISCOUNT = 0.5


class BudgetExceeded(RuntimeError):
    """Raised when an LLM pass would exceed the configured budget."""


def estimate_cost(model: str, input_tokens: int, output_tokens: int, batch: bool = False) -> float:
    try:
        in_price, out_price = MODEL_PRICES[model]
    except KeyError:
        raise ValueError(f"no price entry for model {model!r} — add it to MODEL_PRICES") from None
    cost = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return cost * BATCH_DISCOUNT if batch else cost


@dataclass
class Ledger:
    path: Path
    max_cost_usd: float
    max_calls: int

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._calls = 0
        self._cost = 0.0
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                rec = json.loads(line)
                self._calls += 1
                self._cost += rec.get("cost_usd", 0.0)
        # budgets are per run: historic ledger spend is context, not quota
        self._run_cost_start = self._cost
        self._run_calls_start = self._calls
        self._lock = threading.Lock()

    @property
    def total_cost_usd(self) -> float:
        return self._cost

    @property
    def total_calls(self) -> int:
        return self._calls

    @property
    def run_cost_usd(self) -> float:
        return self._cost - self._run_cost_start

    def check_budget(self) -> None:
        """Call before each LLM request. Aborts LLM passes, never the pipeline."""
        if self.run_cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(
                f"LLM budget reached this run: ${self.run_cost_usd:.2f} >= "
                f"${self.max_cost_usd:.2f} (raise with --max-llm-cost)"
            )
        run_calls = self._calls - self._run_calls_start
        if run_calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call limit reached: {run_calls} >= {self.max_calls}")

    def record(
        self,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        page: int | None = None,
        duration_ms: int | None = None,
        batch: bool = False,
        cached: bool = False,
    ) -> float:
        cost = 0.0 if cached else estimate_cost(model, input_tokens, output_tokens, batch)
        rec = {
            "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "purpose": purpose,
            "model": model,
            "page": page,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "duration_ms": duration_ms,
            "batch": batch,
            "cached": cached,
        }
        with self._lock:
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            self._calls += 1
            self._cost += cost
        return cost

    def summary(self) -> str:
        return (
            f"LLM this run: {self._calls - self._run_calls_start} calls, "
            f"${self.run_cost_usd:.4f} (lifetime: {self._calls} calls, ${self._cost:.4f})"
        )
