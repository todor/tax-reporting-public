from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.ibkr.activity_statement_analyzer import (
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    DIVIDEND_TAX_RATE,
    EXCHANGE_CLASS_EU_NON_REGULATED,
    EXCHANGE_CLASS_EU_REGULATED,
    EXCHANGE_CLASS_UNKNOWN,
    IbkrAnalyzerError,
    _classify_exchange,
    _normalize_exchange,
    analyze_ibkr_activity_statement,
)


def _write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def _read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def _fx_provider(currency: str, on_date: date) -> Decimal:  # noqa: ARG001
    table = {
        "EUR": Decimal("1"),
        "USD": Decimal("0.9"),
        "CHF": Decimal("1.1"),
    }
    return table[currency]


def _base_rows() -> list[list[str]]:
    return [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2"],
        ["Financial Instrument Information", "Data", "Stocks", "TSLA", "NASDAQ"],
        ["Financial Instrument Information", "Data", "Treasury Bills", "BGTB", "IBIS"],
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
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2025-01-10, 10:00:00",
            "IBIS2",
            "C;O",
            "100",
            "Trade",
            "",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2024-12-20",
            "IBIS2",
            "",
            "0",
            "ClosedLot",
            "30",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "TSLA",
            "2025-02-10, 12:00:00",
            "NASDAQ",
            "C",
            "120",
            "Trade",
            "",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "TSLA",
            "2025-02-09",
            "NASDAQ",
            "",
            "0",
            "ClosedLot",
            "20",
        ],
        ["Cash Report", "Header", "Currency", "Ending Cash"],
        ["Cash Report", "Data", "USD", "1000"],
    ]


def _sanity_rows(*, trade_basis: str = "-20", realized_pl: str = "79") -> list[list[str]]:
    return [
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
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "BMW",
            "2025-01-10, 10:00:00",
            "IBIS2",
            "C",
            "100",
            "-1",
            "Trade",
            trade_basis,
            realized_pl,
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-20", "IBIS2", "", "0", "", "ClosedLot", "20", ""],
        ["Trades", "SubTotal", "Stocks", "USD", "BMW", "", "", "", "100", "-1", "", trade_basis, realized_pl],
        ["Trades", "Total", "Stocks", "USD", "", "", "", "", "100", "-1", "", trade_basis, realized_pl],
    ]


def _treasury_rows(
    *,
    trade_symbol: str,
    listing_symbol: str = "912797NP8",
    listing_exchange: str = "IBIS2",
) -> list[list[str]]:
    return [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Treasury Bills", listing_symbol, listing_exchange],
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
        ["Trades", "Data", "Treasury Bills", "USD", trade_symbol, "2025-01-10, 10:00:00", "IBIS2", "C", "100", "Trade", ""],
        ["Trades", "Data", "Treasury Bills", "USD", trade_symbol, "2024-12-20", "IBIS2", "", "0", "ClosedLot", "20"],
    ]


def _rows_with_review_status(
    *,
    listing_exchange: str,
    execution_exchange: str,
    review_status: str,
) -> list[list[str]]:
    return [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", listing_exchange],
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
            "Review Status",
        ],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", execution_exchange, "C", "100", "Trade", "", review_status],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-20", execution_exchange, "", "0", "ClosedLot", "30", ""],
    ]


def _run(
    tmp_path: Path,
    rows: list[list[str]],
    *,
    mode: str = "listed_symbol",
    year: int = 2025,
    report_alias: str | None = None,
):
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)
    return analyze_ibkr_activity_statement(
        input_csv=input_csv,
        tax_year=year,
        tax_exempt_mode=mode,  # type: ignore[arg-type]
        report_alias=report_alias,
        output_dir=tmp_path / "out",
        fx_rate_provider=_fx_provider,
    )


def _tax_credit_debug_payload(result) -> dict[str, object]:
    debug_path = Path(result.summary.tax_credit_debug_report_path)
    assert debug_path.exists()
    return json.loads(debug_path.read_text(encoding="utf-8"))


def _trades_header_and_data(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    header: list[str] | None = None
    data_rows: list[list[str]] = []
    for row in rows:
        if len(row) < 2 or row[0] != "Trades":
            continue
        if row[1] == "Header":
            header = row
        elif row[1] == "Data":
            data_rows.append(row)
    assert header is not None
    return header, data_rows


def _interest_header_and_data(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    header: list[str] | None = None
    data_rows: list[list[str]] = []
    for row in rows:
        if len(row) < 2 or row[0] != "Interest":
            continue
        if row[1] == "Header":
            header = row
        elif row[1] == "Data":
            data_rows.append(row)
    assert header is not None
    return header, data_rows


def _rows_with_interest(interest_rows: list[list[str]], *, mtm_withholding_total: str = "-5") -> list[list[str]]:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            *interest_rows,
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", mtm_withholding_total],
        ]
    )
    return rows


