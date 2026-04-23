from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

from integrations.crypto.kraken import report_analyzer as analyzer
from tests.integrations.crypto.kraken import support as h

TECHNICAL_DETAILS_SEPARATOR = "------------------------------ Technical Details ------------------------------"


def test_deposit_fiat_is_ignored_for_pnl(tmp_path: Path) -> None:
    result = h.run(
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
                amount="1000",
                fee="0",
            )
        ],
        rates={"EUR": Decimal("1"), "USD": Decimal("1")},
    )

    assert result.summary.ignored_fiat_deposit_withdraw_rows == 1
    assert result.summary.appendix_5.rows == 0


def test_non_taxable_crypto_deposit_closes_short_without_taxable_pnl(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                txid="trade-sell-1",
                refid="r-trade-1",
                time="2025-01-01 10:00:00",
                tx_type="trade",
                subtype="tradespot",
                aclass="currency",
                subclass="stable_coin",
                asset="USDC",
                wallet="spot",
                amount="-100",
                fee="0",
            ),
            h.row(
                txid="trade-buy-1",
                refid="r-trade-1",
                time="2025-01-01 10:00:00",
                tx_type="trade",
                subtype="tradespot",
                aclass="currency",
                subclass="crypto",
                asset="BTC",
                wallet="spot",
                amount="0.002",
                fee="0",
            ),
            h.row(
                txid="deposit-1",
                refid="r-deposit-1",
                time="2025-01-01 12:00:00",
                tx_type="deposit",
                subtype="",
                aclass="currency",
                subclass="stable_coin",
                asset="USDC",
                wallet="spot",
                amount="100",
                fee="0",
                review_status="NON-TAXABLE",
                cost_basis_eur="",
            ),
        ],
        rates={"EUR": Decimal("1"), "USD": Decimal("1"), "BTC": Decimal("50000")},
    )

    app5 = result.summary.appendix_5
    assert app5.rows == 0
    assert app5.sale_price_eur == Decimal("0")
    assert app5.purchase_price_eur == Decimal("0")
    assert app5.net_result_eur == Decimal("0")
    assert result.summary.unsupported_transaction_rows == 0
    assert result.summary.manual_check_required is False
    assert len(result.summary.warnings) == 0

    assert "USDC" not in result.summary.holdings_by_asset
    btc_holding = result.summary.holdings_by_asset["BTC"]
    assert btc_holding.quantity == Decimal("0.002")
    assert btc_holding.total_cost_eur == Decimal("100")

    state_payload = json.loads(result.year_end_state_json_path.read_text(encoding="utf-8"))
    assert "USDC" not in state_payload["holdings_by_asset"]
    assert state_payload["holdings_by_asset"]["BTC"]["quantity"] == "0.002"

    out_rows = h.read_csv(result.output_csv_path)
    deposit_rows = [row for row in out_rows if row["Operation ID"] == "r-deposit-1"]
    assert len(deposit_rows) == 1
    assert deposit_rows[0]["Transaction Type"] == "Deposit"
    assert deposit_rows[0]["Review Status"] == "NON-TAXABLE"
    assert deposit_rows[0]["Purchase Price (EUR)"] == ""
    assert deposit_rows[0]["Sale Price (EUR)"] == ""
    assert deposit_rows[0]["Net Profit (EUR)"] == ""


def test_end_to_end_on_kraken_sample_fixture(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "kraken_sample.csv"
    input_csv = tmp_path / fixture.name
    shutil.copy(fixture, input_csv)

    result = analyzer.analyze_kraken_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_unit_rate_provider=h.rate_provider(
            {
                "EUR": Decimal("1"),
                "USD": Decimal("0.8"),
                "ETH": Decimal("2000"),
                "BTC": Decimal("30000"),
                "ETHW": Decimal("2"),
            }
        ),
    )

    assert result.output_csv_path.exists()
    assert result.declaration_txt_path.exists()
    assert result.year_end_state_json_path.exists()

    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) >= 6
    assert "Operation ID" in out_rows[0]
    assert "Purchase Price (EUR)" in out_rows[0]

    trade_buy_rows = [row for row in out_rows if row["Operation ID"] == "r-trade-1" and row["Transaction Type"] == "Buy"]
    assert len(trade_buy_rows) == 1
    assert trade_buy_rows[0]["Quantity"] == "0.00999"
    assert trade_buy_rows[0]["Proceeds (EUR)"] == "1000.0"

    spend_receive_buy_rows = [
        row for row in out_rows if row["Operation ID"] == "r-buy-1" and row["Transaction Type"] == "Buy"
    ]
    assert len(spend_receive_buy_rows) == 1
    assert spend_receive_buy_rows[0]["Proceeds (EUR)"] == "1970.44"
    assert spend_receive_buy_rows[0]["Fee (EUR)"] == "29.56"

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Приложение 5" in text
    assert TECHNICAL_DETAILS_SEPARATOR in text
    assert "Audit Data" in text
    assert "manual check overrides (Review Status non-empty): 2" in text

    state_payload = json.loads(result.year_end_state_json_path.read_text(encoding="utf-8"))
    assert state_payload["state_tax_year_end"] == 2025
    assert "holdings_by_asset" in state_payload
