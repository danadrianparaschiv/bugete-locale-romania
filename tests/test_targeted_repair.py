"""Offline regression tests for every non-hierarchy P2 recovery gate."""

from decimal import Decimal

from bgconvertor.config import RunConfig
from bgconvertor.llm.targeted import (
    EvidenceCell,
    EvidenceReading,
    EvidenceRow,
    repair_targeted,
)
from bgconvertor.model import BudgetDocument, BudgetLine, Issue
from bgconvertor.nomenclator import load_registry


class FakeClient:
    def __init__(self, readings):
        self.readings = list(readings)
        self.config = RunConfig()
        self.models = []

    def structured(self, _purpose, _prompt, _output_model, **kwargs):
        self.models.append(kwargs.get("model"))
        return self.readings.pop(0)


def _document(lines):
    return BudgetDocument(
        title="Test",
        budget="local",
        suffix="02",
        pages=sorted({line.page for line in lines}),
        lines=lines,
    )


def _cells(**values):
    return [EvidenceCell(column=column, value=value) for column, value in values.items()]


def test_quarterly_checksum_repair_requires_and_applies_all_five_cells():
    issue = Issue(
        check="V3_row_checksum", severity="error", page=4, code="03.02",
        message="total != sum(trimestre)",
    )
    line = BudgetLine(
        code="03.02", raw_code="0302", name="Impozit pe venit",
        kind="revenue", page=4, section="TOTAL",
        values={
            "total": Decimal("99"), "trim1": Decimal("25"),
            "trim2": Decimal("25"), "trim3": Decimal("25"), "trim4": Decimal("25"),
        },
        issues=[issue], source="ocr",
    )
    reading = EvidenceReading(rows=[EvidenceRow(
        row_id="r1", code="03.02",
        cells=_cells(total="100", trim1="25", trim2="25", trim3="25", trim4="25"),
    )])

    logs = repair_targeted(
        _document([line]), None, FakeClient([reading]), lambda _page: None
    )

    assert line.values["total"] == Decimal("100")
    assert not line.issues
    assert line.value_sources["trim4"] == "llm:claude-sonnet-5"
    assert logs[0].severity == "info"


def test_global_registry_identity_repair_uses_complete_independent_rows(reference_dir):
    registry = load_registry(reference_dir)
    issue = Issue(
        check="V5_identity", severity="error", page=5, code="00.02", column="total",
        message="[TOTAL] 00.02 total: 60 != 70 per identity",
    )
    current = BudgetLine(
        code="00.02", raw_code="000202", name="VENITURI CURENTE",
        kind="revenue", page=5, section="TOTAL", values={"total": Decimal("60")},
        issues=[issue], source="ocr",
    )
    fiscal = BudgetLine(
        code="00.03", raw_code="000302", name="VENITURI FISCALE",
        kind="revenue", page=5, section="TOTAL", values={"total": Decimal("40")},
        source="ocr",
    )
    non_fiscal = BudgetLine(
        code="00.12", raw_code="001202", name="VENITURI NEFISCALE",
        kind="revenue", page=5, section="TOTAL", values={"total": Decimal("30")},
        source="ocr",
    )
    reading = EvidenceReading(rows=[
        EvidenceRow(row_id="r1", code="00.02", cells=_cells(total="70")),
        EvidenceRow(row_id="r2", code="00.03", cells=_cells(total="40")),
        EvidenceRow(row_id="r3", code="00.12", cells=_cells(total="30")),
    ])

    repair_targeted(
        _document([current, fiscal, non_fiscal]),
        registry,
        FakeClient([reading]),
        lambda _page: None,
    )

    assert current.values["total"] == Decimal("70")
    assert not current.issues


