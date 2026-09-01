from bgconvertor.extract.digital import _header_columns, _normalize, _parse_values


def _word(text, x0, top, *, width=6, height=4):
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + width,
        "top": top,
        "bottom": top + height,
    }


def test_copier_comparative_header_recognizes_compact_codind_and_year_roles():
    words = [
        _word("NR.", 1, 10),
        _word("CODIND.", 11, 10),
        _word("DENUMIRE", 21, 10),
        _word("BVC2023", 31, 10),
        _word("EXEC.2023", 41, 10),
        _word("BVC2024", 51, 10),
        *[_word(str(index), 1 + 10 * index, 20) for index in range(6)],
    ]

    columns, _, bottom = _header_columns(
        words, [0, 10, 20, 30, 40, 50], budget_year=2024
    )

    assert columns == {
        0: "rowno",
        1: "code",
        2: "name",
        3: "buget_2023",
        4: "executie_2023",
        5: "total_2024",
    }
    assert bottom == 25


def test_copier_comparative_values_restore_missing_decimal_separator():
    values, issues = _parse_values({
        "buget_2023": [_word("14.508", 0, 0), _word("80", 0, 0)],
        "executie_2023": [_word("1.737", 0, 0), _word("51", 0, 0)],
        "total_2024": [_word("17.424", 0, 0), _word("15", 0, 0)],
    })

    assert values == {
        "buget_2023": "14508.80",
        "executie_2023": "1737.51",
        "total_2024": "17424.15",
    }
    assert issues == []


def test_copier_comparative_codes_keep_functional_economic_split():
    assert _normalize("65.02/20", comparative=True) == ("20", "65.02")
    assert _normalize("65.02.03.01", comparative=True) == ("65.02.03.01", None)
