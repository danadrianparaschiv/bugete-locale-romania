from bgconvertor.extract.coordinate_sibiu import map_tokens


def _token(x: float, y: float, text: str) -> dict:
    return {
        "box": [[x - 5, y - 3], [x + 5, y - 3], [x + 5, y + 3], [x - 5, y + 3]],
        "text": text,
        "confidence": 0.99,
    }


def test_sibiu_coordinate_groups_wrapped_name_until_next_code():
    tokens = [
        _token(65, 250, "110209"),
        _token(170, 251, "Sume defalcate din taxa pe valoare adaugata"),
        _token(170, 276, "pentru finantarea invatamantului particular"),
        _token(170, 298, "si a celui confesional"),
        _token(305, 298, "12.370,00"),
        _token(445, 298, "3.093,00"),
        _token(515, 298, "3.095,00"),
        _token(585, 298, "3.092,00"),
        _token(655, 298, "3.090,00"),
        _token(725, 298, "13.193,00"),
        _token(795, 298, "13.577,00"),
        _token(865, 298, "13.929,00"),
        _token(65, 318, "1502"),
        _token(170, 318, "TAXE PE SERVICII SPECIFICE"),
        _token(305, 318, "199,00"),
    ]

    payload = map_tokens(
        tokens,
        width=1000,
        height=1000,
        budget_year=2024,
        page=2,
        page_text="BUGETUL LOCAL PE ANUL 2024",
    )

    assert payload["lines"][0]["raw_code"] == "110209"
    assert payload["lines"][0]["name"].endswith("si a celui confesional")
    assert payload["lines"][0]["values"] == {
        "total_2024": "12370.00",
        "trim1": "3093.00",
        "trim2": "3095.00",
        "trim3": "3092.00",
        "trim4": "3090.00",
        "est2025": "13193.00",
        "est2026": "13577.00",
        "est2027": "13929.00",
    }


def test_sibiu_coordinate_maps_program_column_to_canonical_total():
    tokens = [
        _token(65, 300, "000102"),
        _token(200, 300, "TOTAL VENITURI-BUGET LOCAL"),
        _token(389, 300, "565.510,24"),
        _token(575, 300, "159.608,42"),
        _token(665, 300, "151.137,98"),
        _token(752, 300, "141.064,11"),
        _token(844, 300, "113.699,73"),
    ]

    payload = map_tokens(
        tokens,
        width=1000,
        height=1000,
        budget_year=2024,
        page=28,
        page_text="BUGETUL LOCAL PE ANUL 2024",
    )

    assert payload["lines"][0]["values"] == {
        "total_2024": "565510.24",
        "trim1": "159608.42",
        "trim2": "151137.98",
        "trim3": "141064.11",
        "trim4": "113699.73",
    }


def test_sibiu_coordinate_keeps_only_audited_cover_total():
    payload = map_tokens(
        [
            _token(97, 300, "000102"),
            _token(180, 300, "TOTAL VENITURI"),
            _token(305, 300, "946.044,00"),
        ],
        width=1000,
        height=1000,
        budget_year=2024,
        page=1,
        page_text="BUGETUL LOCAL PE ANUL 2024",
    )

    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["raw_code"] == "000102"
    assert payload["lines"][0]["values"]["total_2024"] == "946044"
    assert payload["lines"][0]["institution"] == "TOTAL MUNICIPII"
    assert payload["mapping_context"]["budget_table"] is True
