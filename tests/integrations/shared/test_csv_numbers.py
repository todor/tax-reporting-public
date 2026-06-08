from decimal import Decimal
from pathlib import Path

import pytest

from integrations.shared.csv_numbers import (
    CsvDecimalDetector,
    CsvDecimalParseError,
    CsvDecimalSeparatorError,
    classify_csv_decimal_evidence,
    parse_csv_decimal,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234.56", Decimal("1234.56")),
        ("-1234.56", Decimal("-1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("-1,234.56", Decimal("-1234.56")),
        ("1 234.56", Decimal("1234.56")),
        ("1,234", Decimal("1234")),
    ],
)
def test_parse_csv_decimal_with_dot_separator(raw: str, expected: Decimal) -> None:
    assert parse_csv_decimal(raw, decimal_separator="dot") == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234,56", Decimal("1234.56")),
        ("-1234,56", Decimal("-1234.56")),
        ("1 234,56", Decimal("1234.56")),
    ],
)
def test_parse_csv_decimal_with_comma_separator(raw: str, expected: Decimal) -> None:
    assert parse_csv_decimal(raw, decimal_separator="comma") == expected


def test_comma_separator_does_not_accept_dot_grouping() -> None:
    with pytest.raises(CsvDecimalParseError):
        parse_csv_decimal("1.234,56", decimal_separator="comma")


def test_ambiguous_lone_comma_does_not_decide_auto_detection() -> None:
    detector = CsvDecimalDetector(analyzer_alias="ibkr", input_path=Path("sample.csv"))
    detector.observe("1,011", row_number=2, column_name="Quantity")

    info = detector.resolve("auto")

    assert info.separator == "dot"
    assert info.source == "default"
    assert info.ambiguous_values[0].value == "1,011"
    assert classify_csv_decimal_evidence("1,011") == "ambiguous"


def test_ambiguous_lone_comma_parses_by_resolved_separator() -> None:
    assert parse_csv_decimal("1,011", decimal_separator="dot") == Decimal("1011")
    assert parse_csv_decimal("1,011", decimal_separator="comma") == Decimal("1.011")


def test_mixed_clear_decimal_evidence_fails() -> None:
    detector = CsvDecimalDetector(analyzer_alias="coinbase", input_path=Path("mixed.csv"))
    detector.observe("1234.56", row_number=2, column_name="Subtotal")
    detector.observe("1234,56", row_number=3, column_name="Subtotal")

    with pytest.raises(CsvDecimalSeparatorError):
        detector.resolve("auto")
