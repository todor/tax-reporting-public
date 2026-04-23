from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.bondora_go_grow.bondora_go_grow_parser import (
    parse_bondora_go_grow_pages,
    parse_bondora_go_grow_pdf,
)
from integrations.p2p.shared.appendix6_models import (
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    UnsupportedSecondaryMarketModeError,
)
from tests.integrations.p2p.support import SAMPLE_PDF_PATHS


def _synthetic_pages(*, bonus_line_label: str = "Bonus income received on Bondora account*") -> list[str]:
    return [
        "\n".join(
            [
                "Go & Grow Tax Report – 01/01/2025 - 12/31/2025",
                "Go & Grow",
                "1€",
                "2€",
                "0.50€",
                "3€",
                "4€",
                "5€",
                "Total",
                "Other income",
                bonus_line_label,
                "6€",
                "Grand Total",
            ]
        )
    ]


def _info(label: str, rows: list) -> Decimal | str:
    for row in rows:
        if row.label == label:
            return row.value
    raise AssertionError(f"missing informative row: {label}")


def test_parse_bondora_go_grow_pages_happy_path() -> None:
    result = parse_bondora_go_grow_pages(pages=_synthetic_pages())

    assert result.platform == "bondora_go_grow"
    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("4")
    assert result.aggregate_code_606 == Decimal("6")

    assert _info("Interest Accrued (EUR)", result.informative_rows) == Decimal("4")
    assert _info("Bonus income received on Bondora account (EUR)", result.informative_rows) == Decimal("6")
    assert any(
        "informational only" in message
        for message in result.informational_messages
    )


def test_parse_bondora_go_grow_pages_zero_bonus_produces_zero_code_606() -> None:
    pages = _synthetic_pages()
    pages[0] = pages[0].replace("\n6€\n", "\n0€\n")
    result = parse_bondora_go_grow_pages(pages=pages)
    assert result.aggregate_code_606 == Decimal("0")


def test_parse_bondora_go_grow_pages_handles_bonusincome_spacing_quirk() -> None:
    result = parse_bondora_go_grow_pages(
        pages=_synthetic_pages(bonus_line_label="Bonusincome received on Bondora account*")
    )
    assert result.aggregate_code_606 == Decimal("6")


def test_parse_bondora_go_grow_pages_fails_on_missing_go_and_grow_row() -> None:
    pages = [
        "Go & Grow Tax Report – 01/01/2025 - 12/31/2025\n"
        "Other income\n"
        "Bonus income received on Bondora account*\n"
        "0€"
    ]
    with pytest.raises(P2PValidationError, match="Go & Grow"):
        _ = parse_bondora_go_grow_pages(pages=pages)


def test_parse_bondora_go_grow_pages_fails_for_appendix_5_mode() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        _ = parse_bondora_go_grow_pages(
            pages=_synthetic_pages(),
            secondary_market_mode=SECONDARY_MARKET_MODE_APPENDIX_5,
        )


def test_parse_bondora_go_grow_pdf_sample_values_when_available() -> None:
    sample_path = SAMPLE_PDF_PATHS["bondora_go_grow"]
    if not sample_path.exists():
        pytest.skip(f"sample PDF not available: {sample_path}")

    result = parse_bondora_go_grow_pdf(input_pdf=sample_path)

    assert result.tax_year == 2025
    assert result.aggregate_code_603 == Decimal("0.73")
    assert result.aggregate_code_606 == Decimal("0.00")
    assert _info("Interest Accrued (EUR)", result.informative_rows) == Decimal("0.73")
    assert _info("Bonus income received on Bondora account (EUR)", result.informative_rows) == Decimal("0")
