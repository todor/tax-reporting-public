from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.lendermarket.lendermarket_parser import parse_lendermarket_pages, parse_lendermarket_pdf
from integrations.p2p.shared.appendix6_models import (
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    UnsupportedSecondaryMarketModeError,
)
from tests.integrations.p2p.support import SAMPLE_PDF_PATHS


def _synthetic_pages(*, pending: str = "0.00", campaign: str = "3.50") -> list[str]:
    return [
        "\n".join(
            [
                "Tax statement for operations on Lendermarket from 01.01.2025 - 31.12.2025",
                "Payments Received 1200.00 EUR",
                "- Principal Amount 1000.00 EUR",
                "- Interest 190.00 EUR",
                "- Late Payment Fees 10.00 EUR",
                f"- Pending Payment interest {pending} EUR",
                f"- Campaign rewards and bonuses {campaign} EUR",
            ]
        )
    ]


def _info(label: str, rows: list) -> Decimal | str:
    for row in rows:
        if row.label == label:
            return row.value
    raise AssertionError(f"missing informative row: {label}")


def test_parse_lendermarket_pages_happy_path() -> None:
    result = parse_lendermarket_pages(pages=_synthetic_pages())

    assert result.platform == "lendermarket"
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("200.00")
    assert result.aggregate_code_606 == Decimal("3.50")

    assert _info("Principal Amount (EUR)", result.informative_rows) == Decimal("1000.00")
    assert _info("Pending Payment interest (EUR)", result.informative_rows) == Decimal("0.00")


def test_parse_lendermarket_pages_pending_interest_is_excluded() -> None:
    result = parse_lendermarket_pages(pages=_synthetic_pages(pending="12.34", campaign="0.00"))

    assert result.aggregate_code_603 == Decimal("200.00")
    assert result.aggregate_code_606 == Decimal("0.00")
    assert any(
        "Pending Payment interest is excluded" in message
        for message in result.informational_messages
    )


def test_parse_lendermarket_pages_fails_on_missing_required_field() -> None:
    pages = [
        "Tax statement for operations on Lendermarket from 01.01.2025 - 31.12.2025\n"
        "- Principal Amount 1000.00 EUR\n"
        "- Late Payment Fees 10.00 EUR"
    ]

    with pytest.raises(P2PValidationError, match="missing required field"):
        _ = parse_lendermarket_pages(pages=pages)


def test_parse_lendermarket_pages_fails_for_appendix_5_mode() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        _ = parse_lendermarket_pages(
            pages=_synthetic_pages(),
            secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_5,
        )


def test_parse_lendermarket_pages_whitespace_robustness() -> None:
    pages = [
        "\n".join(
            [
                "Tax statement for operations on Lendermarket from 01.01.2025   -   31.12.2025",
                "Payments Received 1200.00 EUR",
                "-  Principal Amount 1000.00 EUR",
                "- Interest   190.00 EUR",
                "- Late Payment Fees 10.00 EUR",
                "- Pending Payment interest 0.00 EUR",
                "- Campaign rewards and bonuses 0.00 EUR",
            ]
        )
    ]
    result = parse_lendermarket_pages(pages=pages)
    assert result.aggregate_code_603 == Decimal("200.00")


def test_parse_lendermarket_pdf_sample_values_when_available() -> None:
    sample_path = SAMPLE_PDF_PATHS["lendermarket"]
    if not sample_path.exists():
        pytest.skip(f"sample PDF not available: {sample_path}")

    result = parse_lendermarket_pdf(input_pdf=sample_path)
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("802.19")
    assert result.aggregate_code_606 == Decimal("0.00")

    assert _info("Principal Amount (EUR)", result.informative_rows) == Decimal("9369.77")
    assert _info("Interest (EUR)", result.informative_rows) == Decimal("801.79")
    assert _info("Late Payment Fees (EUR)", result.informative_rows) == Decimal("0.40")
    assert _info("Pending Payment interest (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Campaign rewards and bonuses (EUR)", result.informative_rows) == Decimal("0.00")
