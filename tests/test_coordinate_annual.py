from bgconvertor.extract.coordinate_annual import map_tokens


def _token(x: float, y: float, text: str) -> dict:
    return {
        "box": [[x - 5, y - 3], [x + 5, y - 3], [x + 5, y + 3], [x - 5, y + 3]],
        "text": text,
        "confidence": 0.99,
    }


def test_coordinate_annual_ignores_marker_and_preserves_blank_arrears_column():
    tokens = [
        _token(120, 200, "TOTAL VENITURI"),
        _token(360, 201, "*"),
        _token(380, 201, "00.01"),
        _token(459, 202, "683994.00"),
        _token(583, 202, "295494.25"),
        _token(644, 202, "159603.80"),
        _token(706, 202, "127173.15"),
        _token(768, 202, "101722.80"),
        _token(829, 202, "654915.00"),
        _token(891, 202, "619086.00"),
        _token(953, 202, "622084.00"),
    ]

    payload = map_tokens(
        tokens,
        width=1000,
        height=1000,
        budget_year=2024,
        page_text="02 - Buget local detaliat MUNICIPIUL BUZAU",
    )

    assert len(payload["lines"]) == 1
    line = payload["lines"][0]
    assert line["raw_code"] == "00.01"
    assert line["values"] == {
        "total_2024": "683994.00",
        "trim1": "295494.25",
        "trim2": "159603.80",
        "trim3": "127173.15",
        "trim4": "101722.80",
        "est2025": "654915.00",
        "est2026": "619086.00",
        "est2027": "622084.00",
    }
    assert payload["mapping_stats"]["source_value_cells"] == 8
    assert payload["mapping_stats"]["mapped_value_cells"] == 8


def test_coordinate_annual_contextualizes_identical_printed_sections():
    tokens = []
    for y, code in ((200, "30.0.01"), (240, "30.01.01")):
        tokens.extend([
            _token(120, y, "Dobanzi interne directe"),
            _token(380, y, code),
            _token(459, y, "9600.00"),
            _token(583, y, "4500.00"),
        ])

    payload = map_tokens(
        tokens,
        width=1000,
        height=1000,
        budget_year=2024,
        page_text="02 - Buget local detaliat MUNICIPIUL BUZAU",
    )

    assert [line["section"] for line in payload["lines"]] == ["TOTAL", "FUNCTIONARE"]
    assert [line["raw_code"] for line in payload["lines"]] == ["30.01.01", "30.01.01"]
