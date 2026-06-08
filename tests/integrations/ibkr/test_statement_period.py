from __future__ import annotations

import pytest

from integrations.ibkr.activity_statement_analyzer import (
    _validate_base_currency,
    _validate_statement_period,
)
from integrations.ibkr.models import IbkrAnalyzerError
from integrations.shared.contracts import UserFacingTaxError


def _rows(period: str | None) -> list[list[str]]:
    rows = [["Statement", "Header", "Field Name", "Field Value"]]
    if period is not None:
        rows.append(["Statement", "Data", "Period", period])
    return rows


def test_statement_period_accepts_full_tax_year() -> None:
    _validate_statement_period(_rows("January 1, 2025 - December 31, 2025"), tax_year=2025)


def test_statement_period_accepts_full_tax_year_with_trailing_empty_cells() -> None:
    rows = [["Statement", "Data", "Period", "January 1, 2025 - December 31, 2025", "", "", ""]]

    _validate_statement_period(rows, tax_year=2025)


def test_statement_period_rejects_missing_period() -> None:
    with pytest.raises(IbkrAnalyzerError, match="statement period is missing"):
        _validate_statement_period(_rows(None), tax_year=2025)


def test_statement_period_rejects_wrong_start_date() -> None:
    with pytest.raises(IbkrAnalyzerError, match="must cover exactly"):
        _validate_statement_period(_rows("January 2, 2025 - December 31, 2025"), tax_year=2025)


def test_statement_period_rejects_wrong_end_date() -> None:
    with pytest.raises(IbkrAnalyzerError, match="must cover exactly"):
        _validate_statement_period(_rows("January 1, 2025 - December 30, 2025"), tax_year=2025)


def test_statement_period_rejects_wrong_year() -> None:
    with pytest.raises(IbkrAnalyzerError, match="must cover exactly"):
        _validate_statement_period(_rows("January 1, 2024 - December 31, 2024"), tax_year=2025)


def test_statement_period_rejects_malformed_period() -> None:
    with pytest.raises(IbkrAnalyzerError, match="statement period is malformed"):
        _validate_statement_period(_rows("2025-01-01 to 2025-12-31"), tax_year=2025)


def test_base_currency_validation_accepts_eur_account_information() -> None:
    _validate_base_currency(
        [
            ["Account Information", "Header", "Field Name", "Field Value"],
            ["Account Information", "Data", "Base Currency", "EUR"],
        ]
    )


def test_base_currency_validation_rejects_missing_base_currency() -> None:
    with pytest.raises(UserFacingTaxError, match="Unsupported IBKR base currency '<missing>'"):
        _validate_base_currency([["Account Information", "Header", "Field Name", "Field Value"]])


def test_base_currency_validation_rejects_non_eur_base_currency() -> None:
    with pytest.raises(UserFacingTaxError, match="Unsupported IBKR base currency 'USD'"):
        _validate_base_currency(
            [
                ["Account Information", "Header", "Field Name", "Field Value"],
                ["Account Information", "Data", "Base Currency", "USD"],
            ]
        )


def test_statement_period_validation_is_not_skippable() -> None:
    with pytest.raises(IbkrAnalyzerError, match="must cover exactly"):
        _validate_statement_period(_rows("January 1, 2024 - December 31, 2024"), tax_year=2025)
