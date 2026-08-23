from decimal import Decimal

from bgconvertor.assemble import _derive_rows_from_formulas
from bgconvertor.model import BudgetDocument, BudgetLine


def _doc(lines):
    return BudgetDocument(title="T", budget="local", suffix="02", pages=[1], lines=lines)


def _ln(code, name, total, kind="expense_economic", **kw):
    return BudgetLine(code=code, raw_code=code.replace(".", ""), name=name,
                      kind=kind, page=1, values={"total": Decimal(total)}, **kw)


def test_single_missing_child_is_derived():
    d = _doc([
        _ln("20.03", "Hrana (cod 20.03.01+20.03.02)", "100"),
        _ln("20.03.01", "Hrana pentru oameni", "60"),
    ])
    _derive_rows_from_formulas(d)
    derived = [ln for ln in d.lines if ln.code == "20.03.02"]
    assert len(derived) == 1
    assert derived[0].values["total"] == Decimal("40")
    assert derived[0].source == "formula"
    assert derived[0].issues[0].severity == "info"


def test_two_missing_children_stay_underdetermined():
    d = _doc([_ln("20.03", "Hrana (cod 20.03.01+20.03.02)", "100")])
    before = len(d.lines)
    _derive_rows_from_formulas(d)
    assert len(d.lines) == before


def test_consistent_sum_derives_nothing():
    d = _doc([
        _ln("20.03", "Hrana (cod 20.03.01+20.03.02)", "60"),
        _ln("20.03.01", "Hrana pentru oameni", "60"),
    ])
    _derive_rows_from_formulas(d)
    assert not [ln for ln in d.lines if ln.code == "20.03.02"]


def test_parent_with_error_column_is_skipped():
    from bgconvertor.model import Issue
    parent = _ln("20.03", "Hrana (cod 20.03.01+20.03.02)", "100")
    parent.issues.append(Issue(check="V3_checksum", severity="error",
                               page=1, column="total", message="x"))
    d = _doc([parent, _ln("20.03.01", "Hrana pentru oameni", "60")])
    _derive_rows_from_formulas(d)
    assert not [ln for ln in d.lines if ln.code == "20.03.02"]
