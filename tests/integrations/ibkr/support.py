from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from integrations.ibkr.activity_statement_analyzer import (
    analyze_ibkr_activity_statement,
)
from integrations.ibkr.constants import (
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    DIVIDEND_TAX_RATE,
    EXCHANGE_CLASS_INVALID,
    EXCHANGE_CLASS_NON_EU,
    EXCHANGE_CLASS_EU_NON_REGULATED,
    EXCHANGE_CLASS_EU_REGULATED,
    EXCHANGE_CLASS_UNMAPPED,
    EXCHANGE_CLASS_UNKNOWN,
)
from integrations.ibkr.models import IbkrAnalyzerError
from integrations.ibkr.sections.instruments import (
    _classify_exchange,
    _normalize_exchange,
)

__all__ = [
    "APPENDIX_9_ALLOWABLE_CREDIT_RATE",
    "DIVIDEND_TAX_RATE",
    "EXCHANGE_CLASS_EU_NON_REGULATED",
    "EXCHANGE_CLASS_EU_REGULATED",
    "EXCHANGE_CLASS_INVALID",
    "EXCHANGE_CLASS_NON_EU",
    "EXCHANGE_CLASS_UNKNOWN",
    "EXCHANGE_CLASS_UNMAPPED",
    "IbkrAnalyzerError",
    "_base_rows",
    "_classify_exchange",
    "_fx_provider",
    "_normalize_exchange",
    "_read_rows",
    "_rows_with_review_status",
    "_run",
    "_trades_header_and_data",
    "_treasury_rows",
    "_write_rows",
]


def _write_rows(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch", "Description"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2", "Bayerische Motoren Werke AG"],
        ["Financial Instrument Information", "Data", "Stocks", "TSLA", "NASDAQ", "Tesla Inc"],
        ["Financial Instrument Information", "Data", "Treasury Bills", "BGTB", "IBIS", "Bulgarian Treasury Bill"],
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
    appendix8_dividend_list_mode: str = "company",
    year: int = 2025,
    report_alias: str | None = None,
    eu_regulated_exchanges: list[str] | None = None,
    closed_world: bool = False,
):
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)
    return analyze_ibkr_activity_statement(
        input_csv=input_csv,
        tax_year=year,
        tax_exempt_mode=mode,  # type: ignore[arg-type]
        appendix8_dividend_list_mode=appendix8_dividend_list_mode,  # type: ignore[arg-type]
        report_alias=report_alias,
        output_dir=tmp_path / "out",
        eu_regulated_exchanges=eu_regulated_exchanges,
        closed_world=closed_world,
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


def _inject_financial_instrument_rows(
    rows: list[list[str]],
    listing_rows: list[tuple[str, str, str, str]],
) -> list[list[str]]:
    out = list(rows)
    insert_after = max(
        idx
        for idx, row in enumerate(out)
        if len(row) >= 2 and row[0] == "Financial Instrument Information" and row[1] == "Data"
    )
    for offset, (asset_category, symbol, listing_exch, description) in enumerate(listing_rows):
        out.insert(
            insert_after + 1 + offset,
            ["Financial Instrument Information", "Data", asset_category, symbol, listing_exch, description],
        )
    return out


def _rows_for_open_position_check(
    *,
    open_rows: list[tuple[str, str]],
    trade_rows: list[tuple[str, str]],
    listing_symbol: str = "4GLD, 4GLDd",
) -> list[list[str]]:
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
            "Stocks",
            listing_symbol,
            "IBIS2",
            "Sample Instrument",
            "US1234567890",
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
        [
            "Open Positions",
            "Header",
            "Asset Category",
            "Symbol",
            "Currency",
            "Summary Quantity",
            "Cost Basis",
            "DataDiscriminator",
        ]
    )
    for symbol, quantity in open_rows:
        rows.append(
            [
                "Open Positions",
                "Data",
                "Stocks",
                symbol,
                "USD",
                quantity,
                "0",
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


def _open_positions_header_and_data(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    header: list[str] | None = None
    data_rows: list[list[str]] = []
    for row in rows:
        if len(row) < 2 or row[0] != "Open Positions":
            continue
        if row[1] == "Header":
            header = row
        elif row[1] == "Data":
            data_rows.append(row)
    assert header is not None
    return header, data_rows


def _rows_for_appendix8_part1(
    *,
    open_rows: list[tuple[str, str, str, str]],
    instrument_rows: list[tuple[str, str]],
) -> list[list[str]]:
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
    ]
    for symbol, isin in instrument_rows:
        rows.append(
            [
                "Financial Instrument Information",
                "Data",
                "Stocks",
                symbol,
                "NYSE",
                f"{symbol} Corp",
                isin,
            ]
        )

    rows.extend(
        [
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
        ]
    )
    for symbol, _currency, quantity, _basis in open_rows:
        rows.append(
            [
                "Trades",
                "Data",
                "Stocks",
                "USD",
                symbol,
                "2025-01-10, 10:00:00",
                "NYSE",
                "",
                "0",
                "Order",
                "",
                quantity,
            ]
        )

    rows.append(
        [
            "Open Positions",
            "Header",
            "Asset Category",
            "Symbol",
            "Currency",
            "Summary Quantity",
            "Cost Basis",
            "DataDiscriminator",
        ]
    )
    for symbol, currency, quantity, cost_basis in open_rows:
        rows.append(
            [
                "Open Positions",
                "Data",
                "Stocks",
                symbol,
                currency,
                quantity,
                cost_basis,
                "Summary",
            ]
        )
    return rows


__all__ = [name for name in globals() if not name.startswith("__")]