def test_cross_section_identity_uses_row_ids_for_repeated_code(reference_dir):
    registry = load_registry(reference_dir)
    issue = Issue(
        check="V5_identity", severity="error", page=10, code="65.02", column="total",
        message="65.02 total: TOTAL 100 != FUNCTIONARE 70 + DEZVOLTARE 20",
    )
    total = BudgetLine(
        code="65.02", raw_code="6502", name="Invatamant", kind="expense_functional",
        page=10, section="TOTAL", values={"total": Decimal("100")},
        issues=[issue], source="ocr",
    )
    functioning = total.model_copy(deep=True, update={
        "page": 11, "section": "FUNCTIONARE", "values": {"total": Decimal("70")},
        "issues": [],
    })
    development = total.model_copy(deep=True, update={
        "page": 12, "section": "DEZVOLTARE", "values": {"total": Decimal("20")},
        "issues": [],
    })
    reading = EvidenceReading(rows=[
        EvidenceRow(row_id="r1", code="65.02", cells=_cells(total="90")),
        EvidenceRow(row_id="r2", code="65.02", cells=_cells(total="70")),
        EvidenceRow(row_id="r3", code="65.02", cells=_cells(total="20")),
    ])

    repair_targeted(
        _document([total, functioning, development]),
        registry,
        FakeClient([reading]),
        lambda _page: None,
    )

    assert total.values["total"] == Decimal("90")
    assert not total.issues


def test_misread_code_must_pass_registry_name_and_collision_gates(reference_dir):
    registry = load_registry(reference_dir)
    issue = Issue(
        check="V1_code", severity="error", page=6, code="03.92.99",
        message="not in nomenclator",
    )
    line = BudgetLine(
        code="03.92.99", raw_code="039299",
        name="Impozitul pe veniturile din transferul proprietatilor imobiliare",
        kind="revenue", page=6, section="TOTAL", values={"total": Decimal("7")},
        issues=[issue], source="ocr",
    )
    reading = EvidenceReading(rows=[EvidenceRow(
        row_id="r1", code="03.02.18", cells=[]
    )])

    repair_targeted(
        _document([line]), registry, FakeClient([reading]), lambda _page: None
    )

    assert line.code == "03.02.18"
    assert line.code_source == "llm:claude-haiku-4-5"
    assert not line.issues


def test_conflicting_duplicate_is_removed_only_when_both_readings_agree():
    first = BudgetLine(
        code="20.01", raw_code="2001", name="Furnituri", kind="expense_economic",
        func_code="65.02", page=7, section="TOTAL",
        values={"total": Decimal("10")}, source="ocr",
    )
    duplicate_issue = Issue(
        check="V7_hygiene", severity="warning", page=8, code="20.01",
        message="duplicate of p7 with different values",
    )
    second = first.model_copy(deep=True, update={
        "page": 8,
        "values": {"total": Decimal("11")},
        "issues": [duplicate_issue],
    })
    doc = _document([first, second])
    reading = EvidenceReading(rows=[
        EvidenceRow(row_id="first", code="20.01", cells=_cells(total="10")),
        EvidenceRow(row_id="second", code="20.01", cells=_cells(total="10")),
    ])

    repair_targeted(doc, None, FakeClient([reading]), lambda _page: None)

    assert doc.lines == [first]
    assert first.values["total"] == Decimal("10")


def test_incomplete_identity_reading_is_never_accepted(reference_dir):
    registry = load_registry(reference_dir)
    issue = Issue(
        check="V5_identity", severity="error", page=9, code="00.02", column="total",
        message="[TOTAL] identity failed",
    )
    lines = [
        BudgetLine(
            code=code, raw_code=code.replace(".", ""), name=name, kind="revenue",
            page=9, section="TOTAL", values={"total": value},
            issues=[issue] if code == "00.02" else [], source="ocr",
        )
        for code, name, value in (
            ("00.02", "VENITURI CURENTE", Decimal("60")),
            ("00.03", "VENITURI FISCALE", Decimal("40")),
            ("00.12", "VENITURI NEFISCALE", Decimal("30")),
        )
    ]
    # r3 is omitted; existing OCR has exactly the value needed to make 70.
    reading = EvidenceReading(rows=[
        EvidenceRow(row_id="r1", code="00.02", cells=_cells(total="70")),
        EvidenceRow(row_id="r2", code="00.03", cells=_cells(total="40")),
    ])

    logs = repair_targeted(
        _document(lines), registry, FakeClient([reading]), lambda _page: None
    )

    assert lines[0].values["total"] == Decimal("60")
    assert lines[0].issues == [issue]
    assert logs[0].severity == "warning"
