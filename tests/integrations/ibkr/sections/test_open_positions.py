from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tests.integrations.ibkr import support as h

IbkrAnalyzerError = h.IbkrAnalyzerError
_open_positions_header_and_data = h._open_positions_header_and_data
_read_rows = h._read_rows
_rows_for_appendix8_part1 = h._rows_for_appendix8_part1
_rows_for_open_position_check = h._rows_for_open_position_check
_run = h._run


def test_open_position_reconciliation_happy_path(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "7")],
        trade_rows=[("4GLDd", "10"), ("4GLDd", "-3")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows == 0
    assert all(
        "OPEN_POSITION_TRADE_QTY_MISMATCH" not in warning
        and "OPEN_POSITION_UNMATCHED_INSTRUMENT" not in warning
        and "TRADE_UNMATCHED_INSTRUMENT" not in warning
        for warning in result.summary.warnings
    )

def test_open_position_reconciliation_alias_symbols_are_matched(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "5")],
        trade_rows=[("4GLDd", "5")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows == 0
    assert not any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)

def test_open_position_reconciliation_quantity_mismatch_triggers_review(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "6")],
        trade_rows=[("4GLDd", "7")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows >= 1
    assert any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text
    assert "Конкретни действия:" in text
    assert "Проверете Open Positions" in text


def test_open_position_reconciliation_uses_mtm_prior_quantity(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "34.8884")],
        trade_rows=[("4GLDd", "5.5539")],
    )
    rows.extend(
        [
            [
                "Mark-to-Market Performance Summary",
                "Header",
                "Asset Category",
                "Symbol",
                "Prior Quantity",
                "Current Quantity",
                "Mark-to-Market P/L Total",
            ],
            [
                "Mark-to-Market Performance Summary",
                "Data",
                "Stocks",
                "4GLD",
                "29.3345",
                "34.8884",
                "0",
            ],
        ]
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows == 0
    assert not any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_nets_same_isin_transfers(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch", "Description", "ISIN"],
        ["Financial Instrument Information", "Data", "Stocks", "AMZN", "NASDAQ", "Amazon", "US0231351067"],
        ["Financial Instrument Information", "Data", "Stocks", "AMZ", "IBIS", "Amazon", "US0231351067"],
        ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Symbol", "Prior Quantity", "Current Quantity", "Mark-to-Market P/L Total"],
        ["Mark-to-Market Performance Summary", "Data", "Stocks", "AMZN", "16", "0", "0"],
        ["Mark-to-Market Performance Summary", "Data", "Stocks", "AMZ", "0", "0", "0"],
        ["Trades", "Header", "Asset Category", "Currency", "Symbol", "Date/Time", "Exchange", "Code", "Proceeds", "Quantity", "DataDiscriminator", "Basis"],
        ["Trades", "Data", "Stocks", "EUR", "AMZ", "2026-05-28, 10:43:51", "IBIS", "C", "0", "-16", "Order", ""],
        ["Trades", "Data", "Stocks", "EUR", "AMZ", "2026-05-28, 10:43:51", "IBIS", "C", "0", "-16", "Trade", ""],
        ["Trades", "Data", "Stocks", "EUR", "AMZ", "2025-01-01", "", "", "", "16", "ClosedLot", "100"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Currency", "Summary Quantity", "Cost Basis", "DataDiscriminator"],
        ["Open Positions", "Data", "Stocks", "AMZ", "EUR", "0", "0", "Summary"],
        ["Transfers", "Header", "Asset Category", "Symbol", "Direction", "Qty", "Code"],
        ["Transfers", "Data", "Stocks", "AMZ", "In", "16", ""],
        ["Transfers", "Data", "Stocks", "AMZ", "In", "-16", "Ca"],
        ["Transfers", "Data", "Stocks", "AMZN", "In", "-16", ""],
    ]

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.review_required_rows == 0
    assert not any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_uses_recognized_merger_corporate_actions(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description", "Conid", "Security ID", "Underlying", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "CMBT", "CMB TECH NV", "1", "BE0003816338", "CMBT", "NYSE"],
        ["Financial Instrument Information", "Data", "Stocks", "GOGL", "GOLDEN OCEAN GROUP LTD", "2", "BMG396372051", "GOGL", "NASDAQ"],
        ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Symbol", "Prior Quantity", "Current Quantity", "Mark-to-Market P/L Total"],
        ["Mark-to-Market Performance Summary", "Data", "Stocks", "CMBT", "0", "0", "0"],
        ["Mark-to-Market Performance Summary", "Data", "Stocks", "GOGL", "0", "0", "0"],
        ["Trades", "Header", "Asset Category", "Currency", "Symbol", "Date/Time", "Exchange", "Code", "Proceeds", "Quantity", "DataDiscriminator", "Basis"],
        ["Trades", "Data", "Stocks", "USD", "CMBT", "2025-11-07, 10:27:53", "NYSE", "", "30216.50", "-3217.65", "Order", ""],
        ["Trades", "Data", "Stocks", "USD", "CMBT", "2025-11-07, 10:27:53", "NYSE", "", "30216.50", "-3217.65", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "GOGL", "2025-02-05, 10:39:24", "DARK", "O", "-31807.61", "3387", "Order", ""],
        ["Trades", "Data", "Stocks", "USD", "GOGL", "2025-02-05, 10:39:24", "DARK", "O", "-31807.61", "3387", "Trade", ""],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Currency", "Summary Quantity", "Cost Basis", "DataDiscriminator"],
        ["Open Positions", "Data", "Stocks", "CMBT", "USD", "0", "0", "Summary"],
        ["Open Positions", "Data", "Stocks", "GOGL", "USD", "0", "0", "Summary"],
        ["Corporate Actions", "Header", "Asset Category", "Currency", "Report Date", "Date/Time", "Description", "Quantity", "Proceeds", "Value", "Realized P/L"],
        [
            "Corporate Actions",
            "Data",
            "Stocks",
            "USD",
            "2025-08-20",
            "2025-08-19, 20:25:00",
            "GOGL(BMG396372051) Merged(Acquisition) WITH BE0003816338 19 for 20 (CMBT, CMB TECH NV, BE0003816338)",
            "3217.65",
            "0",
            "25869.906",
            "0",
        ],
        [
            "Corporate Actions",
            "Data",
            "Stocks",
            "USD",
            "2025-08-20",
            "2025-08-19, 20:25:00",
            "GOGL(BMG396372051) Merged(Acquisition) WITH BE0003816338 19 for 20 (GOGL, GOLDEN OCEAN GROUP LTD, BMG396372051)",
            "-3387",
            "0",
            "-27028.26",
            "0",
        ],
    ]

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.corporate_actions_recognized_rows == 2
    assert result.summary.corporate_actions_unsupported_rows == 0
    assert result.summary.review_required_rows == 0
    assert not any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_unmatched_open_position_symbol_triggers_review(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("UNKNOWN", "5")],
        trade_rows=[("4GLDd", "5")],
    )
    with pytest.raises(IbkrAnalyzerError, match="cannot be matched to Financial Instrument"):
        _ = _run(tmp_path, rows, mode="listing_exchange")

def test_open_position_reconciliation_unmatched_trade_symbol_triggers_review(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "5")],
        trade_rows=[("UNKNOWN", "5")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows >= 1
    assert any("TRADE_UNMATCHED_INSTRUMENT" in warning for warning in result.summary.warnings)

def test_open_position_reconciliation_accepts_comma_formatted_quantities(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "1,001")],
        trade_rows=[("4GLDd", "1,001")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows == 0
    assert not any("invalid order quantity" in warning for warning in result.summary.warnings)

def test_open_position_reconciliation_treats_empty_quantity_as_zero(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "")],
        trade_rows=[("4GLDd", "")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows == 0
    assert not any("invalid order quantity" in warning for warning in result.summary.warnings)

def test_appendix8_part1_single_country_aggregation_and_declarative_note(tmp_path: Path) -> None:
    rows = _rows_for_appendix8_part1(
        open_rows=[
            ("AAA", "USD", "2", "100"),
            ("BBB", "USD", "3", "200"),
        ],
        instrument_rows=[
            ("AAA", "US1111111111"),
            ("BBB", "US2222222222"),
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.open_positions_summary_rows == 2
    assert result.summary.open_positions_part1_rows == 1
    row = result.summary.appendix_8_part1_rows[0]
    assert row.country_iso == "US"
    assert row.cost_basis_original_currency == "USD"
    assert row.quantity == Decimal("5")
    assert row.acquisition_date.isoformat() == "2025-12-31"
    assert row.cost_basis_original == Decimal("300")
    assert row.cost_basis_eur == Decimal("270")

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Част І, Акции" in text
    assert "- ред 1.1" in text
    assert "Държава: САЩ" in text
    assert "Брой: 5" in text
    assert "Дата и година на придобиване: 31.12.2025" in text
    assert "Обща цена на придобиване в съответната валута: 300.00 USD" in text
    assert "В EUR: 270.00 EUR" in text
    assert "Напомняне: Към Приложение 8, Част I следва да се приложи файл с open positions." not in text
    assert "Забележка:" in text
    assert "Данните в Приложение 8, Част I са декларативни." in text
    assert "Не се изисква прикачване на файл към декларацията." in text
    assert "Запазете отчети (напр. broker statements) за целите на евентуална проверка от НАП." in text


def test_appendix8_part1_groups_same_country_by_currency(tmp_path: Path) -> None:
    rows = _rows_for_appendix8_part1(
        open_rows=[
            ("AAA", "USD", "2", "100"),
            ("BBB", "EUR", "3", "200"),
        ],
        instrument_rows=[
            ("AAA", "US1111111111"),
            ("BBB", "US2222222222"),
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.open_positions_part1_rows == 2

    by_currency = {
        row.cost_basis_original_currency: row
        for row in result.summary.appendix_8_part1_rows
    }
    assert set(by_currency) == {"USD", "EUR"}
    assert by_currency["USD"].country_iso == "US"
    assert by_currency["USD"].quantity == Decimal("2")
    assert by_currency["USD"].cost_basis_original == Decimal("100")
    assert by_currency["USD"].cost_basis_eur == Decimal("90")

    assert by_currency["EUR"].country_iso == "US"
    assert by_currency["EUR"].quantity == Decimal("3")
    assert by_currency["EUR"].cost_basis_original == Decimal("200")
    assert by_currency["EUR"].cost_basis_eur == Decimal("200")

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Обща цена на придобиване в съответната валута: 100.00 USD" in text
    assert "Обща цена на придобиване в съответната валута: 200.00 EUR" in text

def test_appendix8_part1_multiple_countries_and_country_extraction_from_isin(tmp_path: Path) -> None:
    rows = _rows_for_appendix8_part1(
        open_rows=[
            ("AAA", "USD", "1", "100"),
            ("CCC", "CHF", "2", "50"),
        ],
        instrument_rows=[
            ("AAA", "US1111111111"),
            ("CCC", "LU3333333333"),
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    by_country = {item.country_iso: item for item in result.summary.appendix_8_part1_rows}
    assert set(by_country) == {"US", "LU"}
    assert by_country["US"].country_bulgarian == "САЩ"
    assert by_country["US"].cost_basis_eur == Decimal("90")
    assert by_country["LU"].country_bulgarian == "Люксембург"
    assert by_country["LU"].cost_basis_eur == Decimal("55")

def test_open_positions_csv_enrichment_with_country_and_cost_basis_eur(tmp_path: Path) -> None:
    rows = _rows_for_appendix8_part1(
        open_rows=[
            ("AAA", "USD", "2", "100"),
            ("CCC", "EUR", "1", "10"),
        ],
        instrument_rows=[
            ("AAA", "US1111111111"),
            ("CCC", "IE4444444444"),
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _open_positions_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    assert header[2:].count("Country") == 1
    assert header[2:].count("Cost Basis (EUR)") == 1

    aaa = next(r for r in data_rows if r[2 + idx["Symbol"]] == "AAA")
    ccc = next(r for r in data_rows if r[2 + idx["Symbol"]] == "CCC")
    assert aaa[2 + idx["Country"]] == "United States"
    assert aaa[2 + idx["Cost Basis (EUR)"]] == "90.00000000"
    assert ccc[2 + idx["Country"]] == "Ireland"
    assert ccc[2 + idx["Cost Basis (EUR)"]] == "10.00000000"

def test_appendix8_part1_parses_summary_layout_and_security_id_header(tmp_path: Path) -> None:
    rows: list[list[str]] = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        [
            "Financial Instrument Information",
            "Header",
            "Asset Category",
            "Symbol",
            "Listing Exch",
            "Description",
            "Security ID",
        ],
        [
            "Financial Instrument Information",
            "Data",
            "Stocks",
            "XYZ",
            "NYSE",
            "XYZ Corp",
            "US5555555555",
        ],
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
            "DataDiscriminator",
            "Basis",
            "Quantity",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "XYZ",
            "2025-01-10, 10:00:00",
            "NYSE",
            "",
            "0",
            "Order",
            "",
            "4",
        ],
        [
            "Open Positions",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Summary Quantity",
            "Cost Basis",
        ],
        [
            "Open Positions",
            "Data",
            "Summary",
            "Stocks",
            "USD",
            "XYZ",
            "4",
            "120",
        ],
    ]
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.open_positions_part1_rows == 1
    row = result.summary.appendix_8_part1_rows[0]
    assert row.country_iso == "US"
    assert row.cost_basis_eur == Decimal("108")

def test_appendix8_part1_includes_treasury_bills_summary_rows(tmp_path: Path) -> None:
    rows: list[list[str]] = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        [
            "Financial Instrument Information",
            "Header",
            "Asset Category",
            "Symbol",
            "Listing Exch",
            "Description",
            "ISIN",
        ],
        [
            "Financial Instrument Information",
            "Data",
            "Treasury Bills",
            "912797TB3",
            "NYSE",
            "United States Treasury B",
            "US912797TB31",
        ],
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
            "DataDiscriminator",
            "Basis",
            "Quantity",
        ],
        [
            "Trades",
            "Data",
            "Treasury Bills",
            "USD",
            "912797TB3 - United States Treasury B 03/31/26",
            "2025-01-10, 10:00:00",
            "NYSE",
            "",
            "0",
            "Order",
            "",
            "1",
        ],
        [
            "Open Positions",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Summary Quantity",
            "Cost Basis",
        ],
        [
            "Open Positions",
            "Data",
            "Summary",
            "Treasury Bills",
            "USD",
            "912797TB3 - United States Treasury B 03/31/26",
            "1",
            "28000",
        ],
    ]
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.open_positions_part1_rows == 1
    row = result.summary.appendix_8_part1_rows[0]
    assert row.country_iso == "US"
    assert row.quantity == Decimal("1")
    assert row.cost_basis_original == Decimal("28000")
    assert row.cost_basis_eur == Decimal("25200")

def test_appendix8_part1_treasury_bills_verbose_fii_symbol_still_matches(tmp_path: Path) -> None:
    rows: list[list[str]] = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        [
            "Financial Instrument Information",
            "Header",
            "Asset Category",
            "Symbol",
            "Listing Exch",
            "Description",
            "ISIN",
        ],
        [
            "Financial Instrument Information",
            "Data",
            "Treasury Bills",
            "912797TB3 - United States Treasury B 03/31/26",
            "NYSE",
            "United States Treasury B",
            "US912797TB31",
        ],
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
            "DataDiscriminator",
            "Basis",
            "Quantity",
        ],
        [
            "Trades",
            "Data",
            "Treasury Bills",
            "USD",
            "912797TB3 - United States Treasury B 03/31/26",
            "2025-01-10, 10:00:00",
            "NYSE",
            "",
            "0",
            "Order",
            "",
            "1",
        ],
        [
            "Open Positions",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Summary Quantity",
            "Cost Basis",
        ],
        [
            "Open Positions",
            "Data",
            "Summary",
            "Treasury Bills",
            "USD",
            "912797TB3 - United States Treasury B 03/31/26",
            "1",
            "28000",
        ],
    ]
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.open_positions_part1_rows == 1
    row = result.summary.appendix_8_part1_rows[0]
    assert row.country_iso == "US"
    assert row.quantity == Decimal("1")
    assert row.cost_basis_eur == Decimal("25200")

def test_appendix8_part1_open_positions_unsupported_asset_triggers_manual_review(tmp_path: Path) -> None:
    rows = _rows_for_appendix8_part1(
        open_rows=[
            ("AAA", "USD", "2", "100"),
        ],
        instrument_rows=[
            ("AAA", "US1111111111"),
        ],
    )
    for row in rows:
        if len(row) >= 2 and row[0] == "Open Positions" and row[1] == "Data":
            row[2] = "Options"
        if len(row) >= 2 and row[0] == "Trades" and row[1] == "Data":
            row[2] = "Options"

    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows >= 1
    assert any(
        "OPEN_POSITION_UNSUPPORTED_ASSET" in warning
        for warning in result.summary.warnings
    )
