from bgconvertor.config import RunConfig
from bgconvertor.llm.fallback import (
    FallbackBand,
    FallbackCell,
    FallbackRow,
    PageReading,
    extract_band_with_escalation,
    extract_page_llm,
    fallback_bands,
    fallback_benefit,
    fallback_columns,
    fallback_max_tokens,
    merge_page_payloads,
    needs_fallback,
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


def test_layout_fallback_columns_follow_document_year():
    payload = {
        "layout": "scan_revenue_detail",
        "budget_year": 2025,
        "lines": [],
    }
    assert fallback_columns(payload) == [
        "buget_2025",
        "est2026",
        "est2027",
        "est2028",
    ]


def test_annual_total_fallback_uses_document_year():
    assert fallback_columns({
        "layout": "scan_annual_total",
        "budget_year": 2027,
        "lines": [],
    }) == ["total_2027"]


def test_page_local_mapping_context_wins_and_global_columns_are_ignored():
    payload = {
        "budget_year": 2025,
        "mapping_context": {
            "columns": {"0": "name", "1": "code", "2": "trim1", "3": "trim2"}
        },
        "lines": [],
    }
    assert fallback_columns(payload, ["total", "est2039"]) == ["trim1", "trim2"]


def test_catastrophic_zero_line_table_is_fallback_eligible():
    assert needs_fallback({
        "layout": "scan_table_other",
        "n_tables": 1,
        "n_numeric_cells": 80,
        "lines": [],
    })
    assert needs_fallback({
        "layout": "scan_table_other",
        "n_tables": 1,
        "n_numeric_cells": 0,
        "lines": [],
    })


def test_dense_table_is_split_into_bounded_vertical_bands():
    rows = [[index / 100, (index + 0.8) / 100] for index in range(80)]
    bands = fallback_bands({"tables_rows_y": [rows]}, max_rows=32)
    assert [band.row_count for band in bands] == [32, 32, 16]
    assert bands[0].y0 == 0
    assert bands[-1].y1 < 1


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


def test_dense_band_uses_cheap_model_before_high_value_premium_escalation():
    empty = PageReading(rows=[])
    recovered = PageReading(rows=[FallbackRow(
        code="07.02.02",
        name="Impozit pe teren",
        section=None,
        cells=[FallbackCell(column="total", value="10")],
    )])

    class TieredClient(FakeClient):
        def __init__(self):
            super().__init__(None)
            self.readings = [empty, recovered]
            self.config.llm.fallback_model = "gemini-3.6-flash"
            self.config.llm.premium_model = "claude-sonnet-5"
            self.config.llm.premium_min_benefit_units = 6

        def structured(self, purpose, prompt, output_model, **kwargs):
            self.calls.append((purpose, prompt, output_model, kwargs))
            return self.readings.pop(0)

    client = TieredClient()
    payload = extract_band_with_escalation(
        client,
        image=None,
        columns=["total"],
        page=2,
        band=FallbackBand(index=0, y0=0, y1=1, row_count=20),
        max_tokens=4096,
        benefit_units=20,
        primary_model="gemini-3.6-flash",
    )

    assert [call[3]["model"] for call in client.calls] == [
        "gemini-3.6-flash", "claude-sonnet-5",
    ]
    assert payload["lines"][0]["source"] == "llm:claude-sonnet-5"


def test_llm_recovery_merges_missing_cells_without_overwriting_deterministic_values():
    deterministic = {
        "layout": "scan_table_other",
        "lines": [{
            "raw_code": "070202",
            "code": "07.02.02",
            "name": "Impozit pe teren",
            "section": "TOTAL",
            "values": {"total": "100.00"},
            "cell_issues": [{"column": "trim1", "raw": "1O,00"}],
        }],
    }
    llm = {
        "layout": "llm_fallback",
        "lines": [
            {
                "raw_code": "070202",
                "code": "07.02.02",
                "name": "Impozit pe teren",
                "section": "TOTAL",
                "values": {"total": "999.00", "trim1": "10.00"},
                "source": "llm:cheap",
            },
            {
                "raw_code": "070203",
                "code": "07.02.03",
                "name": "Taxe judiciare",
                "section": "TOTAL",
                "values": {"total": "5.00"},
                "source": "llm:cheap",
            },
        ],
    }

    merged = merge_page_payloads(deterministic, [llm])

    assert merged["lines"][0]["values"] == {"total": "100.00", "trim1": "10.00"}
    assert "cell_issues" not in merged["lines"][0]
    assert merged["lines"][0]["source"] == "mixed"
    assert merged["lines"][0]["value_sources"] == {
        "total": "ocr",
        "trim1": "llm:cheap",
    }
    assert merged["lines"][1]["code"] == "07.02.03"
    assert merged["llm_merge"] == {
        "filled_cells": 1,
        "llm_only_rows": 1,
        "conflicts_ignored": 1,
    }
