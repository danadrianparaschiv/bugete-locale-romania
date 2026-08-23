from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bgconvertor.parsing import (
    NumberParseError,
    format_ro_number,
    normalize_indicator_code,
    parse_ro_number,
)


class TestParseRoNumber:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("0,00", Decimal("0.00")),
            ("646.801,06", Decimal("646801.06")),
            ("1.234.567,89", Decimal("1234567.89")),
            ("21303", Decimal("21303")),
            ("1.234", Decimal("1234")),  # thousands sep, no decimals
            ("48152,87", Decimal("48152.87")),
            ("-170,00", Decimal("-170.00")),
            ("−170,00", Decimal("-170.00")),  # unicode minus from OCR
            ("(170,00)", Decimal("-170.00")),
            (" 3.943,37 ", Decimal("3943.37")),
            ("905,00", Decimal("905.00")),
        ],
    )
    def test_numbers(self, raw, expected):
        assert parse_ro_number(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "-", "–", " "])
    def test_empty_cells(self, raw):
        assert parse_ro_number(raw) is None

    def test_x_marker(self):
        assert parse_ro_number("X") == "X"
        assert parse_ro_number(" x ") == "X"

    @pytest.mark.parametrize(
        "raw",
        [
            "1.23",  # bad thousands group — likely OCR misread
            "12.3456",
            "1,2,3",
            "abc",
            "12a",
            "1.234,",  # dangling comma
            "..",
            "-",  # handled as None above, but "--" is garbage:
        ][:-1] + ["--"],
    )
    def test_garbage_raises(self, raw):
        with pytest.raises(NumberParseError):
            parse_ro_number(raw)

    @given(
        st.decimals(
            min_value=Decimal("-999999999.99"),
            max_value=Decimal("999999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    def test_roundtrip(self, value):
        assert parse_ro_number(format_ro_number(value)) == value


class TestFormatRoNumber:
    def test_basic(self):
        assert format_ro_number(Decimal("646801.06")) == "646.801,06"
        assert format_ro_number(Decimal("0")) == "0,00"
        assert format_ro_number(Decimal("-170")) == "-170,00"
        assert format_ro_number(Decimal("1234567.891")) == "1.234.567,89"
        assert format_ro_number(Decimal("21303"), decimals=0) == "21.303"


class TestNormalizeIndicatorCode:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("65020301", "65.02.03.01"),
            ("6502", "65.02"),
            ("650203", "65.02.03"),
            ("42.02.93.01", "42.02.93.01"),
            ("43.02", "43.02"),
            ("10", "10"),
            ("1001", "10.01"),
            ("100101", "10.01.01"),
            ("66.02.06.04*", "66.02.06.04"),
            ("30.02.08 *)", "30.02.08"),
            ("5002", "50.02"),
            # pseudo/form codes and garbage -> None
            ("D", None),
            ("01F", None),
            ("F", None),
            ("*", None),
            ("", None),
            (None, None),
            ("651", None),  # odd digit count
            ("65.02.1", None),
        ],
    )
    def test_codes(self, raw, expected):
        assert normalize_indicator_code(raw) == expected


class TestUSFormatOCR:
    """Bacau prints US-style numbers: comma thousands, dot decimals."""

    def test_us_numbers(self):
        assert parse_ro_number("19,809.00", ocr=True) == Decimal("19809.00")
        assert parse_ro_number("1,234,567.89", ocr=True) == Decimal("1234567.89")
        assert parse_ro_number("0.00", ocr=True) == Decimal("0.00")
        # Romanian stays Romanian
        assert parse_ro_number("19.809,00", ocr=True) == Decimal("19809.00")
        assert parse_ro_number("1.234", ocr=True) == Decimal("1234")

    def test_ambiguous_garbage_still_raises(self):
        import pytest as _pytest
        with _pytest.raises(NumberParseError):
            parse_ro_number("15,000.00 / 0.00", ocr=True)  # dual cell stays flagged


def test_source_letter_suffix_stripped():
    from bgconvertor.parsing import normalize_indicator_code
    assert normalize_indicator_code("51.02A") == "51.02"
    assert normalize_indicator_code("50.02A") == "50.02"
    # a bare pseudo-code letter still rejects
    assert normalize_indicator_code("01F") is None
