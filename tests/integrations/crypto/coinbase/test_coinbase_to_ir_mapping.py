from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from integrations.crypto.coinbase.coinbase_to_ir import load_and_map_coinbase_csv_to_ir
from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from tests.integrations.crypto.coinbase import support as h


def _map_rows(
    tmp_path: Path,
    *,
    rows: list[dict[str, str]],
) -> tuple[IrAnalysisSummary, list]:
    input_csv = tmp_path / "mapping_input.csv"
    h.write_coinbase_csv(input_csv, rows=rows)
    summary = IrAnalysisSummary()
    result = load_and_map_coinbase_csv_to_ir(
        input_csv=str(input_csv),
        summary=summary,
        eur_unit_rate_provider=h.rate_provider({"EUR": Decimal("1"), "USD": Decimal("0.8")}),
    )
    return summary, result.ir_rows


def test_buy_and_sell_use_total_value_for_ir_proceeds(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€1000",
                total="€1020",
                fees="€20",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="1",
                subtotal="€1000",
                total="€980",
                fees="€-20",
            ),
        ],
    )

    assert rows[0].transaction_type == "Buy"
    assert rows[0].quantity == Decimal("1")
    assert rows[0].proceeds_eur == Decimal("1020")
    assert rows[0].fee_eur == Decimal("20")

    assert rows[1].transaction_type == "Sell"
    assert rows[1].quantity == Decimal("-1")
    assert rows[1].proceeds_eur == Decimal("980")
    assert rows[1].fee_eur == Decimal("-20")


def test_convert_is_split_to_sell_and_buy_ir_legs(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Convert",
                asset="ETH",
                qty="0",
                subtotal="€1000",
                total="€980",
                fees="€-20",
                notes="Converted 1 ETH to 0.5 BTC",
            ),
        ],
    )

    assert len(rows) == 2
    sell_leg = rows[0]
    buy_leg = rows[1]

    assert sell_leg.transaction_type == "Sell"
    assert sell_leg.operation_leg == "SELL"
    assert sell_leg.asset == "ETH"
    assert sell_leg.quantity == Decimal("-1")
    assert sell_leg.proceeds_eur == Decimal("1000")
    assert sell_leg.fee_eur is None

    assert buy_leg.transaction_type == "Buy"
    assert buy_leg.operation_leg == "BUY"
    assert buy_leg.asset == "BTC"
    assert buy_leg.quantity == Decimal("0.5")
    assert buy_leg.proceeds_eur == Decimal("980")
    assert buy_leg.fee_eur == Decimal("-20")


def test_withdrawal_alias_maps_to_withdraw(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Withdrawal",
                asset="EUR",
                qty="-2.5",
                subtotal="€2.50",
                total="€2.35",
                fees="€-0.15",
            )
        ],
    )

    assert len(rows) == 1
    assert rows[0].transaction_type == "Withdraw"
    assert rows[0].quantity == Decimal("-2.5")


def test_receive_maps_to_deposit_with_explicit_cost_basis(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Receive",
                asset="BTC",
                qty="0.1",
                review_status="CARRY_OVER_BASIS",
                purchase_price="1000",
            )
        ],
    )

    assert len(rows) == 1
    assert rows[0].transaction_type == "Deposit"
    assert rows[0].source_transaction_type == "Receive"
    assert rows[0].cost_basis_eur == Decimal("1000")
    assert rows[0].review_status == "CARRY-OVER-BASIS"


def test_mapping_tracks_manual_check_overrides_and_unsupported_rows(tmp_path: Path) -> None:
    summary, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€101",
                review_status="OVERRIDE",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Mystery Reward",
                asset="BTC",
                qty="0.1",
                subtotal="€10",
                total="€10",
                review_status="SOMETHING",
            ),
        ],
    )

    assert len(rows) == 1
    assert summary.manual_check_overrides_rows == 2
    assert summary.unsupported_transaction_rows == 1
    assert "Mystery Reward" in summary.unknown_transaction_types
    assert len(summary.warnings) == 1


def test_asset_type_is_derived_from_transaction_semantics(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Deposit",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Withdrawal",
                asset="BTC",
                qty="0.2",
                subtotal="€2.50",
                total="€2.35",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Send",
                asset="EUR",
                qty="1",
                subtotal="€1",
                total="€1",
                review_status="NON-TAXABLE",
            ),
            h.row(
                timestamp="2025-01-04 00:00:00 UTC",
                tx_type="Receive",
                asset="USD",
                qty="10",
                price_currency="USD",
                subtotal="$10",
                total="$10",
                review_status="CARRY_OVER_BASIS",
                purchase_price="10",
            ),
        ],
    )

    assert rows[0].transaction_type == "Deposit"
    assert rows[0].asset_type == "fiat"
    assert rows[1].transaction_type == "Withdraw"
    assert rows[1].asset_type == "fiat"
    assert rows[2].source_transaction_type == "Send"
    assert rows[2].asset_type == "crypto"
    assert rows[3].source_transaction_type == "Receive"
    assert rows[3].asset_type == "crypto"
