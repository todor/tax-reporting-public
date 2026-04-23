from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.estateguru.estateguru_parser import parse_estateguru_pages, parse_estateguru_pdf
from integrations.p2p.shared.appendix6_models import (
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    UnsupportedSecondaryMarketModeError,
)
from tests.integrations.p2p.support import SAMPLE_PDF_PATHS


def _synthetic_pages() -> list[str]:
    return [
        "\n".join(
            [
                "Income Statement",
                "Selected period 01.01.2025 - 31.12.2025",
                "Interest Bonus (Borrower) Penalty Indemnity Bonus (EG) Secondary market profit/loss Sale fee AUM fee Total",
                "Total € 100.00 € 5.00 € 2.00 € 1.00 € 4.00 € -3.00 € 0.50 € -0.25 € 109.25",
            ]
        )
    ]


def _info(result_label: str, rows: list) -> Decimal | str:
    for row in rows:
        if row.label == result_label:
            return row.value
    raise AssertionError(f"missing informative row: {result_label}")


def test_parse_estateguru_pages_happy_path() -> None:
    result = parse_estateguru_pages(pages=_synthetic_pages())

    assert result.platform == "estateguru"
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("103.00")
    assert result.aggregate_code_606 == Decimal("9.00")
    assert result.taxable_code_603 == Decimal("103.00")
    assert result.taxable_code_606 == Decimal("9.00")

    assert _info("Interest (EUR)", result.informative_rows) == Decimal("100.00")
    assert _info("Bonus (Borrower) (EUR)", result.informative_rows) == Decimal("5.00")
    assert _info("Penalty (EUR)", result.informative_rows) == Decimal("2.00")
    assert _info("Indemnity (EUR)", result.informative_rows) == Decimal("1.00")
    assert _info("Bonus (EG) (EUR)", result.informative_rows) == Decimal("4.00")
    assert _info("Secondary market profit/loss (EUR)", result.informative_rows) == Decimal("-3.00")
    assert _info("Sale fee (EUR)", result.informative_rows) == Decimal("0.50")
    assert _info("AUM fee (EUR)", result.informative_rows) == Decimal("-0.25")
    assert _info("Total (EUR)", result.informative_rows) == Decimal("109.25")

    assert any(
        "secondary-market aggregate is <= 0" in message
        for message in result.informational_messages
    )


def test_parse_estateguru_pages_whitespace_and_dash_robustness() -> None:
    pages = [
        "\n".join(
            [
                "Income Statement",
                "Selected period 01.01.2025   –  31.12.2025",
                "Total € 10.00 € 0.00 € 1.00 € 0.50 € 0.00 € 0.25 € 0.00 € 0.00 € 11.25",
            ]
        )
    ]

    result = parse_estateguru_pages(pages=pages)
    assert result.aggregate_code_603 == Decimal("11.50")
    assert result.aggregate_code_606 == Decimal("0.25")


def test_parse_estateguru_pages_fails_on_missing_totals_row() -> None:
    pages = ["Income Statement\nSelected period 01.01.2025 - 31.12.2025\nNo totals"]
    with pytest.raises(P2PValidationError, match="totals row"):
        _ = parse_estateguru_pages(pages=pages)


def test_parse_estateguru_pages_fails_for_appendix_5_mode() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        _ = parse_estateguru_pages(
            pages=_synthetic_pages(),
            secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_5,
        )


def test_parse_estateguru_pdf_sample_values_when_available() -> None:
    sample_path = SAMPLE_PDF_PATHS["estateguru"]
    if not sample_path.exists():
        pytest.skip(f"sample PDF not available: {sample_path}")

    result = parse_estateguru_pdf(input_pdf=sample_path)
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("10.65")
    assert result.aggregate_code_606 == Decimal("0.00")

    assert _info("Interest (EUR)", result.informative_rows) == Decimal("10.65")
    assert _info("Bonus (Borrower) (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Penalty (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Indemnity (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Bonus (EG) (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Secondary market profit/loss (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Sale fee (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("AUM fee (EUR)", result.informative_rows) == Decimal("-0.19")
    assert _info("Total (EUR)", result.informative_rows) == Decimal("10.46")
