from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tests.integrations.ibkr import support as h

EXCHANGE_CLASS_EU_NON_REGULATED = h.EXCHANGE_CLASS_EU_NON_REGULATED
EXCHANGE_CLASS_EU_REGULATED = h.EXCHANGE_CLASS_EU_REGULATED
EXCHANGE_CLASS_UNKNOWN = h.EXCHANGE_CLASS_UNKNOWN
IbkrAnalyzerError = h.IbkrAnalyzerError
_base_rows = h._base_rows
_classify_exchange = h._classify_exchange
_normalize_exchange = h._normalize_exchange
_read_rows = h._read_rows
_rows_with_review_status = h._rows_with_review_status
_run = h._run
_trades_header_and_data = h._trades_header_and_data
_treasury_rows = h._treasury_rows
_write_rows = h._write_rows
_fx_provider = h._fx_provider


def test_exchange_normalization_aliases() -> None:
    assert _normalize_exchange(" ise ") == "ENEXT.IR"
    assert _normalize_exchange("BME") == "SIBE"
    assert _normalize_exchange("BM") == "SIBE"
    assert _normalize_exchange("EUIBSI") == "EUIBSI"
    assert _normalize_exchange("EUIBSILP") == "EUIBSI"
    assert _classify_exchange("ISE") == EXCHANGE_CLASS_EU_REGULATED
    assert _classify_exchange("EUIBSILP") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange("GETTEX2") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange("UNKNOWNX") == EXCHANGE_CLASS_UNKNOWN

def test_financial_instrument_parsing_supports_stocks_and_treasury_bills(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][2] = "Treasury Bills"
    rows[7][4] = "BGTB"
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 1
    assert result.summary.appendix_5.rows == 1

