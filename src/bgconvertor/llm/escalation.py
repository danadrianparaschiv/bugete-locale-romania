"""Cheap-first premium escalation policy.

The verifier, not the model, decides whether the first reading failed.  A
premium model is eligible only for sufficiently valuable work and still goes
through the same hard ledger reservation as every other call.
"""

from __future__ import annotations


def premium_after_failure(client, primary_model: str, benefit_units: float) -> str | None:
    llm = getattr(getattr(client, "config", None), "llm", None)
    if llm is None:
        return None
    premium = getattr(llm, "premium_model", None)
    threshold = float(getattr(llm, "premium_min_benefit_units", 6.0) or 6.0)
    if not premium or premium == primary_model or benefit_units < threshold:
        return None
    return premium
