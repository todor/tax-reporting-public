from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.afranga.afranga_parser import parse_afranga_pages
from integrations.p2p.shared.appendix6_models import (
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    SECONDARY_MARKET_MODE_APPENDIX_6,
    UnsupportedSecondaryMarketModeError,
)
from tests.integrations.p2p.afranga.support import afranga_sample_pages


def _pages_with_summary_values(
    *,
    interest_received: str = "200.00",
    late_interest_received: str = "10.00",
    bonuses: str = "100.00",
    secondary: str = "-5.00",
) -> list[str]:
    pages = afranga_sample_pages()
    pages[0] = [
        "Account Statement",
        "Reporting year: 2025",
        "for the period between 2025-01-01 till 2025-12-31",
        f"Income from interest received {interest_received} EUR",
        f"Income from late interest received {late_interest_received} EUR",
        f"Bonuses {bonuses} EUR",
        f"Income/loss from secondary market discount/premium {secondary} EUR",
    ]
    return ["\n".join(page) for page in pages]


def test_parse_afranga_pages_extracts_summary_metrics_and_part1_rows() -> None:
    result = parse_afranga_pages(pages=_pages_with_summary_values())

    assert result.platform == "afranga"
    assert result.tax_year == 2025
    assert len(result.part1_rows) == 2
    assert result.part1_rows[0].payer_eik == "202557159"
    assert result.part1_rows[0].payer_name == "Stick Credit AD"
    assert result.part1_rows[0].code == "603"
    assert result.part1_rows[0].amount == Decimal("55.00")
    assert result.part1_rows[1].amount == Decimal("80.00")

    assert result.withheld_tax == Decimal("9.50")
    assert any(row.label == "Statement period" and row.value == "2025-01-01 till 2025-12-31" for row in result.informative_rows)


def test_parse_afranga_pages_computes_aggregate_code_603() -> None:
    result = parse_afranga_pages(pages=_pages_with_summary_values())
    assert result.aggregate_code_603 == Decimal("84.50")
    assert result.taxable_code_603 == Decimal("219.50")


def test_parse_afranga_pages_secondary_market_zero_keeps_bonus_only() -> None:
    result = parse_afranga_pages(
        pages=_pages_with_summary_values(secondary="0.00"),
        secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_6,
    )
    assert result.aggregate_code_606 == Decimal("100.00")
    assert result.taxable_code_606 == Decimal("100.00")


def test_parse_afranga_pages_secondary_market_positive_adds_to_code_606() -> None:
    result = parse_afranga_pages(
        pages=_pages_with_summary_values(secondary="12.25"),
        secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_6,
    )
    assert result.aggregate_code_606 == Decimal("112.25")


def test_parse_afranga_pages_secondary_market_negative_not_subtracted() -> None:
    result = parse_afranga_pages(
        pages=_pages_with_summary_values(secondary="-9.00"),
        secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_6,
    )
    assert result.aggregate_code_606 == Decimal("100.00")


def test_parse_afranga_pages_fails_when_aggregate_code_603_is_negative() -> None:
    with pytest.raises(P2PValidationError, match="aggregate_code_603"):
        _ = parse_afranga_pages(
            pages=_pages_with_summary_values(interest_received="10.00", late_interest_received="0.00"),
        )


def test_parse_afranga_pages_fails_on_missing_required_summary_fields() -> None:
    pages = _pages_with_summary_values()
    page0_lines = [line for line in pages[0].splitlines() if not line.startswith("Bonuses")]
    pages[0] = "\n".join(page0_lines)

    with pytest.raises(P2PValidationError, match="missing required field"):
        _ = parse_afranga_pages(pages=pages)


def test_parse_afranga_pages_fails_for_appendix_5_mode() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        _ = parse_afranga_pages(
            pages=_pages_with_summary_values(),
            secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_5,
        )


def test_parse_afranga_pages_emits_warning_on_total_row_mismatch() -> None:
    pages = _pages_with_summary_values()
    page1_lines = pages[1].splitlines()
    page1_lines[-1] = "Total 999.00 999.00 999.00"
    pages[1] = "\n".join(page1_lines)

    result = parse_afranga_pages(pages=pages)
    assert any("Appendix total row mismatch" in warning for warning in result.warnings)


def test_parse_afranga_pages_allows_missing_appendix_section() -> None:
    pages = _pages_with_summary_values()
    pages[1] = "Random other section\nNo appendix data"

    result = parse_afranga_pages(pages=pages)
    assert result.part1_rows == []
    assert result.withheld_tax == Decimal("0")
    assert result.aggregate_code_603 == Decimal("210.00")
