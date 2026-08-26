"""Repair orchestration tests with a fake LLM client — no API, no network."""

from decimal import Decimal

from bgconvertor.config import RunConfig
from bgconvertor.llm.ledger import Ledger
from bgconvertor.llm.orchestrate import (
    CellValue,
    RowReading,
    RowSetReading,
    estimate_sum_repair_candidates,
    estimate_unparseable_candidates,
    repair_document,
)
from bgconvertor.model import BudgetDocument, BudgetLine, Issue


class FakeClient:
    """Returns queued RowSetReadings; records the prompts it saw."""

    def __init__(self, readings):
        self.readings = list(readings)
        self.prompts = []

    def structured(self, purpose, prompt, output_model, image=None, page=None,
                   model=None, max_tokens=4096):
        self.prompts.append(prompt)
        return self.readings.pop(0)


def _doc():
    parent = BudgetLine(
        code="74.02.05", raw_code="740205", name="Salubritate si gestiunea deseurilor (cod 74.02.05.01+74.02.05.02)",
        kind="expense_functional", page=9, section="TOTAL",
        values={"total_2026": Decimal("59411")},  # OCR misread; truth is 58295
        source="ocr",
        issues=[Issue(check="V4_hierarchy", severity="error", page=9,
                      code="74.02.05", column="total_2026",
                      message="parent != sum(children)")],
    )
    c1 = BudgetLine(code="74.02.05.01", raw_code="74020501", name="Salubritate",
                    kind="expense_functional", page=9, section="TOTAL",
                    values={"total_2026": Decimal("55553")}, source="ocr")
    c2 = BudgetLine(code="74.02.05.02", raw_code="74020502", name="Colectarea deseurilor",
                    kind="expense_functional", page=9, section="TOTAL",
                    values={"total_2026": Decimal("2742")}, source="ocr")
    return BudgetDocument(title="T", budget="local", suffix="02", pages=[9],
                          lines=[parent, c1, c2])


def test_repair_applies_consistent_reading():
    doc = _doc()
    client = FakeClient([RowSetReading(rows=[
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value="58.295")]),
        RowReading(code="74.02.05.01", cells=[CellValue(column="total_2026", value="55.553")]),
        RowReading(code="74.02.05.02", cells=[CellValue(column="total_2026", value="2.742")]),
    ])])
    log = repair_document(doc, client, page_image_fn=lambda p: None)
    parent = doc.lines[0]
    assert parent.values["total_2026"] == Decimal("58295")
    assert parent.source == "llm"
    assert not parent.issues  # V4 issue cleared
    assert log[0].severity == "info"
    # prompt lists the rows but never leaks an arithmetic rule (anti-rationalization)
    assert "74.02.05.01" in client.prompts[0]
    assert "Regula" not in client.prompts[0]


def test_repair_rejects_inconsistent_reading():
    doc = _doc()
    client = FakeClient([RowSetReading(rows=[
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value="59.411")]),   # still doesn't sum
        RowReading(code="74.02.05.01", cells=[CellValue(column="total_2026", value="55.553")]),
        RowReading(code="74.02.05.02", cells=[CellValue(column="total_2026", value="2.742")]),
    ])])
    log = repair_document(doc, client, page_image_fn=lambda p: None)
    parent = doc.lines[0]
    assert parent.values["total_2026"] == Decimal("59411")  # untouched
    assert parent.source == "ocr"
    assert parent.issues  # V4 issue stays — UNRESOLVED, never guessed
    assert log[0].severity == "warning"


def test_repair_handles_illegible_cells():
    doc = _doc()
    client = FakeClient([RowSetReading(rows=[
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value=None)]),  # stamped/illegible
        RowReading(code="74.02.05.01", cells=[CellValue(column="total_2026", value="55.553")]),
        RowReading(code="74.02.05.02", cells=[CellValue(column="total_2026", value="2.742")]),
    ])])
    repair_document(doc, client, page_image_fn=lambda p: None)
    # null reading -> parent keeps OCR value -> still inconsistent -> no change
    assert doc.lines[0].values["total_2026"] == Decimal("59411")
    assert doc.lines[0].issues


