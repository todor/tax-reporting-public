from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tests.integrations.crypto.coinbase import support as h


def test_buy_uses_total_including_fees_for_acquisition(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="ETH",
                qty="1",
                subtotal="€3900",
                total="€4000",
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[0]["Purchase Price (EUR)"] == ""

    holding = result.summary.holdings_by_asset["ETH"]
    assert holding.quantity == Decimal("1")
    assert holding.total_cost_eur == Decimal("4000")


def test_buy_uses_total_when_subtotal_and_total_differ(tmp_path: Path) -> None:
    result = h.run(
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
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("1")
    assert holding.total_cost_eur == Decimal("1020")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[0]["Purchase Price (EUR)"] == ""


def test_sell_computes_gain_and_reduces_holdings_with_average_cost(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="2",
                subtotal="€190",
                total="€200",
            ),
            h.row(
                timestamp="2025-01-10 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="0.5",
                subtotal="€80",
                total="€80",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("80")
    assert app5.purchase_price_eur == Decimal("50")
    assert app5.wins_eur == Decimal("30")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("1.5")
    assert holding.total_cost_eur == Decimal("150")


def test_sell_uses_total_not_subtotal_for_proceeds(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€1000",
                total="€1000",
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
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("980")
    assert app5.purchase_price_eur == Decimal("1000")
    assert app5.wins_eur == Decimal("0")
    assert app5.losses_eur == Decimal("20")
    assert app5.rows == 1

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Sale Price (EUR)"] == "980.00000000"
    assert out_rows[1]["Net Profit (EUR)"] == "-20.00000000"


def test_convert_disposes_source_and_acquires_target(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="ETH",
                qty="2",
                subtotal="€190",
                total="€200",
            ),
            h.row(
                timestamp="2025-01-05 00:00:00 UTC",
                tx_type="Convert",
                asset="ETH",
                qty="0",
                subtotal="€150",
                total="€150",
                notes="Converted 1 ETH to 0.05 BTC",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("150")
    assert app5.purchase_price_eur == Decimal("100")
    assert app5.wins_eur == Decimal("50")
    assert app5.rows == 1

    eth_holding = result.summary.holdings_by_asset["ETH"]
    btc_holding = result.summary.holdings_by_asset["BTC"]
    assert eth_holding.quantity == Decimal("1")
    assert eth_holding.total_cost_eur == Decimal("100")
    assert btc_holding.quantity == Decimal("0.05")
    assert btc_holding.total_cost_eur == Decimal("150")


def test_convert_uses_subtotal_for_sell_and_total_for_buy(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="ETH",
                qty="1",
                subtotal="€900",
                total="€900",
            ),
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
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("1000")
    assert app5.purchase_price_eur == Decimal("900")
    assert app5.wins_eur == Decimal("100")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    btc_holding = result.summary.holdings_by_asset["BTC"]
    assert btc_holding.quantity == Decimal("0.5")
    assert btc_holding.total_cost_eur == Decimal("980")


def test_send_taxable_and_non_taxable_behaviour(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Send",
                asset="BTC",
                qty="0.25",
                subtotal="€40",
                total="€40",
                review_status="TAXABLE",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Send",
                asset="BTC",
                qty="0.25",
                subtotal="€50",
                total="€50",
                review_status="NON-TAXABLE",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("0")
    assert app5.purchase_price_eur == Decimal("0")
    assert app5.wins_eur == Decimal("0")
    assert app5.rows == 0
    assert result.summary.taxable_send_rows == 1
    assert result.summary.non_taxable_send_rows == 1

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Purchase Price (EUR)"] == ""
    assert out_rows[1]["Sale Price (EUR)"] == ""
    assert out_rows[1]["Net Profit (EUR)"] == ""
    assert out_rows[2]["Purchase Price (EUR)"] == ""
    assert out_rows[2]["Sale Price (EUR)"] == ""
    assert out_rows[2]["Net Profit (EUR)"] == ""


def test_withdrawal_is_treated_as_fiat_by_transaction_type(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
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
                fees="€-0.15",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    assert result.summary.ignored_fiat_deposit_withdraw_rows == 1


def test_receive_with_carry_over_basis_adds_holdings(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-10 00:00:00 UTC",
                tx_type="Receive",
                asset="ETH",
                qty="0.2",
                review_status="CARRY_OVER_BASIS",
                purchase_price="1200",
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    holding = result.summary.holdings_by_asset["ETH"]
    assert holding.quantity == Decimal("0.2")
    assert holding.total_cost_eur == Decimal("1200")


def test_receive_with_reset_basis_from_prior_tax_event_adds_holdings(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-10 00:00:00 UTC",
                tx_type="Receive",
                asset="ETH",
                qty="0.3",
                review_status="RESET_BASIS_FROM_PRIOR_TAX_EVENT",
                purchase_price="1500",
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    holding = result.summary.holdings_by_asset["ETH"]
    assert holding.quantity == Decimal("0.3")
    assert holding.total_cost_eur == Decimal("1500")


def test_receive_can_realize_pnl_when_reducing_short(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="2",
                subtotal="€200",
                total="€200",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Receive",
                asset="BTC",
                qty="1.5",
                subtotal="",
                total="",
                review_status="CARRY_OVER_BASIS",
                purchase_price="120",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.rows == 1
    assert app5.sale_price_eur == Decimal("150")
    assert app5.purchase_price_eur == Decimal("120")
    assert app5.wins_eur == Decimal("30")
    assert app5.losses_eur == Decimal("0")

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("-0.5")
    assert holding.total_cost_eur == Decimal("-50")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Purchase Price (EUR)"] == "120.00000000"
    assert out_rows[1]["Sale Price (EUR)"] == "150.00000000"
    assert out_rows[1]["Net Profit (EUR)"] == "30.00000000"


def test_receive_non_taxable_closes_short_without_taxable_pnl(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Receive",
                asset="BTC",
                qty="1",
                subtotal="",
                total="€100",
                review_status="NON-TAXABLE",
                cost_basis_eur="",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.rows == 0
    assert app5.sale_price_eur == Decimal("0")
    assert app5.purchase_price_eur == Decimal("0")
    assert app5.net_result_eur == Decimal("0")
    assert "BTC" not in result.summary.holdings_by_asset
    assert result.summary.unsupported_transaction_rows == 0
    assert result.summary.manual_check_required is False
    assert len(result.summary.warnings) == 0

    state_payload = json.loads(result.year_end_state_json_path.read_text(encoding="utf-8"))
    assert "BTC" not in state_payload["holdings_by_asset"]

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Transaction Type"] == "Deposit"
    assert out_rows[1]["Review Status"] == "NON-TAXABLE"
    assert out_rows[1]["Purchase Price (EUR)"] == ""
    assert out_rows[1]["Sale Price (EUR)"] == ""
    assert out_rows[1]["Net Profit (EUR)"] == ""


def test_reverse_chronological_input_is_processed_in_time_order(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-02-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="0.5",
                subtotal="€80",
                total="€80",
            ),
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€90",
                total="€100",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("80")
    assert app5.purchase_price_eur == Decimal("50")
    assert app5.wins_eur == Decimal("30")


def test_non_reverse_input_is_sorted_by_timestamp_before_processing(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-05 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="0.3",
                subtotal="€45",
                total="€45",
            ),
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€90",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€190",
                total="€200",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    # After sorting by timestamp: avg basis is (100 + 200) / 2 = 150; sell 0.3 => basis 45
    assert app5.sale_price_eur == Decimal("45")
    assert app5.purchase_price_eur == Decimal("45")
    assert app5.wins_eur == Decimal("0")


def test_appendix5_includes_only_selected_tax_year_disposals(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        tax_year=2025,
        rows=[
            h.row(
                timestamp="2024-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€90",
                total="€100",
            ),
            h.row(
                timestamp="2024-06-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="0.2",
                subtotal="€30",
                total="€30",
            ),
            h.row(
                timestamp="2025-02-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="0.2",
                subtotal="€30",
                total="€30",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    # 2024 disposal is excluded from declaration totals, but still affects holdings/basis.
    assert app5.sale_price_eur == Decimal("30")
    assert app5.purchase_price_eur == Decimal("20")
    assert app5.wins_eur == Decimal("10")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1


def test_opening_state_json_allows_incremental_year_processing(tmp_path: Path) -> None:
    prior_rows = [
        h.row(
            timestamp="2024-01-01 00:00:00 UTC",
            tx_type="Buy",
            asset="BTC",
            qty="1",
            subtotal="€90",
            total="€100",
        ),
        h.row(
            timestamp="2024-06-01 00:00:00 UTC",
            tx_type="Buy",
            asset="BTC",
            qty="1",
            subtotal="€190",
            total="€200",
        ),
    ]
    current_year_rows = [
        h.row(
            timestamp="2025-03-01 00:00:00 UTC",
            tx_type="Sell",
            asset="BTC",
            qty="0.5",
            subtotal="€90",
            total="€90",
        )
    ]

    prior_result = h.run(
        tmp_path,
        tax_year=2024,
        rows=prior_rows,
        rates={"EUR": Decimal("1")},
        file_name="prior.csv",
    )
    incremental_result = h.run(
        tmp_path,
        tax_year=2025,
        rows=current_year_rows,
        opening_state_json=prior_result.year_end_state_json_path,
        rates={"EUR": Decimal("1")},
        file_name="incremental.csv",
    )
    full_result = h.run(
        tmp_path,
        tax_year=2025,
        rows=prior_rows + current_year_rows,
        rates={"EUR": Decimal("1")},
        file_name="full.csv",
    )

    inc = incremental_result.summary.appendix_5
    full = full_result.summary.appendix_5
    assert inc.sale_price_eur == full.sale_price_eur
    assert inc.purchase_price_eur == full.purchase_price_eur
    assert inc.wins_eur == full.wins_eur
    assert inc.losses_eur == full.losses_eur
    assert inc.rows == full.rows


def test_year_end_state_json_uses_requested_tax_year_cutoff(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        tax_year=2025,
        rows=[
            h.row(
                timestamp="2024-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€90",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€190",
                total="€200",
            ),
            h.row(
                timestamp="2026-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€290",
                total="€300",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    payload = json.loads(result.year_end_state_json_path.read_text(encoding="utf-8"))
    assert payload["state_tax_year_end"] == 2025
    assert payload["holdings_by_asset"]["BTC"]["quantity"] == "2"
    assert payload["holdings_by_asset"]["BTC"]["total_cost_eur"] == "300"
