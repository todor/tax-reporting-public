from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from tests.integrations.fund.finexify import support as h


def test_basic_deposit_balance_withdraw_happy_path(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="120", date="2025-01-31"),
            h.row(tx_type="Withdraw", currency="USDC", amount="60", date="2025-02-01"),
        ],
        rates={"USDC": Decimal("0.8")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("48")
    assert app5.purchase_price_eur == Decimal("40")
    assert app5.wins_eur == Decimal("8")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    out_rows = h.read_csv(result.output_csv_path)
    deposit_row = [row for row in out_rows if row["Type"] == "deposit"][0]
    profit_row = [row for row in out_rows if row["Type"] == "profit"][0]
    withdraw_row = [row for row in out_rows if row["Type"] == "withdraw"][0]

    assert deposit_row["Amount (EUR)"] == "80.0"
    assert deposit_row["Balance"] == "100"
    assert deposit_row["Balance (EUR)"] == "80.0"
    assert deposit_row["Deposit to Date (EUR)"] == "80.0"

    assert profit_row["Amount (EUR)"] == "16.0"
    assert profit_row["Balance"] == "120"
    assert profit_row["Balance (EUR)"] == "96.0"
    assert profit_row["Deposit to Date (EUR)"] == "80.0"

    assert withdraw_row["Amount (EUR)"] == "48.0"
    assert withdraw_row["Balance"] == "60.0"
    assert withdraw_row["Balance (EUR)"] == "48.00"
    assert withdraw_row["Deposit to Date (EUR)"] == "80.0"
    assert withdraw_row["Purchase Price (EUR)"] == "40.00000000"
    assert withdraw_row["Sale Price (EUR)"] == "48.00000000"
    assert withdraw_row["Net Profit (EUR)"] == "8.00000000"


def test_multiple_deposits_same_currency(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="40", date="2025-01-01"),
            h.row(tx_type="Deposit", currency="USDC", amount="60", date="2025-01-02"),
            h.row(tx_type="Balance", currency="USDC", amount="110", date="2025-01-03"),
            h.row(tx_type="Withdraw", currency="USDC", amount="55", date="2025-01-04"),
        ],
        rates={"USDC": Decimal("1")},
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("55")
    assert app5.purchase_price_eur == Decimal("50")
    assert app5.wins_eur == Decimal("5")


def test_deposit_with_non_investment_source_is_mapped_as_profit(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="25", date="2025-01-01", source="Compensation"),
        ],
        rates={"USDC": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 1
    assert out_rows[0]["Type"] == "profit"
    assert out_rows[0]["Amount"] == "25"

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("0")
    assert state.native_profit_balance == Decimal("25")
    assert state.eur_deposit_balance == Decimal("0")


def test_deposit_with_investment_source_remains_deposit(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="25", date="2025-01-01", source="Investment"),
        ],
        rates={"USDC": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 1
    assert out_rows[0]["Type"] == "deposit"
    assert out_rows[0]["Amount"] == "25"

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("25")
    assert state.native_profit_balance == Decimal("0")
    assert state.eur_deposit_balance == Decimal("25")


def test_balance_is_snapshot_not_delta(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="130", date="2025-01-10"),
            h.row(tx_type="Balance", currency="USDC", amount="131", date="2025-01-11"),
        ],
        rates={"USDC": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    profit_rows = [row for row in out_rows if row["Type"] == "profit"]
    assert profit_rows[0]["Amount"] == "30"
    assert profit_rows[1]["Amount"] == "1"


def test_balance_can_generate_loss_delta(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="90", date="2025-01-10"),
        ],
        rates={"USDC": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    profit_row = [row for row in out_rows if row["Type"] == "profit"][0]
    assert profit_row["Amount"] == "-10"
    assert result.summary.appendix_5.rows == 0


def test_partial_then_full_withdrawal_updates_state_proportionally(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="120", date="2025-01-10"),
            h.row(tx_type="Withdraw", currency="USDC", amount="30", date="2025-01-20"),
            h.row(tx_type="Withdraw", currency="USDC", amount="90", date="2025-01-21"),
        ],
        rates={"USDC": Decimal("1")},
    )

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("0")
    assert state.native_profit_balance == Decimal("0")
    assert state.eur_deposit_balance == Decimal("0")


def test_multiple_currencies_are_tracked_independently(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="120", date="2025-01-02"),
            h.row(tx_type="Deposit", currency="ETH", amount="2", date="2025-01-03"),
            h.row(tx_type="Balance", currency="ETH", amount="2.5", date="2025-01-04"),
            h.row(tx_type="Withdraw", currency="ETH", amount="1", date="2025-01-05"),
        ],
        rates={"USDC": Decimal("1"), "ETH": Decimal("2000")},
    )

    usdc_state = result.summary.state_by_currency["USDC"]
    eth_state = result.summary.state_by_currency["ETH"]
    assert usdc_state.native_total_balance == Decimal("120")
    assert eth_state.native_total_balance == Decimal("1.5")


def test_withdraw_tax_rows_only_populated_for_withdraw_entries(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="120", date="2025-01-02"),
            h.row(tx_type="Withdraw", currency="USDC", amount="60", date="2025-01-03"),
        ],
        rates={"USDC": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    deposit_row = [row for row in out_rows if row["Type"] == "deposit"][0]
    profit_row = [row for row in out_rows if row["Type"] == "profit"][0]
    withdraw_row = [row for row in out_rows if row["Type"] == "withdraw"][0]

    assert deposit_row["Net Profit (EUR)"] == ""
    assert profit_row["Net Profit (EUR)"] == ""
    assert withdraw_row["Net Profit (EUR)"] != ""


def test_footer_rows_are_not_treated_as_transactions(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="110", date="2025-01-02"),
            h.row(tx_type="Check", currency="", amount="", date=""),
            h.row(tx_type="", currency="", amount="", date=""),
        ],
        rates={"USDC": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 2
    assert result.summary.unsupported_transaction_rows == 1
    assert result.summary.ignored_rows == 1


def test_state_json_is_written_and_contains_fund_balances(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="ETH", amount="1", date="2025-01-01"),
            h.row(tx_type="Balance", currency="ETH", amount="1.2", date="2025-01-02"),
        ],
        rates={"ETH": Decimal("2000")},
    )

    payload = json.loads(result.year_end_state_json_path.read_text(encoding="utf-8"))
    assert payload["state_tax_year_end"] == 2025
    assert payload["state_by_currency"]["ETH"]["native_deposit_balance"] == "1"
    assert payload["state_by_currency"]["ETH"]["native_profit_balance"] == "0.2"
