from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tests.integrations.ibkr import support as h

IbkrAnalyzerError = h.IbkrAnalyzerError
analyze_ibkr_activity_statement = h.analyze_ibkr_activity_statement
_fx_provider = h._fx_provider
_read_rows = h._read_rows
_run = h._run
_sanity_rows = h._sanity_rows
_trades_header_and_data = h._trades_header_and_data
_write_rows = h._write_rows


def test_sanity_checks_pass_and_debug_artifacts_are_created(tmp_path: Path) -> None:
    result = _run(tmp_path, _sanity_rows(), mode="listed_symbol")
    assert result.summary.sanity_passed is True
    assert result.summary.sanity_failures_count == 0
    assert "_sanity_debug" in result.summary.sanity_debug_artifacts_dir

    debug_csv = Path(result.summary.sanity_debug_csv_path)
    report_json = Path(result.summary.sanity_report_path)
    assert debug_csv.exists()
    assert report_json.exists()

    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["failures_count"] == 0

def test_sanity_basis_sign_mismatch_fails_with_diagnostics(tmp_path: Path) -> None:
    rows = _sanity_rows(trade_basis="-21")
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)
    with pytest.raises(IbkrAnalyzerError, match="SANITY CHECKS FAILED"):
        _ = analyze_ibkr_activity_statement(
            input_csv=input_csv,
            tax_year=2025,
            tax_exempt_mode="listed_symbol",  # type: ignore[arg-type]
            output_dir=tmp_path / "out",
            fx_rate_provider=_fx_provider,
        )

    report_json = tmp_path / "out" / "_sanity_debug" / "ibkr_activity_2025" / "sanity_report.json"
    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert any(item["check_type"] == "BASIS_SIGN_MISMATCH" for item in report["failures"])

    declaration_txt = tmp_path / "out" / "ibkr_activity_declaration_2025.txt"
    text = declaration_txt.read_text(encoding="utf-8")
    assert "Sanity checks: FAIL" in text
    assert "Sanity diagnostics:" in text

def test_sanity_ignores_eur_aggregate_rows_when_non_eur_exists(tmp_path: Path) -> None:
    rows = _sanity_rows()
    rows.insert(7, ["Trades", "SubTotal", "Stocks", "EUR", "BMW", "", "", "", "999", "999", "", "999", "999"])
    rows.append(["Trades", "Total", "Stocks", "EUR", "", "", "", "", "999", "999", "", "999", "999"])
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.sanity_passed is True
    assert result.summary.sanity_failures_count == 0

