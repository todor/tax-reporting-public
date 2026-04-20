from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.crypto.coinbase import report_analyzer as analyzer
from tests.integrations.crypto.coinbase import support as h


def test_preamble_rows_are_skipped(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="0.01",
                subtotal="€100",
                total="€101",
            )
        ],
        rates={"EUR": Decimal("1")},
        preamble_lines=["Account: demo", "Generated: 2025-04-01"],
    )

    assert result.summary.preamble_rows_ignored == 2
    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 1
    assert out_rows[0]["Purchase Price (EUR)"] == ""


def test_convert_notes_parse_failure_fails_clearly(tmp_path: Path) -> None:
    rows = [
        h.row(
            timestamp="2025-01-01 00:00:00 UTC",
            tx_type="Buy",
            asset="ETH",
            qty="2",
            subtotal="€3900",
            total="€4000",
        ),
        h.row(
            timestamp="2025-01-02 00:00:00 UTC",
            tx_type="Convert",
            asset="ETH",
            qty="0",
            subtotal="€2000",
            total="€2000",
            notes="ETH -> BTC",
        ),
    ]

    with pytest.raises(analyzer.CoinbaseAnalyzerError, match="invalid Convert Notes format"):
        _ = h.run(tmp_path, rows=rows, rates={"EUR": Decimal("1")})


def test_receive_missing_cost_basis_is_warning_and_manual_check(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Receive",
                asset="BTC",
                qty="0.1",
                subtotal="",
                total="",
                review_status="CARRY_OVER_BASIS",
                cost_basis_eur="",
            )
        ],
        rates={"EUR": Decimal("1")},
    )
    assert result.summary.unsupported_transaction_rows == 1
    assert result.summary.manual_check_required is True
    assert any("missing Cost Basis (EUR) for Receive" in warning for warning in result.summary.warnings)


def test_receive_invalid_review_status_is_warning_and_manual_check(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Receive",
                asset="BTC",
                qty="0.1",
                subtotal="",
                total="",
                review_status="TAXABLE",
                cost_basis_eur="1000",
            )
        ],
        rates={"EUR": Decimal("1")},
    )
    assert result.summary.unsupported_transaction_rows == 1
    assert result.summary.manual_check_required is True
    assert any("invalid Review Status for Receive" in warning for warning in result.summary.warnings)


def test_receive_non_taxable_does_not_require_cost_basis_and_is_mapped(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Receive",
                asset="BTC",
                qty="0.1",
                subtotal="",
                total="",
                review_status="NON-TAXABLE",
                cost_basis_eur="",
            )
        ],
        rates={"EUR": Decimal("1")},
    )
    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 1
    assert out_rows[0]["Transaction Type"] == "Deposit"
    assert out_rows[0]["Review Status"] == "NON-TAXABLE"
    assert result.summary.unsupported_transaction_rows == 0
    assert result.summary.manual_check_required is False
    assert len(result.summary.warnings) == 0


def test_fiat_deposit_and_withdraw_are_ignored(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Deposit",
                asset="EUR",
                qty="100",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Withdraw",
                asset="USD",
                qty="50",
                price_currency="USD",
                subtotal="$50",
                total="$50",
            ),
        ],
        rates={"EUR": Decimal("1"), "USD": Decimal("0.8")},
    )

    assert result.summary.ignored_fiat_deposit_withdraw_rows == 2
    assert result.summary.appendix_5.rows == 0


def test_deposit_is_treated_as_fiat_by_transaction_type(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Deposit",
                asset="BTC",
                qty="0.01",
                subtotal="€100",
                total="€100",
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    assert result.summary.ignored_fiat_deposit_withdraw_rows == 1
    assert result.summary.appendix_5.rows == 0


def test_unknown_transaction_type_is_warning_and_manual_check(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Staking Reward",
                asset="ETH",
                qty="0.1",
                subtotal="€200",
                total="€200",
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    assert result.summary.unsupported_transaction_rows == 1
    assert result.summary.manual_check_required is True
    assert "Staking Reward" in result.summary.unknown_transaction_types


def test_currency_prefixed_values_are_parsed_and_converted(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="0.1",
                price_currency="BGN",
                subtotal="BGN6500",
                total="BGN6579",
            )
        ],
        rates={"BGN": Decimal("0.5"), "EUR": Decimal("1")},
    )

    out_rows = h.read_csv(result.output_csv_path)
    assert out_rows[0]["Proceeds (EUR)"] == "3289.5"
    assert out_rows[0]["Purchase Price (EUR)"] == ""


def test_header_with_leading_id_column_is_supported(tmp_path: Path) -> None:
    row = h.row(
        timestamp="2025-01-01 00:00:00 UTC",
        tx_type="Buy",
        asset="BTC",
        qty="0.01",
        subtotal="€100",
        total="€101",
    )
    row_with_id = {"ID": "abc123", **row}
    header = ["ID", *h.DEFAULT_HEADER]

    result = h.run(
        tmp_path,
        rows=[row_with_id],
        header=header,
        preamble_lines=["Transactions", "User,Todor,uid-1"],
        rates={"EUR": Decimal("1")},
    )

    assert result.summary.preamble_rows_ignored == 2
    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 1
    assert out_rows[0]["Operation ID"] == "coinbase-abc123"
    assert out_rows[0]["Purchase Price (EUR)"] == ""


def test_withdrawal_alias_is_treated_as_withdraw(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Withdrawal",
                asset="EUR",
                qty="-2.5",
                subtotal="€2.50",
                total="€2.35",
            )
        ],
        rates={"EUR": Decimal("1")},
    )

    assert result.summary.ignored_fiat_deposit_withdraw_rows == 1
    assert result.summary.unsupported_transaction_rows == 0


def test_invalid_tax_year_fails(tmp_path: Path) -> None:
    rows = [
        h.row(
            timestamp="2025-01-01 00:00:00 UTC",
            tx_type="Buy",
            asset="BTC",
            qty="0.01",
            subtotal="€100",
            total="€101",
        )
    ]

    with pytest.raises(analyzer.CoinbaseAnalyzerError, match="invalid tax year"):
        _ = h.run(tmp_path, rows=rows, tax_year=1800, rates={"EUR": Decimal("1")})