def test_repair_rejects_when_any_participating_row_was_not_independently_read():
    doc = _doc()
    client = FakeClient([RowSetReading(rows=[
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value="58.295")]),
        RowReading(code="74.02.05.01", cells=[CellValue(column="total_2026", value="55.553")]),
        # The existing OCR value for 74.02.05.02 would make the sum hold, but
        # acceptance must not reuse it when the model omitted that row.
    ])])

    log = repair_document(doc, client, page_image_fn=lambda _page: None)

    assert doc.lines[0].values["total_2026"] == Decimal("59411")
    assert doc.lines[0].issues
    assert log[0].severity == "warning"


def test_premium_escalation_runs_only_after_cheap_arithmetic_failure():
    inconsistent = RowSetReading(rows=[
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value="59.411")]),
        RowReading(code="74.02.05.01", cells=[CellValue(column="total_2026", value="55.553")]),
        RowReading(code="74.02.05.02", cells=[CellValue(column="total_2026", value="2.742")]),
    ])
    consistent = RowSetReading(rows=[
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value="58.295")]),
        RowReading(code="74.02.05.01", cells=[CellValue(column="total_2026", value="55.553")]),
        RowReading(code="74.02.05.02", cells=[CellValue(column="total_2026", value="2.742")]),
    ])

    class TieredClient(FakeClient):
        def __init__(self, threshold):
            super().__init__([inconsistent, consistent])
            self.config = RunConfig()
            self.config.llm.premium_model = "claude-opus-5"
            self.config.llm.premium_min_benefit_units = threshold
            self.models = []

        def structured(self, *args, **kwargs):
            self.models.append(kwargs.get("model"))
            return super().structured(*args, **kwargs)

    high_value = TieredClient(threshold=3)
    repaired = _doc()
    logs = repair_document(repaired, high_value, page_image_fn=lambda _page: None)

    assert high_value.models == [None, "claude-opus-5"]
    assert repaired.lines[0].values["total_2026"] == Decimal("58295")
    assert "premium escalation" in logs[0].message

    low_value = TieredClient(threshold=4)
    unresolved = _doc()
    repair_document(unresolved, low_value, page_image_fn=lambda _page: None)

    assert low_value.models == [None]
    assert unresolved.lines[0].issues


def test_repair_recovers_dropped_row_from_printed_formula():
    """OCR dropped 74.02.50; the printed formula names it, the model reads it."""
    from bgconvertor.llm.orchestrate import formula_children

    doc = _doc()
    # parent whose true composition includes a row OCR never produced
    parent = doc.lines[0]
    parent.name = "Protectia mediului (cod 74.02.05+74.02.50)"
    parent.values["total_2026"] = Decimal("59411")
    doc.lines[1].code = "74.02.05.01"  # children stay as-is; only 74.02.05 observed
    doc.lines = [parent, BudgetLine(
        code="74.02.05", raw_code="740205", name="Salubritate",
        kind="expense_functional", page=9, section="TOTAL",
        values={"total_2026": Decimal("58295")}, source="ocr")]
    parent.code = "74.02"
    parent.raw_code = "7402"
    parent.issues[0].code = "74.02"

    client = FakeClient([RowSetReading(rows=[
        RowReading(code="74.02", cells=[CellValue(column="total_2026", value="59.411")]),
        RowReading(code="74.02.05", cells=[CellValue(column="total_2026", value="58.295")]),
        RowReading(code="74.02.50", cells=[CellValue(column="total_2026", value="1.116")]),   # the dropped row, read from image
    ])])
    log = repair_document(doc, client, page_image_fn=lambda p: None)
    assert log[0].severity == "info"
    assert parent.values["total_2026"] == Decimal("59411")  # confirmed, not altered
    recovered = [ln for ln in doc.lines if ln.code == "74.02.50"]
    assert len(recovered) == 1
    assert recovered[0].values["total_2026"] == Decimal("1116")
    assert recovered[0].source == "llm"
    # the prompt asked about the dropped row
    assert "74.02.50" in client.prompts[0]
    # and never leaked an arithmetic rule to rationalize against
    assert "Regula" not in client.prompts[0]
    assert formula_children(parent.name) == ["74.02.05", "74.02.50"]


