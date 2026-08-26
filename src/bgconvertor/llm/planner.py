"""Budget-aware ordering and admission for paid recovery work.

The hard ledger cap remains the final authority.  This module decides which
calls should reach that cap first: cached work, then the largest expected
quality gain per reserved dollar.  The units are deliberately conservative
and local (numeric cells/validator breaches), not a claimed corpus recall.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryCandidate:
    key: str
    kind: str
    page: int
    benefit_units: float
    estimated_cost_usd: float
    estimated_calls: int = 1
    cached: bool = False
    detail: str = ""

    @property
    def admitted_cost_usd(self) -> float:
        return 0.0 if self.cached else self.estimated_cost_usd

    @property
    def benefit_per_dollar(self) -> float:
        cost = self.admitted_cost_usd
        return float("inf") if cost == 0 else self.benefit_units / cost


@dataclass(frozen=True)
class RecoveryPlan:
    selected: tuple[RecoveryCandidate, ...]
    skipped: tuple[RecoveryCandidate, ...]
    budget_usd: float

    @property
    def estimated_cost_usd(self) -> float:
        return sum(candidate.admitted_cost_usd for candidate in self.selected)

    @property
    def expected_benefit_units(self) -> float:
        return sum(candidate.benefit_units for candidate in self.selected)

    def as_dict(self) -> dict:
        def item(candidate: RecoveryCandidate) -> dict:
            return {
                "key": candidate.key,
                "kind": candidate.kind,
                "page": candidate.page,
                "benefit_units": round(candidate.benefit_units, 3),
                "estimated_cost_usd": round(candidate.admitted_cost_usd, 6),
                "estimated_calls": 0 if candidate.cached else candidate.estimated_calls,
                "benefit_per_dollar": (
                    None
                    if candidate.benefit_per_dollar == float("inf")
                    else round(candidate.benefit_per_dollar, 3)
                ),
                "cached": candidate.cached,
                "detail": candidate.detail,
            }

        return {
            "policy": "cached_then_expected_quality_gain_per_reserved_dollar",
            "budget_usd": round(self.budget_usd, 6),
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "expected_benefit_units": round(self.expected_benefit_units, 3),
            "selected": [item(candidate) for candidate in self.selected],
            "skipped": [item(candidate) for candidate in self.skipped],
        }


def _rank(candidate: RecoveryCandidate) -> tuple:
    return (
        not candidate.cached,
        -candidate.benefit_per_dollar,
        -candidate.benefit_units,
        candidate.admitted_cost_usd,
        candidate.page,
        candidate.key,
    )


def select_candidates(
    candidates: list[RecoveryCandidate],
    budget_usd: float,
    max_calls: int | None = None,
) -> RecoveryPlan:
    """Greedily admit deterministic, stable work under a soft stage budget.

    Reservations still run immediately before every request, so an estimate
    can stop too early but can never authorize spending beyond the ledger cap.
    Cached candidates have zero admitted cost and are always ranked first.
    """
    selected: list[RecoveryCandidate] = []
    skipped: list[RecoveryCandidate] = []
    spent = 0.0
    paid_calls = 0
    for candidate in sorted(candidates, key=_rank):
        paid = not candidate.cached
        calls = candidate.estimated_calls if paid else 0
        call_fits = max_calls is None or paid_calls + calls <= max_calls
        cost_fits = spent + candidate.admitted_cost_usd <= budget_usd + 1e-12
        if call_fits and cost_fits:
            selected.append(candidate)
            spent += candidate.admitted_cost_usd
            paid_calls += calls
        else:
            skipped.append(candidate)
    return RecoveryPlan(tuple(selected), tuple(skipped), max(0.0, budget_usd))
