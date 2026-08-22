"""Repair orchestration tests with a fake LLM client — no API, no network."""

from decimal import Decimal

from bgconvertor.llm.orchestrate import CellValue, RowReading, RowSetReading, repair_document
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