def _rows_with_dividends_and_withholding(
    dividend_rows: list[list[str]],
    withholding_rows: list[list[str]],
) -> list[list[str]]:
    rows = _base_rows()
    rows.extend(
        [
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
            *dividend_rows,
            ["Withholding Tax", "Header", "Currency", "Date", "Description", "Amount", "Code"],
            *withholding_rows,
        ]
    )
    return rows


def _rows_for_open_position_check(
    *,
    open_rows: list[tuple[str, str]],
    trade_rows: list[tuple[str, str]],
    listing_symbol: str = "4GLD, 4GLDd",
) -> list[list[str]]:
    rows: list[list[str]] = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch"],
        ["Financial Instrument Information", "Data", "Stocks", listing_symbol, "IBIS2"],
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
            "Quantity",
            "DataDiscriminator",
            "Basis",
        ],
    ]
    for symbol, quantity in trade_rows:
        rows.append(
            [
                "Trades",
                "Data",
                "Stocks",
                "USD",
                symbol,
                "2025-01-10, 10:00:00",
                "IBIS2",
                "",
                "0",
                quantity,
                "Order",
                "",
            ]
        )

    rows.append(
        ["Open Positions", "Header", "Asset Category", "Symbol", "Summary Quantity", "DataDiscriminator"]
    )
    for symbol, quantity in open_rows:
        rows.append(
            [
                "Open Positions",
                "Data",
                "Stocks",
                symbol,
                quantity,
                "Summary",
            ]
        )
    return rows