def test_budget_planner_runs_higher_yield_sum_group_first(tmp_path):
    low = _doc()
    high_parent = BudgetLine(
        code="65.02.03",
        raw_code="650203",
        name="Invatamant prescolar",
        kind="expense_functional",
        page=10,
        section="TOTAL",
        values={"total_2026": Decimal("30"), "est2027": Decimal("40")},
        source="ocr",
        issues=[
            Issue(
                check="V4_hierarchy",
                severity="error",
                page=10,
                code="65.02.03",
                column=column,
                message="parent != sum(children)",
            )
            for column in ("total_2026", "est2027")
        ],
    )
    high_children = [
        BudgetLine(
            code=f"65.02.03.0{index}",
            raw_code=f"6502030{index}",
            name=f"Child {index}",
            kind="expense_functional",
            page=10,
            section="TOTAL",
            values={"total_2026": value, "est2027": value},
            source="ocr",
        )
        for index, value in ((1, Decimal("10")), (2, Decimal("15")))
    ]
    low.lines.extend([high_parent, *high_children])

    reading = RowSetReading(rows=[
        RowReading(code="65.02.03", cells=[
            CellValue(column="total_2026", value="25"),
            CellValue(column="est2027", value="25"),
        ]),
        RowReading(code="65.02.03.01", cells=[
            CellValue(column="total_2026", value="10"),
            CellValue(column="est2027", value="10"),
        ]),
        RowReading(code="65.02.03.02", cells=[
            CellValue(column="total_2026", value="15"),
            CellValue(column="est2027", value="15"),
        ]),
    ])

    class PlannedFakeClient(FakeClient):
        def __init__(self):
            super().__init__([reading])
            self.config = RunConfig()
            self.ledger = Ledger(
                path=tmp_path / "ledger.jsonl",
                max_cost_usd=0.05,
                max_calls=10,
            )

    client = PlannedFakeClient()
    log = repair_document(low, client, page_image_fn=lambda _page: None)

    assert len(client.prompts) == 1
    assert "65.02.03" in client.prompts[0]
    assert "74.02.05" not in client.prompts[0]
    assert not high_parent.issues
    assert low.lines[0].issues  # lower-yield group was deliberately deferred
    assert any("budget planner selected 1" in issue.message for issue in log)


def test_file_wide_candidates_have_document_qualified_keys():
    config = RunConfig().llm

    first = estimate_sum_repair_candidates(
        _doc(), config, job_key_prefix="doc:0|"
    )
    second = estimate_sum_repair_candidates(
        _doc(), config, job_key_prefix="doc:1|"
    )

    assert first[0].key.startswith("doc:0|sum|")
    assert second[0].key.startswith("doc:1|sum|")
    assert first[0].key != second[0].key


def test_unparseable_candidates_are_document_qualified_and_discounted():
    line = BudgetLine(
        code="20.01",
        raw_code="2001",
        name="Furnituri",
        kind="expense_economic",
        page=12,
        values={},
        issues=[Issue(
            check="V7_hygiene",
            severity="error",
            page=12,
            code="20.01",
            column="trim2",
            message="unparseable OCR cell",
        )],
    )
    doc = BudgetDocument(
        title="T",
        budget="local",
        suffix="10",
        pages=[12],
        lines=[line],
    )

    candidates = estimate_unparseable_candidates(
        doc,
        RunConfig().llm,
        job_key_prefix="doc:2|",
    )

    assert candidates[0].key == "doc:2|cell:p12"
    assert candidates[0].benefit_units == 0.25
