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
