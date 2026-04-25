from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.crypto.coinbase import report_analyzer as analyzer
from tests.integrations.crypto.coinbase import support as h


def test_sell_from_flat_opens_short_without_realized_pnl(tmp_path: Path) -> None:
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
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("0")
    assert app5.purchase_price_eur == Decimal("0")
    assert app5.wins_eur == Decimal("0")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 0

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("-1")
    assert holding.total_cost_eur == Decimal("-100")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[0]["Purchase Price (EUR)"] == ""
    assert out_rows[0]["Sale Price (EUR)"] == ""
    assert out_rows[0]["Net Profit (EUR)"] == ""


def test_buy_partially_closes_short_and_preserves_short_average(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="10",
                subtotal="€1000",
                total="€1000",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="4",
                subtotal="€320",
                total="€320",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("400")
    assert app5.purchase_price_eur == Decimal("320")
    assert app5.wins_eur == Decimal("80")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("-6")
    assert holding.total_cost_eur == Decimal("-600")
    assert holding.average_price_eur == Decimal("100")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Purchase Price (EUR)"] == "320.00000000"
    assert out_rows[1]["Sale Price (EUR)"] == "400.00000000"
    assert out_rows[1]["Net Profit (EUR)"] == "80.00000000"


def test_buy_fully_closes_short_position(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="10",
                subtotal="€1000",
                total="€1000",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="10",
                subtotal="€1200",
                total="€1200",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("1000")
    assert app5.purchase_price_eur == Decimal("1200")
    assert app5.wins_eur == Decimal("0")
    assert app5.losses_eur == Decimal("200")
    assert app5.rows == 1
    assert "BTC" not in result.summary.holdings_by_asset


def test_short_to_long_flip_realizes_only_closing_part(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="10",
                subtotal="€1000",
                total="€1000",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="20",
                subtotal="€1800",
                total="€1800",
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

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("10")
    assert holding.total_cost_eur == Decimal("900")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Purchase Price (EUR)"] == "900.00000000"
    assert out_rows[1]["Sale Price (EUR)"] == "1000.00000000"
    assert out_rows[1]["Net Profit (EUR)"] == "100.00000000"


def test_long_to_short_flip_realizes_only_closing_part(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="10",
                subtotal="€1000",
                total="€1000",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="20",
                subtotal="€2200",
                total="€2200",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("1100")
    assert app5.purchase_price_eur == Decimal("1000")
    assert app5.wins_eur == Decimal("100")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("-10")
    assert holding.total_cost_eur == Decimal("-1100")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Purchase Price (EUR)"] == "1000.00000000"
    assert out_rows[1]["Sale Price (EUR)"] == "1100.00000000"
    assert out_rows[1]["Net Profit (EUR)"] == "100.00000000"


def test_convert_source_leg_uses_signed_logic_for_long_and_short(tmp_path: Path) -> None:
    long_case = h.run(
        tmp_path,
        file_name="convert_long.csv",
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

    long_app5 = long_case.summary.appendix_5
    assert long_app5.sale_price_eur == Decimal("150")
    assert long_app5.purchase_price_eur == Decimal("100")
    assert long_app5.wins_eur == Decimal("50")
    assert long_app5.rows == 1

    short_case = h.run(
        tmp_path,
        file_name="convert_short.csv",
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Sell",
                asset="ETH",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Convert",
                asset="ETH",
                qty="0",
                subtotal="€40",
                total="€40",
                notes="Converted 0.5 ETH to 0.01 BTC",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    short_app5 = short_case.summary.appendix_5
    assert short_app5.rows == 0

    eth_holding = short_case.summary.holdings_by_asset["ETH"]
    btc_holding = short_case.summary.holdings_by_asset["BTC"]
    assert eth_holding.quantity == Decimal("-1.5")
    assert eth_holding.total_cost_eur == Decimal("-140")
    assert btc_holding.quantity == Decimal("0.01")
    assert btc_holding.total_cost_eur == Decimal("40")


def test_convert_target_leg_can_close_short(tmp_path: Path) -> None:
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
                tx_type="Buy",
                asset="ETH",
                qty="1",
                subtotal="€200",
                total="€200",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Convert",
                asset="ETH",
                qty="0",
                subtotal="€220",
                total="€220",
                notes="Converted 1 ETH to 2 BTC",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("320")
    assert app5.purchase_price_eur == Decimal("310")
    assert app5.wins_eur == Decimal("10")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("1")
    assert holding.total_cost_eur == Decimal("110")

    out_rows = h.read_csv(result.output_csv_path)
    convert_rows = [row for row in out_rows if row["Source Row"] == "3"]
    assert len(convert_rows) == 2
    sell_leg = next(row for row in convert_rows if row["Operation Leg"] == "SELL")
    buy_leg = next(row for row in convert_rows if row["Operation Leg"] == "BUY")

    assert sell_leg["Purchase Price (EUR)"] == "200.00000000"
    assert sell_leg["Sale Price (EUR)"] == "220.00000000"
    assert sell_leg["Net Profit (EUR)"] == "20.00000000"

    assert buy_leg["Purchase Price (EUR)"] == "110.00000000"
    assert buy_leg["Sale Price (EUR)"] == "100.00000000"
    assert buy_leg["Net Profit (EUR)"] == "-10.00000000"


def test_receive_can_close_short_and_realize_pnl(tmp_path: Path) -> None:
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
    assert app5.sale_price_eur == Decimal("150")
    assert app5.purchase_price_eur == Decimal("120")
    assert app5.wins_eur == Decimal("30")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    holding = result.summary.holdings_by_asset["BTC"]
    assert holding.quantity == Decimal("-0.5")
    assert holding.total_cost_eur == Decimal("-50")

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[1]["Purchase Price (EUR)"] == "120.00000000"
    assert out_rows[1]["Sale Price (EUR)"] == "150.00000000"
    assert out_rows[1]["Net Profit (EUR)"] == "30.00000000"


def test_same_direction_extensions_do_not_realize_pnl(tmp_path: Path) -> None:
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
                tx_type="Sell",
                asset="BTC",
                qty="2",
                subtotal="€220",
                total="€220",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Buy",
                asset="ETH",
                qty="1",
                subtotal="€300",
                total="€300",
            ),
            h.row(
                timestamp="2025-01-04 00:00:00 UTC",
                tx_type="Buy",
                asset="ETH",
                qty="2",
                subtotal="€500",
                total="€500",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.rows == 0
    assert app5.sale_price_eur == Decimal("0")
    assert app5.purchase_price_eur == Decimal("0")

    btc = result.summary.holdings_by_asset["BTC"]
    eth = result.summary.holdings_by_asset["ETH"]
    assert btc.quantity == Decimal("-3")
    assert btc.total_cost_eur == Decimal("-320")
    assert eth.quantity == Decimal("3")
    assert eth.total_cost_eur == Decimal("800")


def test_send_against_short_is_rejected_with_clear_error(tmp_path: Path) -> None:
    rows = [
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
            tx_type="Send",
            asset="BTC",
            qty="0.1",
            subtotal="€15",
            total="€15",
            review_status="TAXABLE",
        ),
    ]

    with pytest.raises(analyzer.CoinbaseAnalyzerError, match="Send requires existing long holdings"):
        _ = h.run(tmp_path, rows=rows, rates={"EUR": Decimal("1")})


def test_end_to_end_short_scenario_totals_and_text(tmp_path: Path) -> None:
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
                tx_type="Buy",
                asset="BTC",
                qty="0.4",
                subtotal="€30",
                total="€30",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€120",
                total="€120",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("100")
    assert app5.purchase_price_eur == Decimal("102")
    assert app5.wins_eur == Decimal("10")
    assert app5.losses_eur == Decimal("12")
    assert app5.net_result_eur == Decimal("-2")
    assert app5.rows == 2

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "- Код 5082" in text
    assert "  Продажна цена: 100.00 EUR" in text
    assert "  Цена на придобиване: 102.00 EUR" in text
    assert "  Печалба: 10.00 EUR" in text
    assert "  Загуба: 12.00 EUR" in text
    assert "- Нетен резултат: -2.00 EUR" in text