def test_sanity_checks_real_eur_totals_when_eur_trades_exist(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2"],
        [
            "Trades",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Exchange",
            "Code",
            "Proceeds",
            "Comm/Fee",
            "DataDiscriminator",
            "Basis",
            "Realized P/L",
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "-1", "Trade", "-20", "79"],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-09", "IBIS2", "", "0", "", "ClosedLot", "20", ""],
        ["Trades", "Data", "Stocks", "EUR", "BMW", "2025-02-10, 10:00:00", "IBIS2", "C", "50", "-1", "Trade", "-10", "39"],
        ["Trades", "Data", "Stocks", "EUR", "BMW", "2025-02-09", "IBIS2", "", "0", "", "ClosedLot", "10", ""],
        ["Trades", "SubTotal", "Stocks", "USD", "BMW", "", "", "", "100", "-1", "", "-20", "79"],
        ["Trades", "SubTotal", "Stocks", "EUR", "BMW", "", "", "", "50", "-1", "", "-10", "39"],
        ["Trades", "Total", "Stocks", "USD", "", "", "", "", "100", "-1", "", "-20", "79"],
        ["Trades", "Total", "Stocks", "EUR", "", "", "", "", "50", "-1", "", "-10", "39"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.sanity_passed is True
    assert result.summary.sanity_failures_count == 0
    assert result.summary.sanity_checked_subtotals == 2
    assert result.summary.sanity_checked_totals == 2

def test_sanity_ignores_derived_eur_totals_when_real_eur_total_exists(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2"],
        [
            "Trades",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Exchange",
            "Code",
            "Proceeds",
            "Comm/Fee",
            "DataDiscriminator",
            "Basis",
            "Realized P/L",
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "-1", "Trade", "-20", "79"],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-09", "IBIS2", "", "0", "", "ClosedLot", "20", ""],
        ["Trades", "Data", "Stocks", "EUR", "BMW", "2025-02-10, 10:00:00", "IBIS2", "C", "50", "-1", "Trade", "-10", "39"],
        ["Trades", "Data", "Stocks", "EUR", "BMW", "2025-02-09", "IBIS2", "", "0", "", "ClosedLot", "10", ""],
        ["Trades", "SubTotal", "Stocks", "USD", "BMW", "", "", "", "100", "-1", "", "-20", "79"],
        ["Trades", "SubTotal", "Stocks", "EUR", "BMW", "", "", "", "999", "999", "", "999", "999"],  # derived EUR for USD block
        ["Trades", "SubTotal", "Stocks", "EUR", "BMW", "", "", "", "50", "-1", "", "-10", "39"],    # real EUR block
        ["Trades", "Total", "Stocks", "USD", "", "", "", "", "100", "-1", "", "-20", "79"],
        ["Trades", "Total", "Stocks", "EUR", "", "", "", "", "999", "999", "", "999", "999"],        # derived EUR for USD block
        ["Trades", "Total", "Stocks", "EUR", "", "", "", "", "50", "-1", "", "-10", "39"],           # real EUR block
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.sanity_passed is True
    assert result.summary.sanity_failures_count == 0

def test_output_populates_subtotal_total_eur_columns(tmp_path: Path) -> None:
    rows = _sanity_rows(trade_basis="-20", realized_pl="79")
    result = _run(tmp_path, rows, mode="listed_symbol")
    output_rows = _read_rows(result.output_csv_path)
    header, _data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}

    subtotal_row = next(r for r in output_rows if len(r) > 1 and r[0] == "Trades" and r[1] == "SubTotal")
    total_row = next(r for r in output_rows if len(r) > 1 and r[0] == "Trades" and r[1] == "Total")

    # 100 USD -> 90 EUR, -20 USD Basis -> -18 EUR, -1 USD Fee -> -0.9 EUR, pnl=71.1 EUR
    assert Decimal(subtotal_row[2 + idx["Proceeds (EUR)"]]) == Decimal("90.00000000")
    assert Decimal(subtotal_row[2 + idx["Basis (EUR)"]]) == Decimal("-18.00000000")
    assert Decimal(subtotal_row[2 + idx["Comm/Fee (EUR)"]]) == Decimal("-0.90000000")
    assert Decimal(subtotal_row[2 + idx["Sale Price (EUR)"]]) == Decimal("89.10000000")
    assert Decimal(subtotal_row[2 + idx["Purchase Price (EUR)"]]) == Decimal("18.00000000")
    assert Decimal(subtotal_row[2 + idx["Realized P/L (EUR)"]]) == Decimal("71.10000000")
    assert Decimal(subtotal_row[2 + idx["Realized P/L Wins (EUR)"]]) == Decimal("71.10000000")
    assert Decimal(subtotal_row[2 + idx["Realized P/L Losses (EUR)"]]) == Decimal("0.00000000")

    assert Decimal(total_row[2 + idx["Proceeds (EUR)"]]) == Decimal("90.00000000")
    assert Decimal(total_row[2 + idx["Basis (EUR)"]]) == Decimal("-18.00000000")
    assert Decimal(total_row[2 + idx["Comm/Fee (EUR)"]]) == Decimal("-0.90000000")
    assert Decimal(total_row[2 + idx["Sale Price (EUR)"]]) == Decimal("89.10000000")
    assert Decimal(total_row[2 + idx["Purchase Price (EUR)"]]) == Decimal("18.00000000")
    assert Decimal(total_row[2 + idx["Realized P/L (EUR)"]]) == Decimal("71.10000000")
    assert Decimal(total_row[2 + idx["Realized P/L Wins (EUR)"]]) == Decimal("71.10000000")
    assert Decimal(total_row[2 + idx["Realized P/L Losses (EUR)"]]) == Decimal("0.00000000")

def test_sale_purchase_price_columns_empty_for_open_and_closedlot(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2"],
        [
            "Trades",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Exchange",
            "Code",
            "Proceeds",
            "Comm/Fee",
            "DataDiscriminator",
            "Basis",
            "Realized P/L",
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-01, 10:00:00", "IBIS2", "O", "50", "-1", "Trade", "-10", "0"],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-02, 10:00:00", "IBIS2", "C", "100", "-1", "Trade", "-20", "79"],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-02", "IBIS2", "", "0", "", "ClosedLot", "20", ""],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}

    open_trade = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade" and r[2 + idx["Code"]] == "O")
    closed_lot = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "ClosedLot")

    assert open_trade[2 + idx["Sale Price (EUR)"]] == ""
    assert open_trade[2 + idx["Purchase Price (EUR)"]] == ""
    assert closed_lot[2 + idx["Sale Price (EUR)"]] == ""
    assert closed_lot[2 + idx["Purchase Price (EUR)"]] == ""
