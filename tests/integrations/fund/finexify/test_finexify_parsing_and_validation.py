from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.fund.finexify.finexify_parser import load_finexify_csv
from integrations.fund.finexify.finexify_to_ir import load_and_map_finexify_csv_to_ir
from integrations.fund.finexify.models import CsvValidationError, FinexifyAnalyzerError
from integrations.fund.shared.fund_ir_models import FundAnalysisSummary
from tests.integrations.fund.finexify import support as h


def test_parser_loads_with_preamble(tmp_path: Path) -> None:
    csv_path = tmp_path / "finexify.csv"
    h.write_finexify_csv(
        csv_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
        ],
        preamble_lines=["Some header noise", "Another line"],
    )

    loaded = load_finexify_csv(csv_path)

    assert loaded.preamble_rows_ignored == 2
    assert len(loaded.rows) == 1
    assert loaded.rows[0].raw["Type"] == "Deposit"


def test_parser_fails_on_missing_required_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "broken.csv"
    h.write_finexify_csv(
        csv_path,
        rows=[{"Type": "Deposit", "Cryptocurrency": "USDC", "Amount": "100"}],
        header=["Type", "Cryptocurrency", "Amount"],
    )

    with pytest.raises(CsvValidationError, match="missing required columns"):
        load_finexify_csv(csv_path)


def test_invalid_date_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(FinexifyAnalyzerError, match="invalid Date format"):
        h.run(
            tmp_path,
            rows=[
                h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-21-01"),
            ],
            rates={"USDC": Decimal("1")},
        )


def test_unsupported_type_adds_warning_and_manual_check_required(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Total profit", currency="", amount="", date=""),
            h.row(tx_type="", currency="", amount="", date=""),
        ],
        rates={"USDC": Decimal("1")},
    )

    assert result.summary.unsupported_transaction_rows == 1
    assert result.summary.ignored_rows == 1
    assert result.summary.manual_check_required is True
    assert any("unsupported Type" in warning for warning in result.summary.warnings)


def test_same_day_date_only_and_timestamp_rows_keep_original_order(tmp_path: Path) -> None:
    summary = FundAnalysisSummary()
    input_csv = tmp_path / "input.csv"
    h.write_finexify_csv(
        input_csv,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-12-01"),
            h.row(tx_type="Withdraw", currency="USDC", amount="110", date="2025-12-01T07:52:21.879Z"),
            h.row(tx_type="Balance", currency="USDC", amount="110", date="2025-12-01"),
        ],
    )

    with pytest.raises(FinexifyAnalyzerError, match="Withdraw exceeds current balance"):
        load_and_map_finexify_csv_to_ir(
            input_csv=str(input_csv),
            summary=summary,
        )


def test_descending_input_is_reversed_to_ascending(tmp_path: Path) -> None:
    summary = FundAnalysisSummary()
    input_csv = tmp_path / "descending.csv"
    h.write_finexify_csv(
        input_csv,
        rows=[
            h.row(tx_type="Withdraw", currency="USDC", amount="110", date="2025-12-02"),
            h.row(tx_type="Balance", currency="USDC", amount="110", date="2025-12-01"),
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-11-30"),
        ],
    )

    mapping = load_and_map_finexify_csv_to_ir(
        input_csv=str(input_csv),
        summary=summary,
    )

    assert [row.transaction_type for row in mapping.ir_rows] == ["deposit", "profit", "withdraw"]


def test_withdraw_more_than_available_fails(tmp_path: Path) -> None:
    with pytest.raises(FinexifyAnalyzerError, match="Withdraw exceeds current balance"):
        h.run(
            tmp_path,
            rows=[
                h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
                h.row(tx_type="Withdraw", currency="USDC", amount="120", date="2025-01-02"),
            ],
            rates={"USDC": Decimal("1")},
        )
