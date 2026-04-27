from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from integrations.crypto.kraken.kraken_to_ir import load_and_map_kraken_csv_to_ir
from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from tests.integrations.crypto.kraken import support as h


def _map_rows(
    tmp_path: Path,
    *,
    rows: list[dict[str, str]],
    rates: dict[str, Decimal] | None = None,
) -> tuple[IrAnalysisSummary, list]:
    input_csv = tmp_path / "mapping_input.csv"
    h.write_kraken_csv(input_csv, rows=rows)
    summary = IrAnalysisSummary()
    result = load_and_map_kraken_csv_to_ir(
        input_csv=str(input_csv),
        summary=summary,
        eur_unit_rate_provider=h.rate_provider(rates or {"EUR": Decimal("1"), "USD": Decimal("1")}),
    )
    return summary, result.ir_rows


def test_deposit_fiat_maps_to_ir_deposit_fiat(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-01 00:00:00",
                tx_type="deposit",
                subtype="",
                aclass="currency",
                subclass="fiat",
                asset="EUR",
                wallet="spot",
                amount="100",
                fee="0",
            )
        ],
    )
    assert len(rows) == 1
    assert rows[0].transaction_type == "Deposit"
    assert rows[0].asset_type == "fiat"
    assert rows[0].quantity == Decimal("100")


def test_deposit_non_fiat_maps_to_receive_like_deposit(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-01 00:00:00",
                tx_type="deposit",
                subtype="",
                aclass="currency",
                subclass="stable_coin",
                asset="USDC",
                wallet="spot",
                amount="25",
                fee="0",
                review_status="CARRY_OVER_BASIS",
                cost_basis_eur="20",
            )
        ],
    )

    assert len(rows) == 1
    assert rows[0].transaction_type == "Deposit"
    assert rows[0].asset_type == "crypto"
    assert rows[0].source_transaction_type == "Receive"
    assert rows[0].review_status == "CARRY-OVER-BASIS"
    assert rows[0].cost_basis_eur == Decimal("20")


def test_spend_receive_pair_maps_to_single_buy(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t-spend",
                refid="r-buy-1",
                time="2025-01-02 10:00:00",
                tx_type="spend",
                subtype="",
                aclass="currency",
                subclass="fiat",
                asset="EUR",
                wallet="spot",
                amount="-1970.44",
                fee="29.56",
            ),
            h.row(
                txid="t-receive",
                refid="r-buy-1",
                time="2025-01-02 10:00:00",
                tx_type="receive",
                subtype="",
                aclass="currency",
                subclass="crypto",
                asset="ETH",
                wallet="spot",
                amount="0.86501002",
                fee="0",
            ),
        ],
    )

    assert len(rows) == 1
    buy_row = rows[0]
    assert buy_row.transaction_type == "Buy"
    assert buy_row.operation_id == "r-buy-1"
    assert buy_row.asset == "ETH"
    assert buy_row.quantity == Decimal("0.86501002")
    assert buy_row.proceeds_eur == Decimal("1970.44")
    assert buy_row.fee_eur == Decimal("29.56")


def test_standalone_receive_maps_to_manual_deposit_with_net_quantity(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-03 00:00:00",
                tx_type="receive",
                subtype="",
                aclass="currency",
                subclass="crypto",
                asset="ETH",
                wallet="spot",
                amount="1.5",
                fee="0.1",
                review_status="RESET_BASIS_FROM_PRIOR_TAX_EVENT",
                cost_basis_eur="1000",
            )
        ],
    )
    assert len(rows) == 1
    assert rows[0].transaction_type == "Deposit"
    assert rows[0].source_transaction_type == "Receive"
    assert rows[0].quantity == Decimal("1.4")
    assert rows[0].review_status == "CARRY-OVER-BASIS"
    assert rows[0].cost_basis_eur == Decimal("1000")


def test_standalone_receive_gift_sets_zero_basis(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-03 00:00:00",
                tx_type="receive",
                subtype="",
                aclass="currency",
                subclass="crypto",
                asset="ETH",
                wallet="spot",
                amount="1.5",
                fee="0.1",
                review_status="GIFT",
                cost_basis_eur="1000",
            )
        ],
    )
    assert len(rows) == 1
    assert rows[0].transaction_type == "Deposit"
    assert rows[0].source_transaction_type == "Receive"
    assert rows[0].quantity == Decimal("1.4")
    assert rows[0].review_status == "GIFT"
    assert rows[0].cost_basis_eur == Decimal("0")


def test_standalone_receive_missing_basis_is_warning_and_manual_check(tmp_path: Path) -> None:
    summary, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-03 00:00:00",
                tx_type="receive",
                subtype="",
                aclass="currency",
                subclass="crypto",
                asset="ETH",
                wallet="spot",
                amount="1.5",
                fee="0",
                review_status="CARRY_OVER_BASIS",
                cost_basis_eur="",
            )
        ],
    )
    assert len(rows) == 0
    assert summary.unsupported_transaction_rows == 1
    assert summary.manual_check_required is True
    assert any("missing Cost Basis" in warning for warning in summary.warnings)


