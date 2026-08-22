"""Sum engine: evaluate aggregation identities over extracted values.

Pure functions over {code: Decimal} mappings — the validator (Phase 1)
feeds these per column, the eval harness uses them on golden pages.
"""

from __future__ import annotations

from decimal import Decimal

from .rules import Identity

# Budgets are in mii lei with 2 decimals; each term can carry a half-cent
# rounding, so the tolerance scales mildly with the number of terms.
BASE_TOLERANCE = Decimal("0.01")


class MissingCode(KeyError):
    pass


def identity_delta(values: dict[str, Decimal], identity: Identity) -> Decimal:
    """target - (sum(plus) - sum(minus)); raises MissingCode when a term is absent."""
    def get(code: str) -> Decimal:
        try:
            return values[code]
        except KeyError:
            raise MissingCode(code) from None

    expected = sum((get(c) for c in identity.plus), Decimal(0)) - sum(
        (get(c) for c in identity.minus), Decimal(0)
    )
    return get(identity.target) - expected


def identity_holds(
    values: dict[str, Decimal],
    identity: Identity,
    tolerance: Decimal = BASE_TOLERANCE,
) -> bool:
    n_terms = max(1, len(identity.plus) + len(identity.minus))
    return abs(identity_delta(values, identity)) <= tolerance * n_terms


def children_sum_delta(
    parent_value: Decimal, child_values: list[Decimal]
) -> Decimal:
    """parent - sum(children). Caller filters memo/'din care' lines first."""
    return parent_value - sum(child_values, Decimal(0))
