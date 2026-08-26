from bgconvertor.analysis import city_analysis
from bgconvertor.model import BudgetDocument, BudgetLine, ConversionResult


def _result(sources):
    lines = [
        BudgetLine(name=f"L{i}", page=1, kind="revenue", code="51.02",
                   values={"total": 1}, source=s)
        for i, s in enumerate(sources)
    ]
    doc = BudgetDocument(title="T", budget="local", suffix="02", pages=[1], lines=lines)
    return ConversionResult(pdf="x.pdf", documents=[doc])


def test_llm_models_aggregated_and_legacy_flagged():
    a = city_analysis(_result([
        "digital", "ocr",
        "llm:gemini-3.6-flash", "llm:claude-sonnet-5", "llm:claude-sonnet-5",
        "llm",  # cache dinainte de proveniență
    ]))
    assert a["llm_models"] == [
        "claude-sonnet-5", "gemini-3.6-flash", "llm (model neînregistrat)",
    ]


def test_no_llm_lines_means_empty_list():
    assert city_analysis(_result(["digital", "ocr"]))["llm_models"] == []


def test_mixed_row_uses_cell_level_provenance():
    result = _result(["mixed"])
    result.documents[0].lines[0].value_sources = {
        "total": "llm:gemini-3.6-flash"
    }

    assert city_analysis(result)["llm_models"] == ["gemini-3.6-flash"]