def test_deposit_non_fiat_missing_review_status_is_warning_and_manual_check(tmp_path: Path) -> None:
    summary, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-03 00:00:00",
                tx_type="deposit",
                subtype="",
                aclass="currency",
                subclass="stable_coin",
                asset="USDC",
                wallet="spot",
                amount="25",
                fee="0",
                review_status="",
                cost_basis_eur="20",
            )
        ],
    )
    assert len(rows) == 0
    assert summary.unsupported_transaction_rows == 1
    assert summary.manual_check_required is True
    assert any("missing Review Status" in warning for warning in summary.warnings)


def test_deposit_non_fiat_non_taxable_does_not_require_cost_basis_and_is_mapped(tmp_path: Path) -> None:
    summary, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-03 00:00:00",
                tx_type="deposit",
                subtype="",
                aclass="currency",
                subclass="stable_coin",
                asset="USDC",
                wallet="spot",
                amount="25",
                fee="0",
                review_status="NON-TAXABLE",
                cost_basis_eur="",
            )
        ],
    )
    assert len(rows) == 1
    assert rows[0].transaction_type == "Deposit"
    assert rows[0].review_status == "NON-TAXABLE"
    assert rows[0].cost_basis_eur is None
    assert rows[0].proceeds_eur == Decimal("25")
    assert summary.unsupported_transaction_rows == 0
    assert summary.manual_check_required is False
    assert len(summary.warnings) == 0


def test_trade_pair_maps_sell_buy_with_net_buy_quantity_and_implied_fee(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t-sell",
                refid="r-trade-1",
                time="2025-01-04 00:00:00",
                tx_type="trade",
                subtype="tradespot",
                aclass="currency",
                subclass="stable_coin",
                asset="USDC",
                wallet="spot",
                amount="-2000",
                fee="0",
            ),
            h.row(
                txid="t-buy",
                refid="r-trade-1",
                time="2025-01-04 00:00:00",
                tx_type="trade",
                subtype="tradespot",
                aclass="currency",
                subclass="crypto",
                asset="BTC",
                wallet="spot",
                amount="0.0562",
                fee="0.0001124",
            ),
        ],
        rates={"EUR": Decimal("1"), "USD": Decimal("0.8"), "BTC": Decimal("99999")},
    )

    assert len(rows) == 2
    sell_row = rows[0]
    buy_row = rows[1]

    assert sell_row.transaction_type == "Sell"
    assert sell_row.proceeds_eur == Decimal("1600")

    assert buy_row.transaction_type == "Buy"
    assert buy_row.quantity == Decimal("0.0560876")
    assert buy_row.proceeds_eur == Decimal("1600")
    assert buy_row.fee_eur == Decimal("3.2")


def test_earn_reward_maps_to_zero_cost_earn(tmp_path: Path) -> None:
    _, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-05 00:00:00",
                tx_type="earn",
                subtype="reward",
                aclass="currency",
                subclass="crypto",
                asset="ETH",
                wallet="spot",
                amount="0.5",
                fee="0.1",
            )
        ],
    )
    assert len(rows) == 1
    assert rows[0].transaction_type == "Earn"
    assert rows[0].quantity == Decimal("0.4")
    assert rows[0].proceeds_eur == Decimal("0")
    assert rows[0].cost_basis_eur == Decimal("0")


def test_transfer_and_earn_autoallocation_are_ignored(tmp_path: Path) -> None:
    summary, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-06 00:00:00",
                tx_type="transfer",
                subtype="spotfromfutures",
                aclass="currency",
                subclass="crypto",
                asset="ETHW",
                wallet="spot",
                amount="1",
            ),
            h.row(
                txid="t2",
                refid="",
                time="2025-01-06 00:00:01",
                tx_type="earn",
                subtype="autoallocation",
                aclass="currency",
                subclass="crypto",
                asset="ETH",
                wallet="spot",
                amount="0.1",
            ),
        ],
    )
    assert len(rows) == 0
    assert summary.unsupported_transaction_rows == 0


def test_unknown_combo_adds_warning_and_manual_check(tmp_path: Path) -> None:
    summary, rows = _map_rows(
        tmp_path,
        rows=[
            h.row(
                txid="t1",
                refid="",
                time="2025-01-07 00:00:00",
                tx_type="mystery",
                subtype="x",
                aclass="currency",
                subclass="crypto",
                asset="BTC",
                wallet="spot",
                amount="1",
            )
        ],
    )
    assert len(rows) == 0
    assert summary.unsupported_transaction_rows == 1
    assert summary.manual_check_required is True
    assert len(summary.warnings) == 1
