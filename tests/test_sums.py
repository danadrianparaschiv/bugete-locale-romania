from decimal import Decimal

import pytest

from bgconvertor.rules import Identity
from bgconvertor.sums import MissingCode, children_sum_delta, identity_delta, identity_holds

D = Decimal


def test_identity_delta_exact():
    ident = Identity(target="00.06", plus=["03.02", "04.02"], scope="revenue")
    values = {"00.06": D("142639.00"), "03.02": D("5080.00"), "04.02": D("137559.00")}
    assert identity_delta(values, ident) == D("0.00")
    assert identity_holds(values, ident)


def test_identity_with_minus_terms():
    # VENITURI PROPRII = 00.02 - 11.02 - 37.02 + 00.15
    ident = Identity(
        target="49.90", plus=["00.02", "00.15"], minus=["11.02", "37.02"], scope="revenue"
    )
    values = {
        "49.90": D("249559.00"),
        "00.02": D("307654.00"),
        "00.15": D("100.00"),
        "11.02": D("58000.00"),
        "37.02": D("195.00"),
    }
    assert identity_delta(values, ident) == D("0.00")


def test_identity_tolerance_scales_with_terms():
    ident = Identity(target="t", plus=["a", "b", "c"], scope="revenue")
    values = {"t": D("100.02"), "a": D("50"), "b": D("30"), "c": D("20")}
    assert identity_holds(values, ident)  # 0.02 <= 0.01 * 3
    values["t"] = D("100.04")
    assert not identity_holds(values, ident)


def test_missing_code_raises():
    ident = Identity(target="t", plus=["a"], scope="revenue")
    with pytest.raises(MissingCode):
        identity_delta({"t": D(1)}, ident)


def test_children_sum_delta():
    assert children_sum_delta(D("100"), [D("60"), D("40")]) == 0
    assert children_sum_delta(D("100"), [D("60")]) == D("40")
