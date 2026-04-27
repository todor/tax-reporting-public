from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from integrations.crypto.shared.crypto_ir_models import CryptoIrRow, IrAnalysisSummary
from integrations.crypto.shared.generic_crypto_analyzer import analyze_ir_rows


def _row(
    *,
    operation_id: str,
    tx_type: str,
    asset: str,
    quantity: str,
    proceeds: str | None,
    row_number: int,
    source_tx: str | None = None,
    operation_leg: str | None = None,
    cost_basis: str | None = None,
    review_status: str | None = None,
) -> CryptoIrRow:
    return CryptoIrRow(
        timestamp=datetime(2025, 1, row_number, tzinfo=timezone.utc),
        operation_id=operation_id,
        transaction_type=tx_type,
        asset=asset,
        asset_type="crypto",
        quantity=Decimal(quantity),
        proceeds_eur=None if proceeds is None else Decimal(proceeds),
        fee_eur=None,
        cost_basis_eur=None if cost_basis is None else Decimal(cost_basis),
        review_status=review_status,
        source_exchange="coinbase",
        source_row_number=row_number,
        source_transaction_type=source_tx or tx_type,
        operation_leg=operation_leg,
    )


def test_short_to_long_flip_realizes_only_closing_leg() -> None:
    rows = [
        _row(operation_id="op-1", tx_type="Sell", asset="BTC", quantity="-10", proceeds="1000", row_number=1),
        _row(operation_id="op-2", tx_type="Buy", asset="BTC", quantity="20", proceeds="1800", row_number=2),
    ]
    summary = IrAnalysisSummary(processed_rows=2)
    result = analyze_ir_rows(ir_rows=rows, tax_year=2025, summary=summary)

    assert result.summary.appendix_5.sale_price_eur == Decimal("1000")
    assert result.summary.appendix_5.purchase_price_eur == Decimal("900")
    assert result.summary.appendix_5.wins_eur == Decimal("100")
    assert result.summary.appendix_5.losses_eur == Decimal("0")
    assert result.summary.appendix_5.rows == 1

    btc = result.summary.holdings_by_asset["BTC"]
    assert btc.quantity == Decimal("10")
    assert btc.total_cost_eur == Decimal("900")


def test_convert_legs_are_grouped_for_appendix_totals() -> None:
    rows = [
        _row(operation_id="op-1", tx_type="Sell", asset="BTC", quantity="-1", proceeds="100", row_number=1),
        _row(operation_id="op-2", tx_type="Buy", asset="ETH", quantity="1", proceeds="200", row_number=2),
        _row(
            operation_id="op-3",
            tx_type="Sell",
            asset="ETH",
            quantity="-1",
            proceeds="220",
            row_number=3,
            source_tx="Convert",
            operation_leg="SELL",
        ),
        _row(
            operation_id="op-3",
            tx_type="Buy",
            asset="BTC",
            quantity="2",
            proceeds="220",
            row_number=3,
            source_tx="Convert",
            operation_leg="BUY",
        ),
    ]
    summary = IrAnalysisSummary(processed_rows=4)
    result = analyze_ir_rows(ir_rows=rows, tax_year=2025, summary=summary)

    assert result.summary.appendix_5.sale_price_eur == Decimal("320")
    assert result.summary.appendix_5.purchase_price_eur == Decimal("310")
    assert result.summary.appendix_5.wins_eur == Decimal("10")
    assert result.summary.appendix_5.losses_eur == Decimal("0")
    assert result.summary.appendix_5.rows == 1

    sell_leg = next(row for row in result.enriched_rows if row.ir_row.operation_leg == "SELL")
    buy_leg = next(row for row in result.enriched_rows if row.ir_row.operation_leg == "BUY")
    assert sell_leg.net_profit_eur == Decimal("20")
    assert buy_leg.net_profit_eur == Decimal("-10")


def test_opening_state_year_filters_rows_for_ledger_and_declaration() -> None:
    rows = [
        CryptoIrRow(
            timestamp=datetime(2021, 1, 1, tzinfo=timezone.utc),
            operation_id="op-2021",
            transaction_type="Buy",
            asset="BTC",
            asset_type="crypto",
            quantity=Decimal("1"),
            proceeds_eur=Decimal("50"),
            fee_eur=None,
            cost_basis_eur=None,
            review_status=None,
            source_exchange="coinbase",
            source_row_number=1,
            source_transaction_type="Buy",
        ),
        CryptoIrRow(
            timestamp=datetime(2022, 1, 1, tzinfo=timezone.utc),
            operation_id="op-2022",
            transaction_type="Buy",
            asset="BTC",
            asset_type="crypto",
            quantity=Decimal("1"),
            proceeds_eur=Decimal("60"),
            fee_eur=None,
            cost_basis_eur=None,
            review_status=None,
            source_exchange="coinbase",
            source_row_number=2,
            source_transaction_type="Buy",
        ),
        CryptoIrRow(
            timestamp=datetime(2023, 1, 1, tzinfo=timezone.utc),
            operation_id="op-2023",
            transaction_type="Buy",
            asset="BTC",
            asset_type="crypto",
            quantity=Decimal("1"),
            proceeds_eur=Decimal("200"),
            fee_eur=None,
            cost_basis_eur=None,
            review_status=None,
            source_exchange="coinbase",
            source_row_number=3,
            source_transaction_type="Buy",
        ),
        CryptoIrRow(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            operation_id="op-2024",
            transaction_type="Sell",
            asset="BTC",
            asset_type="crypto",
            quantity=Decimal("-0.5"),
            proceeds_eur=Decimal("150"),
            fee_eur=None,
            cost_basis_eur=None,
            review_status=None,
            source_exchange="coinbase",
            source_row_number=4,
            source_transaction_type="Sell",
        ),
        CryptoIrRow(
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            operation_id="op-2025",
            transaction_type="Sell",
            asset="BTC",
            asset_type="crypto",
            quantity=Decimal("-1"),
            proceeds_eur=Decimal("300"),
            fee_eur=None,
            cost_basis_eur=None,
            review_status=None,
            source_exchange="coinbase",
            source_row_number=5,
            source_transaction_type="Sell",
        ),
        CryptoIrRow(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            operation_id="op-2026",
            transaction_type="Buy",
            asset="BTC",
            asset_type="crypto",
            quantity=Decimal("1"),
            proceeds_eur=Decimal("400"),
            fee_eur=None,
            cost_basis_eur=None,
            review_status=None,
            source_exchange="coinbase",
            source_row_number=6,
            source_transaction_type="Buy",
        ),
    ]
    summary = IrAnalysisSummary(processed_rows=len(rows))
    result = analyze_ir_rows(
        ir_rows=rows,
        tax_year=2025,
        summary=summary,
        opening_holdings={"BTC": (Decimal("1"), Decimal("100"))},
        opening_state_year_end=2022,
    )

    assert result.summary.rows_ignored_before_or_equal_opening_state_year == 2
    assert result.summary.rows_ignored_after_tax_year == 1
    assert result.summary.rows_applied_to_ledger == 3
    assert result.summary.rows_included_in_tax_year == 1

    assert result.summary.appendix_5.sale_price_eur == Decimal("300")
    assert result.summary.appendix_5.purchase_price_eur == Decimal("150")
    assert result.summary.appendix_5.wins_eur == Decimal("150")
    assert result.summary.appendix_5.losses_eur == Decimal("0")
    assert result.summary.appendix_5.rows == 1

    btc = result.summary.holdings_by_asset["BTC"]
    assert btc.quantity == Decimal("0.5")
    assert btc.total_cost_eur == Decimal("75")
