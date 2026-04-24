from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.robocash.robocash_parser import parse_robocash_pages
from integrations.p2p.shared.appendix6_models import (
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    UnsupportedSecondaryMarketModeError,
)


def _synthetic_pages(*, bonus: str = "0.00", withheld: str = "0.00") -> list[str]:
    return [
        "\n".join(
            [
                "Tax report for the year ended 31.12.2025",
                "Earned interest €767.61",
                f"Earned income from bonuses €{bonus}",
                f"Taxes withheld €{withheld}",
            ]
        )
    ]


def _info(label: str, rows: list) -> Decimal | str:
    for row in rows:
        if row.label == label:
            return row.value
    raise AssertionError(f"missing informative row: {label}")


def test_parse_robocash_pages_happy_path() -> None:
    result = parse_robocash_pages(pages=_synthetic_pages(bonus="11.00", withheld="4.50"))

    assert result.platform == "robocash"
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("767.61")
    assert result.aggregate_code_606 == Decimal("11.00")

    assert _info("Taxes withheld (EUR)", result.informative_rows) == Decimal("4.50")
    assert any(
        "Taxes withheld is parsed" in message
        for message in result.informational_messages
    )


def test_parse_robocash_pages_bonus_zero_produces_zero_code_606() -> None:
    result = parse_robocash_pages(pages=_synthetic_pages(bonus="0.00", withheld="0.00"))
    assert result.aggregate_code_606 == Decimal("0.00")


def test_parse_robocash_pages_fails_on_missing_required_field() -> None:
    pages = ["Tax report for the year ended 31.12.2025\nEarned interest €10.00"]
    with pytest.raises(P2PValidationError, match="Earned income from bonuses"):
        _ = parse_robocash_pages(pages=pages)


def test_parse_robocash_pages_fails_for_appendix_5_mode() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        _ = parse_robocash_pages(
            pages=_synthetic_pages(),
            secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_5,
        )


def test_parse_robocash_pages_whitespace_robustness() -> None:
    pages = [
        "\n".join(
            [
                "Tax report for the year ended 31.12.2025",
                "Earned interest € 767.61",
                "Earned income from bonuses € 0.00",
                "Taxes withheld € 0.00",
            ]
        )
    ]
    result = parse_robocash_pages(pages=pages)
    assert result.aggregate_code_603 == Decimal("767.61")
