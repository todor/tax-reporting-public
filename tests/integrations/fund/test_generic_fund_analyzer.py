from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from integrations.fund.shared.fund_ir_models import (
    FundAnalysisSummary,
    FundCurrencyState,
    FundIrRow,
    GenericFundAnalyzerError,
)
from integrations.fund.shared.generic_fund_analyzer import analyze_fund_ir_rows


class StaticRateProvider:
    def __init__(self, rates: dict[str, Decimal]) -> None:
        self._rates = {k.upper(): v for k, v in rates.items()}

    def __call__(self, currency: str, _currency_type: str, _timestamp: datetime) -> Decimal:
        key = currency.strip().upper()
        if key not in self._rates:
            raise AssertionError(f"missing rate for {key}")
        return self._rates[key]


def _row(
    *,
    timestamp: str,
    tx_type: str,
    currency: str,
    amount: str,
    row_number: int,
    sort_index: int,
) -> FundIrRow:
    return FundIrRow(
        timestamp=datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc),
        operation_id=f"op-{row_number}",
        transaction_type=tx_type,
        currency=currency,
        currency_type="crypto",
        amount=Decimal(amount),
        source_exchange="test",
        source_row_number=row_number,
        source_transaction_type=tx_type,
        sort_index=sort_index,
    )


def test_partial_withdraw_realizes_proportional_deposit_and_profit() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="USDC", amount="100", row_number=1, sort_index=0),
        _row(timestamp="2025-01-10T00:00:00", tx_type="profit", currency="USDC", amount="20", row_number=2, sort_index=1),
        _row(timestamp="2025-01-20T00:00:00", tx_type="withdraw", currency="USDC", amount="30", row_number=3, sort_index=2),
    ]

    summary = FundAnalysisSummary()
    result = analyze_fund_ir_rows(
        ir_rows=rows,
        tax_year=2025,
        summary=summary,
        eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1")}),
    )

    bucket = result.summary.appendix_5
    assert bucket.sale_price_eur == Decimal("30")
    assert bucket.purchase_price_eur == Decimal("25")
    assert bucket.wins_eur == Decimal("5")
    assert bucket.losses_eur == Decimal("0")
    assert bucket.rows == 1

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("75")
    assert state.eur_deposit_balance == Decimal("75")
    assert state.native_profit_balance == Decimal("15")


def test_full_withdraw_clears_state() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="USDC", amount="100", row_number=1, sort_index=0),
        _row(timestamp="2025-02-01T00:00:00", tx_type="profit", currency="USDC", amount="50", row_number=2, sort_index=1),
        _row(timestamp="2025-03-01T00:00:00", tx_type="withdraw", currency="USDC", amount="150", row_number=3, sort_index=2),
    ]

    summary = FundAnalysisSummary()
    result = analyze_fund_ir_rows(
        ir_rows=rows,
        tax_year=2025,
        summary=summary,
        eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1")}),
    )

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("0")
    assert state.eur_deposit_balance == Decimal("0")
    assert state.native_profit_balance == Decimal("0")


def test_multiple_currencies_are_independent() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="USDC", amount="100", row_number=1, sort_index=0),
        _row(timestamp="2025-01-02T00:00:00", tx_type="deposit", currency="ETH", amount="2", row_number=2, sort_index=1),
        _row(timestamp="2025-01-03T00:00:00", tx_type="profit", currency="ETH", amount="0.5", row_number=3, sort_index=2),
        _row(timestamp="2025-01-10T00:00:00", tx_type="withdraw", currency="USDC", amount="20", row_number=4, sort_index=3),
    ]

    summary = FundAnalysisSummary()
    result = analyze_fund_ir_rows(
        ir_rows=rows,
        tax_year=2025,
        summary=summary,
        eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1"), "ETH": Decimal("2000")}),
    )

    usdc = result.summary.state_by_currency["USDC"]
    eth = result.summary.state_by_currency["ETH"]

    assert usdc.native_deposit_balance == Decimal("80")
    assert usdc.native_profit_balance == Decimal("0")
    assert eth.native_deposit_balance == Decimal("2")
    assert eth.native_profit_balance == Decimal("0.5")


def test_withdraw_more_than_available_fails() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="USDC", amount="100", row_number=1, sort_index=0),
        _row(timestamp="2025-01-02T00:00:00", tx_type="withdraw", currency="USDC", amount="100.1", row_number=2, sort_index=1),
    ]

    with pytest.raises(GenericFundAnalyzerError, match="withdrawal exceeds current total balance"):
        analyze_fund_ir_rows(
            ir_rows=rows,
            tax_year=2025,
            summary=FundAnalysisSummary(),
            eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1")}),
        )


def test_profit_row_can_book_loss() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="USDC", amount="100", row_number=1, sort_index=0),
        _row(timestamp="2025-01-10T00:00:00", tx_type="profit", currency="USDC", amount="-10", row_number=2, sort_index=1),
    ]

    result = analyze_fund_ir_rows(
        ir_rows=rows,
        tax_year=2025,
        summary=FundAnalysisSummary(),
        eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1")}),
    )

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("100")
    assert state.native_profit_balance == Decimal("-10")


def test_profit_row_negative_total_fails() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="USDC", amount="100", row_number=1, sort_index=0),
        _row(timestamp="2025-01-10T00:00:00", tx_type="profit", currency="USDC", amount="-200", row_number=2, sort_index=1),
    ]

    with pytest.raises(GenericFundAnalyzerError, match="negative"):
        analyze_fund_ir_rows(
            ir_rows=rows,
            tax_year=2025,
            summary=FundAnalysisSummary(),
            eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1")}),
        )


def test_opening_state_is_applied() -> None:
    rows = [
        _row(timestamp="2026-01-01T00:00:00", tx_type="withdraw", currency="USDC", amount="5", row_number=1, sort_index=0),
    ]

    opening = {
        "USDC": FundCurrencyState(
            currency="USDC",
            currency_type="crypto",
            native_deposit_balance=Decimal("10"),
            eur_deposit_balance=Decimal("9"),
            native_profit_balance=Decimal("2"),
        )
    }

    result = analyze_fund_ir_rows(
        ir_rows=rows,
        tax_year=2026,
        summary=FundAnalysisSummary(),
        eur_unit_rate_provider=StaticRateProvider({"USDC": Decimal("1")}),
        opening_state_by_currency=opening,
    )

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("5.833333333333333333333333333")
    assert state.native_profit_balance == Decimal("1.166666666666666666666666667")


def test_sale_price_uses_withdraw_timestamp_rate() -> None:
    rows = [
        _row(timestamp="2025-01-01T00:00:00", tx_type="deposit", currency="ETH", amount="1", row_number=1, sort_index=0),
        _row(timestamp="2025-01-02T00:00:00", tx_type="withdraw", currency="ETH", amount="1", row_number=2, sort_index=1),
    ]

    result = analyze_fund_ir_rows(
        ir_rows=rows,
        tax_year=2025,
        summary=FundAnalysisSummary(),
        eur_unit_rate_provider=StaticRateProvider({"ETH": Decimal("2500")}),
    )

    bucket = result.summary.appendix_5
    assert bucket.sale_price_eur == Decimal("2500")
    assert bucket.purchase_price_eur == Decimal("2500")
