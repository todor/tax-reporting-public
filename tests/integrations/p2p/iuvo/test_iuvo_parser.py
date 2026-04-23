from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.iuvo.iuvo_parser import parse_iuvo_pages, parse_iuvo_pdf
from integrations.p2p.shared.appendix6_models import (
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    UnsupportedSecondaryMarketModeError,
)
from tests.integrations.p2p.support import SAMPLE_PDF_PATHS


def _synthetic_pages(
    *,
    gains: str = "10.00",
    losses: str = "-3.00",
    fees: str = "-1.00",
    campaign: str = "2.00",
) -> list[str]:
    return [
        "\n".join(
            [
                "Your income for the period 2025-01-01 - 2025-12-31, generated on iuvo marketplace is:",
                "Interest income",
                "Originators from Bulgaria",
                "50.00 EUR",
                "20.00 EUR",
                "70.00 EUR",
                "Late fees",
                "Originators from Bulgaria",
                "5.00 EUR",
                "5.00 EUR",
                "Secondary market gains",
                "Originators from Bulgaria",
                "7.00 EUR",
                "3.00 EUR",
                f"{gains} EUR",
                "Campaign rewards",
                f"{campaign} EUR",
                "Interest income iuvoSAVE",
                "8/12m EUR iuvoSAVE",
                "30.00 EUR",
                "30.00 EUR",
            ]
        ),
        "\n".join(
            [
                "Your expenses for the period 2025-01-01 - 2025-12-31 in relation to your investment activity on iuvo are:",
                f"Secondary market fees {fees} EUR",
                "Secondary market losses",
                "Originators from Bulgaria",
                f"{losses} EUR",
                f"{losses} EUR",
                "Early withdraw fees iuvoSAVE 0.00 EUR",
            ]
        ),
    ]


def _info(label: str, rows: list) -> Decimal | str:
    for row in rows:
        if row.label == label:
            return row.value
    raise AssertionError(f"missing informative row: {label}")


def test_parse_iuvo_pages_happy_path() -> None:
    result = parse_iuvo_pages(pages=_synthetic_pages())

    assert result.platform == "iuvo"
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("105.00")
    assert result.aggregate_code_606 == Decimal("8.00")

    assert _info("Interest income (EUR)", result.informative_rows) == Decimal("70.00")
    assert _info("Late fees (EUR)", result.informative_rows) == Decimal("5.00")
    assert _info("Interest income iuvoSAVE (EUR)", result.informative_rows) == Decimal("30.00")
    assert _info("Secondary market aggregate used for code 606 (EUR)", result.informative_rows) == Decimal("6.00")


def test_parse_iuvo_pages_secondary_non_positive_is_omitted() -> None:
    result = parse_iuvo_pages(
        pages=_synthetic_pages(gains="1.00", losses="-2.00", fees="0.00", campaign="0.00")
    )

    assert result.aggregate_code_603 == Decimal("105.00")
    assert result.aggregate_code_606 == Decimal("0.00")
    assert any(
        "secondary-market aggregate is <= 0" in message
        for message in result.informational_messages
    )


def test_parse_iuvo_pages_fails_on_missing_required_label() -> None:
    pages = _synthetic_pages()
    page0 = pages[0].replace("Campaign rewards\n2.00 EUR\n", "")
    with pytest.raises(P2PValidationError, match="Campaign rewards"):
        _ = parse_iuvo_pages(pages=[page0, pages[1]])


def test_parse_iuvo_pages_fails_for_appendix_5_mode() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        _ = parse_iuvo_pages(
            pages=_synthetic_pages(),
            secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_5,
        )


def test_parse_iuvo_pages_inline_amount_spacing_robustness() -> None:
    pages = [
        "\n".join(
            [
                "Your income for the period 2025-01-01 - 2025-12-31, generated on iuvo marketplace is:",
                "Interest income 70.00 EUR",
                "Late fees 5.00 EUR",
                "Secondary market gains 3.00 EUR",
                "Campaign rewards 1.00 EUR",
                "Interest income iuvoSAVE 2.00 EUR",
            ]
        ),
        "\n".join(
            [
                "Your expenses for the period 2025-01-01 - 2025-12-31 in relation to your investment activity on iuvo are:",
                "Secondary market fees -1.00 EUR",
                "Secondary market losses -1.00 EUR",
                "Early withdraw fees iuvoSAVE 0.00 EUR",
            ]
        ),
    ]

    result = parse_iuvo_pages(pages=pages)
    assert result.aggregate_code_603 == Decimal("77.00")
    assert result.aggregate_code_606 == Decimal("2.00")


def test_parse_iuvo_pdf_sample_values_when_available() -> None:
    sample_path = SAMPLE_PDF_PATHS["iuvo"]
    if not sample_path.exists():
        pytest.skip(f"sample PDF not available: {sample_path}")

    result = parse_iuvo_pdf(input_pdf=sample_path)
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("1605.56")
    assert result.aggregate_code_606 == Decimal("0.00")

    assert _info("Interest income (EUR)", result.informative_rows) == Decimal("673.56")
    assert _info("Late fees (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Secondary market gains (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Campaign rewards (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Interest income iuvoSAVE (EUR)", result.informative_rows) == Decimal("932.00")
    assert _info("Secondary market fees (EUR)", result.informative_rows) == Decimal("0.00")
    assert _info("Secondary market losses (EUR)", result.informative_rows) == Decimal("-58.25")
