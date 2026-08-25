from bgconvertor.config import RunConfig
from bgconvertor.llm.fallback import (
    FallbackCell,
    FallbackRow,
    PageReading,
    extract_page_llm,
    fallback_benefit,
    fallback_columns,
    fallback_max_tokens,
)


class FakeClient:
    def __init__(self, reading):
        self.reading = reading
        self.config = RunConfig()
        self.calls = []

    def structured(self, purpose, prompt, output_model, **kwargs):
        self.calls.append((purpose, prompt, output_model, kwargs))
        return self.reading


def test_wide_transposed_fallback_keeps_all_nine_columns():
    payload = {
        "layout": "scan_transposed_detail",
        "lines": [{"values": {"total": "10", "trim1": "3"}}],
    }
    assert fallback_columns(payload, ["total", "est2027"]) == [
        "total",
        "credite_stinse",
        "trim1",
        "trim2",
        "trim3",
        "trim4",
        "est2027",
        "est2028",
        "est2029",
    ]


def test_fallback_size_and_benefit_scale_with_missing_numeric_work():
    small = {"n_numeric_cells": 20, "lines": []}
    large = {"n_numeric_cells": 400, "lines": []}
    assert fallback_max_tokens(small, 4) < fallback_max_tokens(large, 4)
    assert fallback_max_tokens(large, 4) <= 24000
    assert fallback_benefit(large) > fallback_benefit(small)


def test_full_page_output_rejects_unrequested_columns():
    reading = PageReading(rows=[FallbackRow(
        code="070202",
        name="Impozit pe teren",
        section=None,
        cells=[
            FallbackCell(column="total", value="7.145,00"),
            FallbackCell(column="invented", value="999,00"),
        ],
    )])
    client = FakeClient(reading)

    payload = extract_page_llm(
        client,
        image=None,
        columns=["total"],
        page=2,
        max_tokens=4096,
    )

    assert payload["lines"][0]["values"] == {"total": "7145.00"}
    assert payload["lines"][0]["cell_issues"] == [
        {"column": "invented", "raw": "999,00"}
    ]
    assert client.calls[0][3]["max_tokens"] == 4096