def test_financial_instrument_symbol_aliases_are_supported(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[3] = ["Financial Instrument Information", "Data", "Stocks", "4GLD, 4GLDd", "IBIS2"]
    rows[7] = ["Trades", "Data", "Stocks", "USD", "4GLDD", "2025-01-10, 10:00:00", "IBIS2", "C;O", "100", "Trade", ""]
    rows[8] = ["Trades", "Data", "Stocks", "USD", "4GLDD", "2024-12-20", "IBIS2", "", "0", "ClosedLot", "30"]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows >= 1

def test_treasury_bills_exact_symbol_match(tmp_path: Path) -> None:
    rows = _treasury_rows(trade_symbol="912797NP8")
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 1

    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade_row = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert trade_row[2 + idx["Normalized Symbol"]] == ""
    assert trade_row[2 + idx["Listing Exchange"]] == "IBIS2"

def test_treasury_bills_extracted_identifier_match(tmp_path: Path) -> None:
    rows = _treasury_rows(trade_symbol="United States Treasury B 06/05/25<br/>912797NP8 4.28601533%")
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 1

    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade_row = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert trade_row[2 + idx["Normalized Symbol"]] == "912797NP8"
    assert trade_row[2 + idx["Listing Exchange"]] == "IBIS2"
    assert trade_row[2 + idx["Appendix Target"]] == "APPENDIX_13"

def test_treasury_bills_multiple_identifier_candidates_mark_review(tmp_path: Path) -> None:
    rows = _treasury_rows(trade_symbol="TBill AAA111BBB and 912797NP8")
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_5.rows == 1
    assert any("multiple 9-char identifier candidates" in warning for warning in result.summary.warnings)

    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade_row = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert trade_row[2 + idx["Review Required"]] == "YES"
    assert "multiple 9-char identifier candidates" in trade_row[2 + idx["Tax Treatment Reason"]]

def test_treasury_bills_no_identifier_candidates_mark_review(tmp_path: Path) -> None:
    rows = _treasury_rows(trade_symbol="United States Treasury Bill June 2025")
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_5.rows == 1
    assert any("no 9-char identifier candidate" in warning for warning in result.summary.warnings)

    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade_row = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert trade_row[2 + idx["Review Required"]] == "YES"
    assert "no 9-char identifier candidate" in trade_row[2 + idx["Tax Treatment Reason"]]

def test_treasury_bills_extracted_identifier_uses_financial_instrument_mapping(tmp_path: Path) -> None:
    rows = _treasury_rows(
        trade_symbol="United States Treasury B 06/05/25<br/>912797NP8 4.28601533%",
        listing_exchange="NASDAQ",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0

def test_listing_mode_eu_vs_non_eu_classification(tmp_path: Path) -> None:
    result = _run(tmp_path, _base_rows(), mode="listed_symbol")
    assert result.summary.appendix_13.rows == 1  # BMW (IBIS2)
    assert result.summary.appendix_5.rows == 1  # TSLA (NASDAQ)

def test_execution_mode_review_for_non_regulated_or_unknown(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUDARK"  # BMW, EU-listed + non-regulated execution -> review
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review_rows == 1
    assert result.summary.appendix_13.rows == 0
    assert result.summary.review.rows == 1

def test_trade_filtering_only_code_with_closing_token(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.insert(
        11,
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2025-02-12, 10:00:00",
            "IBIS2",
            "O",
            "5",
            "Trade",
            "",
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.ignored_non_closing_trade_rows == 1

def test_order_discriminator_is_ignored_even_if_code_contains_c(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.insert(
        11,
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2025-02-12, 10:00:00",
            "IBIS2",
            "C",
            "5",
            "Order",
            "",
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.ignored_non_closing_trade_rows >= 1
    assert result.summary.appendix_13.rows == 1

def test_trades_multiple_headers_use_correct_active_mapping(tmp_path: Path) -> None:
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
            "DataDiscriminator",
            "Basis",
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-20", "IBIS2", "", "0", "ClosedLot", "30"],
        [
            "Trades",
            "Header",
            "Symbol",
            "Asset Category",
            "Date/Time",
            "Currency",
            "Exchange",
            "DataDiscriminator",
            "Code",
            "Basis",
            "Proceeds",
        ],
        ["Trades", "Data", "BMW", "Stocks", "2025-02-10, 10:00:00", "USD", "IBIS2", "Trade", "C", "", "200"],
        ["Trades", "Data", "BMW", "Stocks", "2025-01-20", "USD", "IBIS2", "ClosedLot", "", "50", "0"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 2
    assert result.summary.appendix_13.wins_eur == Decimal("198")

def test_forex_trades_header_without_basis_is_accepted(tmp_path: Path) -> None:
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
            "DataDiscriminator",
        ],
        ["Trades", "Data", "Forex", "USD", "EUR.USD", "2025-01-05, 10:00:00", "IDEALPRO", "C", "10", "Trade"],
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
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-20", "IBIS2", "", "0", "ClosedLot", "30"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.forex_ignored_rows == 1
    assert result.summary.appendix_13.rows == 1

def test_closedlot_grouping_stops_on_next_trade(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.insert(
        9,
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2025-01-11, 09:00:00",
            "IBIS2",
            "C",
            "-50",
            "Trade",
            "",
        ],
    )
    rows.insert(
        10,
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2025-01-09",
            "IBIS2",
            "",
            "0",
            "ClosedLot",
            "10",
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 2

def test_financial_instrument_multiple_headers_use_correct_mapping(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "NASDAQ"],
        ["Financial Instrument Information", "Header", "Listing Exch", "Symbol", "Asset Category"],
        ["Financial Instrument Information", "Data", "IBIS2", "TSLA", "Stocks"],
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
        ],
        ["Trades", "Data", "Stocks", "USD", "TSLA", "2025-02-10, 12:00:00", "IBIS2", "C", "120", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "TSLA", "2025-02-09", "IBIS2", "", "0", "ClosedLot", "20"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 1
    assert result.summary.appendix_5.rows == 0

def test_prior_year_closedlot_is_used_for_basis(tmp_path: Path) -> None:
    result = _run(tmp_path, _base_rows(), mode="listed_symbol")
    # BMW: proceeds=100*0.9=90, basis=30*0.9=27, pnl=63
    assert result.summary.appendix_13.wins_eur == Decimal("63")

def test_fx_logic_eur_rate_identity(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][3] = "EUR"
    rows[8][3] = "EUR"
    rows[7][8] = "100"
    rows[8][10] = "40"
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.wins_eur == Decimal("60")

def test_pnl_formula_for_positive_and_negative_proceeds(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][8] = "-50"  # proceeds EUR=-45
    rows[8][10] = "-20"  # basis EUR=-18 (signed IBKR basis for short close)
    result = _run(tmp_path, rows, mode="listed_symbol")
    # pnl = proceeds - basis = -27
    assert result.summary.appendix_13.losses_eur == Decimal("27")

def test_review_status_taxable_routes_row_to_appendix_5(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="IBIS2",
        review_status="TAXABLE",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0
    assert result.summary.review_status_overrides_rows == 1

    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade_row = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert trade_row[2 + idx["Appendix Target"]] == "APPENDIX_5"
    assert trade_row[2 + idx["Tax Treatment Reason"]] == "Review Status override: TAXABLE"
    assert trade_row[2 + idx["Review Required"]] == "NO"

def test_review_status_non_taxable_routes_row_to_appendix_13(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NASDAQ",
        execution_exchange="NASDAQ",
        review_status="NON-TAXABLE",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_5.rows == 0
    assert result.summary.appendix_13.rows == 1
    assert result.summary.review_status_overrides_rows == 1

def test_empty_review_status_uses_existing_mode_logic(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="EUDARK",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review_rows == 1
    assert result.summary.review.rows == 1
    assert result.summary.review_status_overrides_rows == 0
    assert result.summary.unknown_review_status_rows == 0

def test_unknown_review_status_is_reported(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="IBIS2",
        review_status="MAYBE",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows == 1
    assert result.summary.unknown_review_status_rows == 1
    assert "MAYBE" in result.summary.unknown_review_status_values
    assert any("unknown Review Status=MAYBE" in warning for warning in result.summary.warnings)

    output_rows = _read_rows(result.output_csv_path)
    header, data_rows = _trades_header_and_data(output_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade_row = next(r for r in data_rows if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert trade_row[2 + idx["Review Required"]] == "YES"
    assert "Unknown Review Status value" in trade_row[2 + idx["Review Notes"]]

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "непознат Review Status" in text

def test_csv_integrity_non_trades_unchanged_and_trades_consistent(tmp_path: Path) -> None:
    input_rows = _base_rows()
    result = _run(tmp_path, input_rows, mode="listed_symbol")
    output_rows = _read_rows(result.output_csv_path)

    for in_row, out_row in zip(input_rows, output_rows):
        if len(in_row) >= 1 and in_row[0] != "Trades":
            assert in_row == out_row

    trades_header, trades_data = _trades_header_and_data(output_rows)
    expected_len = len(trades_header)
    assert all(len(row) == expected_len for row in trades_data)

def test_no_closedlot_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.pop(8)
    with pytest.raises(IbkrAnalyzerError, match="no ClosedLot rows attached"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_unsupported_asset_category_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][2] = "Options"
    with pytest.raises(IbkrAnalyzerError, match="Unsupported Asset Category encountered"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_trades_data_before_header_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    header = rows.pop(6)
    rows.insert(11, header)
    with pytest.raises(IbkrAnalyzerError, match="Trades row encountered before Trades Header"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_financial_instrument_data_before_header_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    header = rows.pop(2)
    rows.insert(5, header)
    with pytest.raises(IbkrAnalyzerError, match="Financial Instrument Information Data row encountered before Financial Instrument Information Header"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_trades_missing_required_column_in_active_header_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[6] = [
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
    ]  # Basis missing
    with pytest.raises(IbkrAnalyzerError, match="missing required column"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_financial_instrument_missing_required_column_in_active_header_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[2] = ["Financial Instrument Information", "Header", "Asset Category", "Symbol"]  # Listing Exch missing
    with pytest.raises(IbkrAnalyzerError, match="missing required column"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_missing_symbol_mapping_review_not_silent(tmp_path: Path) -> None:
    rows = _base_rows()
    rows = [row for row in rows if not (row[0] == "Financial Instrument Information" and len(row) > 3 and row[3] == "TSLA")]
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review_rows == 1
    assert any("Missing symbol mapping" in item for item in result.summary.warnings)

def test_conflicting_symbol_mapping_fails_when_classification_differs(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.insert(4, ["Financial Instrument Information", "Data", "Stocks", "BMW", "NASDAQ"])
    with pytest.raises(IbkrAnalyzerError, match="conflicting symbol mapping"):
        _ = _run(tmp_path, rows, mode="listed_symbol")

def test_conflicting_symbol_mapping_allowed_when_same_classification(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.insert(4, ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS"])
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.rows >= 1

def test_realistic_fixture_with_requested_exchanges(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2"],
        ["Financial Instrument Information", "Data", "Stocks", "AIR", "ISE"],
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
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "EUDARK", "C", "100", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-10", "EUDARK", "", "0", "ClosedLot", "20"],
        ["Trades", "Data", "Stocks", "USD", "AIR", "2025-01-11, 11:00:00", "EUIBSI", "C", "50", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "AIR", "2024-12-09", "EUIBSI", "", "0", "ClosedLot", "10"],
        ["Trades", "Data", "Stocks", "USD", "AIR", "2025-01-12, 12:00:00", "GETTEX2", "C", "30", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "AIR", "2024-12-08", "GETTEX2", "", "0", "ClosedLot", "5"],
        ["Cash Report", "Header", "Currency", "Ending Cash"],
        ["Cash Report", "Data", "USD", "1000"],
    ]
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review_rows == 3
    assert "EUDARK" in result.summary.review_exchanges
    assert "EUIBSI" in result.summary.review_exchanges
    assert "GETTEX2" in result.summary.review_exchanges

def test_commission_is_applied_for_long_closing_trade(tmp_path: Path) -> None:
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
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "-1", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-09", "IBIS2", "", "0", "", "ClosedLot", "20"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.wins_eur == Decimal("71.1")
    out_rows = _read_rows(result.output_csv_path)
    header, data = _trades_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    trade = next(r for r in data if r[2 + idx["DataDiscriminator"]] == "Trade")
    assert Decimal(trade[2 + idx["Comm/Fee (EUR)"]]) == Decimal("-0.90000000")
    assert Decimal(trade[2 + idx["Realized P/L (EUR)"]]) == Decimal("71.10000000")

def test_commission_is_applied_for_short_closing_trade(tmp_path: Path) -> None:
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
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "-100", "-1", "Trade", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-09", "IBIS2", "", "0", "", "ClosedLot", "-20"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_13.losses_eur == Decimal("72.9")

def test_cli_prints_output_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rows = _base_rows()
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)

    from integrations.ibkr import activity_statement_analyzer as module

    monkeypatch.setattr(module, "_default_fx_provider", lambda _cache_dir: _fx_provider)
    monkeypatch.setattr(
        "sys.argv",
        [
            "activity_statement_analyzer.py",
            "--input",
            str(input_csv),
            "--tax-year",
            "2025",
            "--tax-exempt-mode",
            "listed_symbol",
            "--report-alias",
            "account_A",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    exit_code = module.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Modified CSV:" in out
    assert "Declaration TXT:" in out
    assert "account_A" in out

def test_report_alias_is_in_output_filenames(tmp_path: Path) -> None:
    result = _run(tmp_path, _base_rows(), mode="listed_symbol", report_alias="acc_1")
    assert "acc_1" in result.output_csv_path.name
    assert "acc_1" in result.declaration_txt_path.name

def test_report_alias_is_normalized_for_filename(tmp_path: Path) -> None:
    result = _run(tmp_path, _base_rows(), mode="listed_symbol", report_alias="  acc main #1 ")
    assert "acc_main_1" in result.output_csv_path.name

def test_invalid_report_alias_fails(tmp_path: Path) -> None:
    with pytest.raises(IbkrAnalyzerError, match="report alias must contain at least one alphanumeric character"):
        _ = _run(tmp_path, _base_rows(), mode="listed_symbol", report_alias="!!!")