def _dividends_header_and_data(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    header: list[str] | None = None
    data_rows: list[list[str]] = []
    for row in rows:
        if len(row) < 2 or row[0] != "Dividends":
            continue
        if row[1] == "Header":
            header = row
        elif row[1] == "Data":
            data_rows.append(row)
    assert header is not None
    return header, data_rows


def _withholding_header_and_data(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    header: list[str] | None = None
    data_rows: list[list[str]] = []
    for row in rows:
        if len(row) < 2 or row[0] != "Withholding Tax":
            continue
        if row[1] == "Header":
            header = row
        elif row[1] == "Data":
            data_rows.append(row)
    assert header is not None
    return header, data_rows


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


def test_open_position_reconciliation_happy_path(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "7")],
        trade_rows=[("4GLDd", "10"), ("4GLDd", "-3")],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
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
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows == 0
    assert not any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_quantity_mismatch_triggers_review(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "6")],
        trade_rows=[("4GLDd", "7")],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows >= 1
    assert any("OPEN_POSITION_TRADE_QTY_MISMATCH" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_unmatched_open_position_symbol_triggers_review(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("UNKNOWN", "5")],
        trade_rows=[("4GLDd", "5")],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows >= 1
    assert any("OPEN_POSITION_UNMATCHED_INSTRUMENT" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_unmatched_trade_symbol_triggers_review(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "5")],
        trade_rows=[("UNKNOWN", "5")],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows >= 1
    assert any("TRADE_UNMATCHED_INSTRUMENT" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_accepts_comma_formatted_quantities(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "1,001")],
        trade_rows=[("4GLDd", "1,001")],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows == 0
    assert not any("invalid order quantity" in warning for warning in result.summary.warnings)


def test_open_position_reconciliation_treats_empty_quantity_as_zero(tmp_path: Path) -> None:
    rows = _rows_for_open_position_check(
        open_rows=[("4GLD", "")],
        trade_rows=[("4GLDd", "")],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows == 0
    assert not any("invalid order quantity" in warning for warning in result.summary.warnings)


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


def test_review_bucket_excluded_from_appendix_totals(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUIBSI"  # non-regulated
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review.rows == 1
    assert result.summary.appendix_13.rows == 0


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


def test_declaration_text_contains_required_sections(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "GETTEX2"
    result = _run(tmp_path, rows, mode="execution_exchange")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" in text
    assert "СТАТУС: REQUIRED" in text
    assert "Приложение 5" in text
    assert "Приложение 13" in text
    assert "РЪЧНА ПРОВЕРКА (ИЗКЛЮЧЕНИ ОТ АВТОМАТИЧНИТЕ ТАБЛИЦИ)" in text
    assert "ВНИМАНИЕ: FOREX ОПЕРАЦИИ" in text


def test_forex_rows_are_ignored_with_warning_in_text(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.insert(
        11,
        [
            "Trades",
            "Data",
            "Forex",
            "USD",
            "EUR.USD",
            "2025-03-01, 10:00:00",
            "IDEALPRO",
            "C",
            "10",
            "Trade",
            "",
        ],
    )
    rows.insert(
        12,
        [
            "Trades",
            "Data",
            "Forex",
            "USD",
            "EUR.USD",
            "2025-02-28",
            "IDEALPRO",
            "",
            "0",
            "ClosedLot",
            "2",
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.forex_ignored_rows == 1
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "СТАТУС: REQUIRED" in text
    assert "Forex сделки" in text


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


def test_manual_check_section_is_omitted_when_not_required(tmp_path: Path) -> None:
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
        ["Trades", "Data", "Stocks", "EUR", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "0", "Trade", ""],
        ["Trades", "Data", "Stocks", "EUR", "BMW", "2024-12-01", "IBIS2", "", "0", "", "ClosedLot", "20"],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" not in text
    assert "СТАТУС: NOT REQUIRED" not in text


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


def test_informational_warnings_do_not_trigger_manual_check_required(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUDARK"  # informational only in listed_symbol mode for EU-listed symbol
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert any("informational only" in warning for warning in result.summary.warnings)
    assert result.summary.review_required_rows == 0
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" not in text


def test_interest_scoped_headers_are_resolved_from_active_header(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "USD", "2025-03-01", "USD Credit Interest for Mar-2025", "10"],
            ["Interest", "Header", "Description", "Amount", "Date", "Currency"],
            ["Interest", "Data", "EUR Credit Interest for Apr-2025", "2", "2025-04-01", "EUR"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-1.5"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.interest_taxable_rows == 2
    assert result.summary.appendix_6_code_603_eur == Decimal("11")


def test_interest_total_rows_are_skipped(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "Total", "2025-03-01", "USD Credit Interest for Mar-2025", ""],
            ["Interest", "Data", "Total in EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", ""],
            ["Interest", "Data", "Total Interest in EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", ""],
            ["Interest", "Data", "USD", "2025-03-01", "USD Credit Interest for Mar-2025", "10"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.interest_total_rows_skipped == 3
    assert result.summary.interest_processed_rows == 1
    assert result.summary.appendix_6_code_603_eur == Decimal("9")


def test_interest_type_extraction_and_classification(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "EUR", "2025-02-01", "EUR Credit Interest for Feb-2025", "1"],
            ["Interest", "Data", "EUR", "2025-03-01", "EUR IBKR Managed Securities (SYEP) Interest for Mar-2025", "2"],
            ["Interest", "Data", "USD", "2025-04-01", "USD Debit Interest for Apr-2025", "-3"],
            ["Interest", "Data", "USD", "2025-05-01", "USD Borrow Fees for May-2025", "-4"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _interest_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}

    def find_row(desc: str) -> list[str]:
        return next(r for r in data_rows if r[2 + idx["Description"]] == desc)

    credit = find_row("EUR Credit Interest for Feb-2025")
    syep = find_row("EUR IBKR Managed Securities (SYEP) Interest for Mar-2025")
    debit = find_row("USD Debit Interest for Apr-2025")
    borrow = find_row("USD Borrow Fees for May-2025")

    assert credit[2 + idx["Status"]] == "TAXABLE"
    assert credit[2 + idx["Amount (EUR)"]] == "1.00000000"

    assert syep[2 + idx["Status"]] == "TAXABLE"
    assert syep[2 + idx["Amount (EUR)"]] == "2.00000000"

    assert debit[2 + idx["Status"]] == "NON-TAXABLE"
    assert debit[2 + idx["Amount (EUR)"]] == ""

    assert borrow[2 + idx["Status"]] == "NON-TAXABLE"
    assert borrow[2 + idx["Amount (EUR)"]] == ""


def test_unknown_interest_type_marks_review_required(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "USD", "2025-05-01", "USD Special Interest Adjustment for May-2025", "7"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.interest_unknown_rows == 1
    assert result.summary.review_required_rows >= 1
    assert "Special Interest Adjustment" in ",".join(result.summary.interest_unknown_types)

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _interest_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    unknown = data_rows[0]
    assert unknown[2 + idx["Status"]] == "UNKNOWN"
    assert unknown[2 + idx["Amount (EUR)"]] == ""

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "НУЖЕН Е ПРЕГЛЕД: открити са непознати видове лихви" in text
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" in text


def test_interest_review_status_human_override_is_applied(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount", "Review Status"],
            ["Interest", "Data", "USD", "2025-03-01", "USD Special Interest Adjustment for Mar-2025", "10", "TAXABLE"],
            ["Interest", "Data", "USD", "2025-03-02", "USD Credit Interest for Mar-2025", "8", "NON-TAXABLE"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-1"],
        ]
    )
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)

    result = analyze_ibkr_activity_statement(
        input_csv=input_csv,
        tax_year=2025,
        tax_exempt_mode="listed_symbol",  # type: ignore[arg-type]
        output_dir=tmp_path / "out",
        fx_rate_provider=lambda c, _d: Decimal("1") if c == "EUR" else Decimal("0.5"),
    )
    # Unknown -> TAXABLE override contributes: 10 * 0.5 = 5
    # Credit -> NON-TAXABLE override excluded
    assert result.summary.appendix_6_code_603_eur == Decimal("5")
    assert result.summary.interest_unknown_rows == 0

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _interest_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    first = data_rows[0]
    second = data_rows[1]
    assert first[2 + idx["Status"]] == "TAXABLE"
    assert second[2 + idx["Status"]] == "NON-TAXABLE"


def test_appendix_6_total_code_603_uses_credit_and_syep_only(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "USD", "2025-03-01", "USD Credit Interest for Mar-2025", "10"],
            ["Interest", "Data", "EUR", "2025-03-02", "EUR IBKR Managed Securities (SYEP) Interest for Mar-2025", "5"],
            ["Interest", "Data", "USD", "2025-03-03", "USD Debit Interest for Mar-2025", "-2"],
            ["Interest", "Data", "USD", "2025-03-04", "USD Borrow Fees for Mar-2025", "-1"],
            ["Interest", "Data", "USD", "2025-03-05", "USD Special Interest Adjustment for Mar-2025", "1"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_6_code_603_eur == Decimal("14")


def test_interest_fx_uses_row_date_and_unknown_is_not_converted(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "USD", "2025-03-01", "USD Credit Interest for Mar-2025", "10"],
            ["Interest", "Data", "USD", "2025-03-02", "USD IBKR Managed Securities (SYEP) Interest for Mar-2025", "10"],
            ["Interest", "Data", "USD", "2025-03-02", "USD Special Interest Adjustment for Mar-2025", "10"],
        ]
    )
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)

    def fx_by_date(currency: str, on_date: date) -> Decimal:
        if currency == "EUR":
            return Decimal("1")
        if currency == "USD" and on_date.day == 1:
            return Decimal("0.5")
        if currency == "USD" and on_date.day == 2:
            return Decimal("0.8")
        return Decimal("1")

    result = analyze_ibkr_activity_statement(
        input_csv=input_csv,
        tax_year=2025,
        tax_exempt_mode="listed_symbol",  # type: ignore[arg-type]
        output_dir=tmp_path / "out",
        fx_rate_provider=fx_by_date,
    )
    assert result.summary.appendix_6_code_603_eur == Decimal("13")

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _interest_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    unknown = next(r for r in data_rows if "Special Interest Adjustment" in r[2 + idx["Description"]])
    assert unknown[2 + idx["Amount (EUR)"]] == ""


def test_interest_withholding_is_extracted_from_mark_to_market_summary(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [["Interest", "Data", "EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", "10"]],
        mtm_withholding_total="-3.75",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_9_withholding_paid_eur == Decimal("3.75")
    assert result.summary.appendix_9_withholding_source_found is True


def test_appendix_9_section_contains_expected_values(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", "20"],
            ["Interest", "Data", "EUR", "2025-03-01", "EUR IBKR Managed Securities (SYEP) Interest for Mar-2025", "5"],
        ],
        mtm_withholding_total="-4",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Приложение 9" in text
    assert "Част II" in text
    assert "Държава: Ирландия" in text
    assert "Код вид доход: 603" in text
    assert "Брутен размер на дохода (включително платеният данък): 20.00" in text
    assert "Платен данък в чужбина: 4.00" in text
    assert "Допустим размер на данъчния кредит: 2.00" in text
    assert "Размер на признатия данъчен кредит: 2.00" in text


def test_appendix_9_allowable_credit_uses_code_constant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.ibkr.activity_statement_analyzer as ibkr_module

    monkeypatch.setattr(ibkr_module, "APPENDIX_9_ALLOWABLE_CREDIT_RATE", Decimal("0.20"))
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", "20"],
        ],
        mtm_withholding_total="-4",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Допустим размер на данъчния кредит: 4.00" in text
    assert "Размер на признатия данъчен кредит: 4.00" in text


def test_appendix_9_country_level_credit_is_not_rowwise(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "EUR", "2025-01-05", "EUR Credit Interest for Jan-2025", "100"],
            ["Interest", "Data", "EUR", "2025-02-05", "EUR Credit Interest for Feb-2025", "100"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-15"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert len(result.summary.appendix_9_country_results) == 1
    country = result.summary.appendix_9_country_results["IE"]
    assert country.aggregated_gross_eur == Decimal("200")
    assert country.aggregated_foreign_tax_paid_eur == Decimal("15")
    assert country.allowable_credit_aggregated_eur == Decimal("200") * APPENDIX_9_ALLOWABLE_CREDIT_RATE
    assert country.recognized_credit_correct_eur == Decimal("15")
    assert country.recognized_credit_wrong_rowwise_eur == Decimal("0")
    assert country.delta_correct_minus_rowwise_eur == Decimal("15")

    payload = _tax_credit_debug_payload(result)
    assert "_tax_credit_debug" in result.summary.tax_credit_debug_report_path
    appendix_9_entries = payload["appendix_9"]
    assert isinstance(appendix_9_entries, list)
    ie_entry = next(item for item in appendix_9_entries if item["country_iso"] == "IE")
    assert Decimal(ie_entry["recognized_credit_correct"]) == Decimal("15")
    assert Decimal(ie_entry["recognized_credit_wrong_rowwise"]) == Decimal("0")
    assert Decimal(ie_entry["delta_correct_minus_rowwise"]) == Decimal("15")


def test_appendix_9_country_level_uses_mtm_source_of_paid_tax(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "EUR", "2025-01-05", "EUR Credit Interest for Jan-2025", "100"],
            ["Interest", "Data", "EUR", "2025-02-05", "EUR Credit Interest for Feb-2025", "100"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-20"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    country = result.summary.appendix_9_country_results["IE"]
    assert country.recognized_credit_correct_eur == Decimal("20")
    assert country.recognized_credit_wrong_rowwise_eur == Decimal("0")
    assert country.delta_correct_minus_rowwise_eur == Decimal("20")


def test_interest_output_rendering_contains_appendix_6_and_review_warning(tmp_path: Path) -> None:
    rows = _rows_with_interest(
        [
            ["Interest", "Data", "EUR", "2025-02-01", "EUR Credit Interest for Feb-2025", "1"],
            ["Interest", "Data", "USD", "2025-03-01", "USD Special Interest Adjustment for Mar-2025", "2"],
            ["Interest", "Data", "Total in EUR", "2025-03-31", "EUR Credit Interest for Mar-2025", ""],
        ],
        mtm_withholding_total="-0.2",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Приложение 6" in text
    assert "Част I" in text
    assert "Обща сума на доходите с код 603" in text
    assert "НУЖЕН Е ПРЕГЛЕД: открити са непознати видове лихви" in text
    assert "Приложение 9" in text


def test_interest_data_before_header_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Data", "EUR", "2025-02-01", "EUR Credit Interest for Feb-2025", "1"],
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
        ]
    )
    with pytest.raises(IbkrAnalyzerError, match="Interest row encountered before Interest Header"):
        _ = _run(tmp_path, rows, mode="listed_symbol")


def test_interest_missing_required_column_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description"],
            ["Interest", "Data", "EUR", "2025-02-01", "EUR Credit Interest for Feb-2025"],
        ]
    )
    with pytest.raises(IbkrAnalyzerError, match="Interest header at row"):
        _ = _run(tmp_path, rows, mode="listed_symbol")


def test_dividends_scoped_headers_are_resolved_from_active_header(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
            ["Dividends", "Header", "Description", "Amount", "Date", "Currency"],
            ["Dividends", "Data", "IS04(IE00BSKRJZ44) Cash Dividend USD 0.0735 per Share", "2", "2025-03-02", "EUR"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.dividends_processed_rows == 2
    assert result.summary.dividends_cash_rows == 2

    out_rows = _read_rows(result.output_csv_path)
    current_header: list[str] | None = None
    extracted: list[dict[str, str]] = []
    for row in out_rows:
        if len(row) < 2 or row[0] != "Dividends":
            continue
        if row[1] == "Header":
            current_header = row[2:]
            continue
        if row[1] != "Data" or current_header is None:
            continue
        values = row[2:] + [""] * (len(current_header) - len(row[2:]))
        extracted.append({name: values[i] for i, name in enumerate(current_header)})

    us_row = next(item for item in extracted if "US8760301072" in item["Description"])
    ie_row = next(item for item in extracted if "IE00BSKRJZ44" in item["Description"])
    assert us_row["Amount (EUR)"] == "9.00000000"
    assert us_row["Country"] == "United States"
    assert ie_row["Amount (EUR)"] == "2.00000000"
    assert ie_row["Country"] == "Ireland"


def test_dividend_total_rows_are_skipped(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "Total", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", ""],
            ["Dividends", "Data", "Total in EUR", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", ""],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.dividends_total_rows_skipped == 2
    assert result.summary.dividends_processed_rows == 1
    assert result.summary.dividends_cash_rows == 1


def test_dividend_routing_cash_lieu_unknown(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
            ["Dividends", "Data", "USD", "2025-03-02", "ABC(US1234567890) Lieu Received USD 0.10 per Share", "5"],
            ["Dividends", "Data", "USD", "2025-03-03", "XYZ(US1234567890) Special Distribution", "7"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.dividends_cash_rows == 1
    assert result.summary.dividends_lieu_rows == 1
    assert result.summary.dividends_unknown_rows == 1
    assert result.summary.review_required_rows >= 1
    assert result.summary.appendix_6_lieu_received_eur == Decimal("4.5")
    assert result.summary.appendix_6_code_603_eur == Decimal("4.5")

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _dividends_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    cash_row = next(r for r in data_rows if "Cash Dividend" in r[2 + idx["Description"]])
    lieu_row = next(r for r in data_rows if "Lieu Received" in r[2 + idx["Description"]])
    unknown_row = next(r for r in data_rows if "Special Distribution" in r[2 + idx["Description"]])
    assert cash_row[2 + idx["Appendix"]] == "Appendix 8"
    assert lieu_row[2 + idx["Appendix"]] == "Appendix 6"
    assert unknown_row[2 + idx["Appendix"]] == "UNKNOWN"
    assert unknown_row[2 + idx["Review Status"]] == ""


def test_dividend_isin_country_mapping_us_lu_ie(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "1"],
            ["Dividends", "Data", "USD", "2025-03-02", "TIGO(LU0038705702) Cash Dividend USD 2.00 per Share", "1"],
            ["Dividends", "Data", "EUR", "2025-03-03", "IS04(IE00BSKRJZ44) Cash Dividend USD 0.0735 per Share", "1"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert set(result.summary.appendix_8_by_country) == {"US", "LU", "IE"}

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _dividends_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    assert next(r for r in data_rows if "US8760301072" in r[2 + idx["Description"]])[2 + idx["Country"]] == "United States"
    assert next(r for r in data_rows if "LU0038705702" in r[2 + idx["Description"]])[2 + idx["Country"]] == "Luxembourg"
    assert next(r for r in data_rows if "IE00BSKRJZ44" in r[2 + idx["Description"]])[2 + idx["Country"]] == "Ireland"


def test_withholding_tax_filtering_and_country_aggregation(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
            ["Dividends", "Data", "USD", "2025-03-02", "TIGO(LU0038705702) Cash Dividend USD 2.00 per Share", "10"],
        ],
        [
            ["Withholding Tax", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share - US Tax", "-2", ""],
            ["Withholding Tax", "Data", "USD", "2025-03-02", "TIGO(LU0038705702) Cash Dividend USD 2.00 per Share - LU Tax", "-3", ""],
            ["Withholding Tax", "Data", "USD", "2025-03-03", "Withholding @ 20% on Credit Interest for Mar-2025", "-1", ""],
            ["Withholding Tax", "Data", "Total in EUR", "2025-03-31", "Totals", "", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.withholding_processed_rows == 3
    assert result.summary.withholding_total_rows_skipped == 1
    assert result.summary.withholding_dividend_rows == 2
    assert result.summary.withholding_non_dividend_rows == 1
    assert result.summary.appendix_8_by_country["US"].withholding_tax_paid_eur == Decimal("1.8")
    assert result.summary.appendix_8_by_country["LU"].withholding_tax_paid_eur == Decimal("2.7")

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _withholding_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    non_div = next(r for r in data_rows if "Credit Interest" in r[2 + idx["Description"]])
    assert non_div[2 + idx["Country"]] == "Ireland"
    assert non_div[2 + idx["Amount (EUR)"]] == "-0.90000000"
    assert non_div[2 + idx["ISIN"]] == ""
    assert non_div[2 + idx["Appendix"]] == "Appendix 9"
    assert non_div[2 + idx["Status"]] == "TAXABLE"


def test_dividends_and_withholding_do_not_duplicate_manual_columns(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount", "Country", "Amount (EUR)", "ISIN", "Appendix", "Review Status"],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10", "", "", "", "", ""],
            ["Withholding Tax", "Header", "Currency", "Date", "Description", "Amount", "Code", "Country", "Amount (EUR)", "ISIN", "Appendix", "Review Status"],
            ["Withholding Tax", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share - US Tax", "-2", "", "", "", "", "", ""],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    out_rows = _read_rows(result.output_csv_path)
    div_header = next(r for r in out_rows if len(r) > 1 and r[0] == "Dividends" and r[1] == "Header")
    wh_header = next(r for r in out_rows if len(r) > 1 and r[0] == "Withholding Tax" and r[1] == "Header")

    assert div_header[2:].count("Country") == 1
    assert div_header[2:].count("Amount (EUR)") == 1
    assert div_header[2:].count("Status") == 1
    assert div_header[2:].count("Review Status") == 1
    assert wh_header[2:].count("Country") == 1
    assert wh_header[2:].count("Amount (EUR)") == 1
    assert wh_header[2:].count("Status") == 1
    assert wh_header[2:].count("Review Status") == 1


def test_withholding_review_status_taxable_uses_manual_values_for_aggregation(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", "20"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-4"],
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
            ["Withholding Tax", "Header", "Currency", "Date", "Description", "Amount", "Code", "Country", "Amount (EUR)", "Appendix", "Review Status"],
            ["Withholding Tax", "Data", "USD", "2025-03-01", "Some manual dividend withholding", "-1", "", "Luxembourg", "5.00000000", "Appendix 8", "TAXABLE"],
            ["Withholding Tax", "Data", "USD", "2025-03-02", "Withholding @ 20% on Credit Interest for Mar-2025", "-1", "", "Ireland", "2.00000000", "Appendix 9", "TAXABLE"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_8_by_country["LU"].withholding_tax_paid_eur == Decimal("5")
    assert result.summary.appendix_9_withholding_paid_eur == Decimal("4")
    assert result.summary.appendix_9_country_results["IE"].aggregated_foreign_tax_paid_eur == Decimal("4")

def test_appendix_8_tax_credit_math_uses_configurable_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.ibkr.activity_statement_analyzer as module

    monkeypatch.setattr(module, "DIVIDEND_TAX_RATE", Decimal("0.10"))
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "TPR(US8760301072) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "TPR(US8760301072) Cash Dividend EUR 1.00 per Share - US Tax", "-7", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert result.summary.dividend_tax_rate == Decimal("0.10")
    assert "Приложение 8" in text
    assert "Брутен размер на дохода: 100.00" in text
    assert "Платен данък в чужбина: 7.00" in text
    assert "Допустим размер на данъчния кредит: 7.00" in text
    assert "Размер на признатия данъчен кредит: 7.00" in text
    assert "Дължим данък, подлежащ на внасяне: 3.00" in text


def test_appendix_8_country_level_credit_is_not_rowwise(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
            ["Dividends", "Data", "EUR", "2025-03-02", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-15", ""],
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share - US Tax", "0", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    country = result.summary.appendix_8_country_results["US"]
    assert len(result.summary.appendix_8_country_results) == 1
    assert country.aggregated_gross_eur == Decimal("200")
    assert country.aggregated_foreign_tax_paid_eur == Decimal("15")
    assert country.bulgarian_tax_aggregated_eur == Decimal("200") * DIVIDEND_TAX_RATE
    assert country.credit_correct_eur == Decimal("10")
    assert country.credit_wrong_rowwise_eur == Decimal("5")
    assert country.delta_correct_minus_rowwise_eur == Decimal("5")
    assert country.tax_due_correct_eur == Decimal("0")
    assert country.tax_due_wrong_rowwise_eur == Decimal("5")

    payload = _tax_credit_debug_payload(result)
    appendix_8_entries = payload["appendix_8"]
    assert isinstance(appendix_8_entries, list)
    us_entry = next(item for item in appendix_8_entries if item["country_iso"] == "US")
    assert Decimal(us_entry["credit_correct"]) == Decimal("10")
    assert Decimal(us_entry["credit_wrong_rowwise"]) == Decimal("5")
    assert Decimal(us_entry["delta_correct_minus_rowwise"]) == Decimal("5")


def test_appendix_8_country_level_regression_when_rowwise_matches(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
            ["Dividends", "Data", "EUR", "2025-03-02", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-2", ""],
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share - US Tax", "-2", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    country = result.summary.appendix_8_country_results["US"]
    assert country.credit_correct_eur == Decimal("4")
    assert country.credit_wrong_rowwise_eur == Decimal("4")
    assert country.delta_correct_minus_rowwise_eur == Decimal("0")


def test_appendix_6_includes_lieu_with_interest_contributors(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", "10"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-1"],
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
            ["Dividends", "Data", "EUR", "2025-03-02", "ABC(US1234567890) Lieu Received EUR 0.10 per Share", "5"],
            ["Withholding Tax", "Header", "Currency", "Date", "Description", "Amount", "Code"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.appendix_6_credit_interest_eur == Decimal("10")
    assert result.summary.appendix_6_lieu_received_eur == Decimal("5")
    assert result.summary.appendix_6_code_603_eur == Decimal("15")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Подател: Credit Interest (EUR): 10.00" in text
    assert "Подател: Lieu Received (EUR): 5.00" in text
    assert "Обща сума на доходите с код 603: 15.00" in text


def test_dividend_unknown_or_bad_isin_marks_review(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "XYZ(US1234567890) Special Distribution", "1"],
            ["Dividends", "Data", "USD", "2025-03-02", "No isin Cash Dividend text", "1"],
            ["Dividends", "Data", "USD", "2025-03-03", "ABC(ZZ1234567890) Cash Dividend USD 1.00 per Share", "1"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows >= 3
    assert result.summary.dividends_unknown_rows == 1
    assert result.summary.dividends_country_errors_rows == 2
    assert any("unknown dividend description" in w for w in result.summary.warnings)
    assert any("unknown ISIN country code=ZZ" in w for w in result.summary.warnings)

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" in text


def test_dividend_status_column_auto_fill_and_unknown_triggers_review(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "1"],
            ["Dividends", "Data", "USD", "2025-03-02", "ABC(US1234567890) Lieu Received USD 0.10 per Share", "1"],
            ["Dividends", "Data", "USD", "2025-03-03", "XYZ(US1234567890) Special Distribution", "1"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _dividends_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    assert next(r for r in data_rows if "Cash Dividend" in r[2 + idx["Description"]])[2 + idx["Status"]] == "TAXABLE"
    assert next(r for r in data_rows if "Lieu Received" in r[2 + idx["Description"]])[2 + idx["Status"]] == "TAXABLE"
    assert next(r for r in data_rows if "Special Distribution" in r[2 + idx["Description"]])[2 + idx["Status"]] == "UNKNOWN"
    assert result.summary.review_required_rows >= 1


def test_withholding_status_column_auto_fill_and_unknown_triggers_review(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [],
        [
            ["Withholding Tax", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share - US Tax", "-2", ""],
            ["Withholding Tax", "Data", "USD", "2025-03-02", "Withholding @ 20% on Credit Interest for Mar-2025", "-1", ""],
            ["Withholding Tax", "Data", "USD", "2025-03-03", "Some Adjustment", "-1", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _withholding_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    assert next(r for r in data_rows if "Cash Dividend" in r[2 + idx["Description"]])[2 + idx["Status"]] == "TAXABLE"
    assert next(r for r in data_rows if "Credit Interest" in r[2 + idx["Description"]])[2 + idx["Status"]] == "TAXABLE"
    assert next(r for r in data_rows if "Some Adjustment" in r[2 + idx["Description"]])[2 + idx["Status"]] == "UNKNOWN"
    assert result.summary.review_required_rows >= 1


def test_dividend_review_status_human_override_is_applied(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            [
                "Dividends",
                "Header",
                "Currency",
                "Date",
                "Description",
                "Amount",
                "Review Status",
                "Country",
                "Amount (EUR)",
                "Appendix",
            ],
            [
                "Dividends",
                "Data",
                "USD",
                "2025-03-01",
                "XYZ(US1234567890) Special Distribution",
                "10",
                "TAXABLE",
                "United States",
                "9.00000000",
                "Appendix 8",
            ],
            [
                "Dividends",
                "Data",
                "USD",
                "2025-03-02",
                "TPR(US8760301072) Cash Dividend USD 0.35 per Share",
                "10",
                "NON-TAXABLE",
                "",
                "",
                "Appendix 8",
            ],
            ["Withholding Tax", "Header", "Currency", "Date", "Description", "Amount", "Code"],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_status_overrides_rows >= 2
    assert result.summary.dividends_unknown_rows == 0
    assert result.summary.appendix_8_by_country["US"].gross_dividend_eur == Decimal("9")

    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _dividends_header_and_data(out_rows)
    idx = {c: i for i, c in enumerate(header[2:])}
    forced_taxable = next(r for r in data_rows if "Special Distribution" in r[2 + idx["Description"]])
    forced_non_taxable = next(r for r in data_rows if "Cash Dividend" in r[2 + idx["Description"]])
    assert forced_taxable[2 + idx["Appendix"]] == "Appendix 8"
    assert forced_taxable[2 + idx["Review Status"]] == "TAXABLE"
    assert forced_taxable[2 + idx["Status"]] == "TAXABLE"
    assert forced_non_taxable[2 + idx["Appendix"]] == "Appendix 8"
    assert forced_non_taxable[2 + idx["Review Status"]] == "NON-TAXABLE"
    assert forced_non_taxable[2 + idx["Status"]] == "NON-TAXABLE"


def test_dividend_realistic_fixture_and_output_rendering(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "EUR", "2025-03-01", "EUR Credit Interest for Mar-2025", "20"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-4"],
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share (Ordinary Dividend)", "10"],
            ["Dividends", "Data", "USD", "2025-03-02", "TIGO(LU0038705702) Cash Dividend USD 2.00 per Share (Ordinary Dividend)", "10"],
            ["Dividends", "Data", "USD", "2025-03-03", "IS04(IE00BSKRJZ44) Cash Dividend USD 0.0735 per Share (Mixed Income)", "10"],
            ["Dividends", "Data", "USD", "2025-03-04", "ABC(US1234567890) Lieu Received USD 0.10 per Share", "5"],
            ["Withholding Tax", "Header", "Currency", "Date", "Description", "Amount", "Code"],
            ["Withholding Tax", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share - US Tax", "-2", ""],
            ["Withholding Tax", "Data", "USD", "2025-03-02", "TIGO(LU0038705702) Cash Dividend USD 2.00 per Share - LU Tax", "-2", ""],
            ["Withholding Tax", "Data", "USD", "2025-03-03", "Withholding @ 20% on Credit Interest for Mar-2025", "-1", ""],
        ]
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Приложение 6" in text
    assert "Приложение 8" in text
    assert "Приложение 9" in text
    assert "Код вид доход: 8141" in text
    assert "Код вид доход: 603" in text
    assert "Държава: САЩ" in text
    assert "Държава: Люксембург" in text
    assert "Държава: Ирландия" in text
    assert "Обща сума на доходите с код 603: 24.50" in text
    assert "Брутен размер на дохода (включително платеният данък): 20.00" in text


def test_dividends_data_before_header_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
            ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ]
    )
    with pytest.raises(IbkrAnalyzerError, match="Dividends row encountered before Dividends Header"):
        _ = _run(tmp_path, rows, mode="listed_symbol")


def test_dividends_missing_required_column_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Dividends", "Header", "Currency", "Date", "Description"],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share"],
        ]
    )
    with pytest.raises(IbkrAnalyzerError, match="Dividends header at row"):
        _ = _run(tmp_path, rows, mode="listed_symbol")
