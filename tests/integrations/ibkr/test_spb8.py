from __future__ import annotations

from decimal import Decimal

from integrations.shared.spb8 import SPB8Row
from integrations.ibkr.shared import _build_active_headers
from integrations.ibkr.spb8 import extract_ibkr_spb8_rows
from integrations.ibkr.models import InstrumentListing


def _listing(*, symbol: str = "VWCE", isin: str = "IE00BK5BQT80") -> InstrumentListing:
    return InstrumentListing(
        symbol=symbol,
        canonical_symbol=symbol,
        listing_exchange="IBIS",
        listing_exchange_normalized="IBIS",
        listing_exchange_class="eu_regulated",
        is_eu_listed=True,
        description=symbol,
        isin=isin,
    )


def _extract(rows: list[list[str]], listings: dict[str, InstrumentListing] | None = None):
    active_headers, _seen_headers = _build_active_headers(rows)
    return extract_ibkr_spb8_rows(
        rows=rows,
        active_headers=active_headers,
        listings=listings or {"VWCE": _listing()},
        account_name="U123",
    )


def _security_rows(
    *,
    end_qty: str,
    transfer_direction: str | None = None,
    transfer_qty: str | None = None,
    trade_qty: str | None = None,
    transfer_symbol: str = "VWCE",
    transfer_asset: str = "Stocks",
    transfer_type: str = "ACATS",
    transfer_code: str = "",
) -> list[list[str]]:
    rows = [
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description", "Listing Exchange", "ISIN"],
        ["Financial Instrument Information", "Data", "Stocks", "VWCE", "Vanguard", "IBIS", "IE00BK5BQT80"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Summary Quantity", "DataDiscriminator", "Currency", "Cost Basis"],
        ["Open Positions", "Data", "Stocks", "VWCE", end_qty, "Summary", "EUR", "1000"],
    ]
    if trade_qty is not None:
        rows.extend(
            [
                ["Trades", "Header", "Asset Category", "Symbol", "Quantity", "DataDiscriminator"],
                ["Trades", "Data", "Stocks", "VWCE", trade_qty, "Order"],
            ]
        )
    if transfer_direction is not None and transfer_qty is not None:
        rows.extend(
            [
                ["Transfers", "Header", "Asset Category", "Symbol", "Type", "Direction", "Qty", "Code"],
                [
                    "Transfers",
                    "Data",
                    transfer_asset,
                    transfer_symbol,
                    transfer_type,
                    transfer_direction,
                    transfer_qty,
                    transfer_code,
                ],
            ]
        )
    return rows


def test_ibkr_spb8_extracts_cash_by_currency_from_cash_report() -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Cash Report", "Header", "Currency Summary", "Currency", "Total", "Securities", "Futures"],
        ["Cash Report", "Data", "Starting Cash", "Base Currency Summary", "999", "999", "0"],
        ["Cash Report", "Data", "Ending Cash", "Base Currency Summary", "888", "888", "0"],
        ["Cash Report", "Data", "Starting Cash", "EUR", "0", "0", "0"],
        ["Cash Report", "Data", "Ending Cash", "EUR", "0.969861231", "0.969861231", "0"],
        ["Cash Report", "Data", "Starting Cash", "USD", "0", "0", "0"],
        ["Cash Report", "Data", "Ending Cash", "USD", "817.825579235", "817.825579235", "0"],
    ]
    active_headers, _seen_headers = _build_active_headers(rows)

    extracted = extract_ibkr_spb8_rows(rows=rows, active_headers=active_headers, listings={}, account_name="U123")

    assert extracted.warnings == []
    assert extracted.rows == [
        SPB8Row(
            account_name="U123 cash EUR",
            platform="ibkr",
            type_code="03",
            country="Ирландия",
            currency="EUR",
            start_nav=Decimal("0"),
            end_nav=Decimal("0.969861231"),
        ),
        SPB8Row(
            account_name="U123 cash USD",
            platform="ibkr",
            type_code="03",
            country="Ирландия",
            currency="USD",
            start_nav=Decimal("0"),
            end_nav=Decimal("817.825579235"),
        ),
    ]


def test_ibkr_spb8_does_not_use_net_asset_value_for_cash() -> None:
    rows = [
        ["Statement", "Header", "Field", "Value"],
        ["Statement", "Data", "Account", "U123"],
        ["Account Information", "Header", "Field Name", "Field Value"],
        ["Account Information", "Data", "Base Currency", "EUR"],
        ["Net Asset Value", "Header", "Asset Class", "Prior Total", "Current Long", "Current Short", "Current Total", "Change"],
        ["Net Asset Value", "Data", "Cash ", "0", "697.225668113", "0", "697.225668113", "697.225668113"],
    ]
    active_headers, _seen_headers = _build_active_headers(rows)

    extracted = extract_ibkr_spb8_rows(rows=rows, active_headers=active_headers, listings={}, account_name="U123")

    assert extracted.warnings == []
    assert extracted.rows == []


def test_ibkr_spb8_extracts_isin_from_open_positions_and_trades() -> None:
    rows = [
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description", "Listing Exchange", "ISIN"],
        ["Financial Instrument Information", "Data", "Stocks", "VWCE", "Vanguard", "IBIS", "IE00BK5BQT80"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Summary Quantity", "DataDiscriminator", "Currency", "Cost Basis"],
        ["Open Positions", "Data", "Stocks", "VWCE", "15", "Summary", "EUR", "1000"],
        ["Trades", "Header", "Asset Category", "Symbol", "Quantity", "DataDiscriminator"],
        ["Trades", "Data", "Stocks", "VWCE", "3", "Order"],
    ]
    active_headers, _seen_headers = _build_active_headers(rows)
    listings = {
        "VWCE": InstrumentListing(
            symbol="VWCE",
            canonical_symbol="VWCE",
            listing_exchange="IBIS",
            listing_exchange_normalized="IBIS",
            listing_exchange_class="eu_regulated",
            is_eu_listed=True,
            description="Vanguard",
            isin="IE00BK5BQT80",
        )
    }

    extracted = extract_ibkr_spb8_rows(rows=rows, active_headers=active_headers, listings=listings, account_name="U123")

    security_row = extracted.rows[0]
    assert extracted.warnings == [
        "СПБ-8: количествата за ценни книжа са изчислени от Open Positions, Trades и Transfers."
    ]
    assert security_row.type_code == "04"
    assert security_row.isin == "IE00BK5BQT80"
    assert security_row.start_nav == Decimal("12")
    assert security_row.end_nav == Decimal("15")


def test_ibkr_spb8_stock_transfer_in_reconstructs_start_quantity() -> None:
    extracted = _extract(_security_rows(end_qty="100", transfer_direction="In", transfer_qty="100"))

    assert extracted.rows[0].start_nav == Decimal("0")
    assert extracted.rows[0].end_nav == Decimal("100")


def test_ibkr_spb8_stock_transfer_out_reconstructs_start_quantity() -> None:
    extracted = _extract(_security_rows(end_qty="50", transfer_direction="Out", transfer_qty="-50"))

    assert extracted.rows[0].start_nav == Decimal("100")
    assert extracted.rows[0].end_nav == Decimal("50")


def test_ibkr_spb8_transfer_uses_signed_quantity_without_abs_regression() -> None:
    extracted = _extract(_security_rows(end_qty="100", transfer_direction="In", transfer_qty="-100"))

    assert extracted.rows[0].start_nav == Decimal("200")
    assert extracted.rows[0].end_nav == Decimal("100")


def test_ibkr_spb8_acats_in_row_reconstructs_start_quantity() -> None:
    extracted = _extract(
        _security_rows(end_qty="100", transfer_direction="In", transfer_qty="100", transfer_type="ACATS")
    )

    assert extracted.rows[0].start_nav == Decimal("0")
    assert extracted.rows[0].end_nav == Decimal("100")


def test_ibkr_spb8_interdepot_signed_quantities_net_by_isin() -> None:
    rows = [
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description", "Listing Exchange", "ISIN"],
        ["Financial Instrument Information", "Data", "Stocks", "AVGO", "Broadcom", "NASDAQ", "US11135F1012"],
        ["Financial Instrument Information", "Data", "Stocks", "1YD", "Broadcom", "IBIS", "US11135F1012"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Summary Quantity", "DataDiscriminator", "Currency", "Cost Basis"],
        ["Open Positions", "Data", "Stocks", "1YD", "90", "Summary", "EUR", "1000"],
        ["Transfers", "Header", "Asset Category", "Symbol", "Type", "Direction", "Qty", "Code"],
        ["Transfers", "Data", "Stocks", "1YD", "InterDepot", "In", "90", ""],
        ["Transfers", "Data", "Stocks", "AVGO", "InterDepot", "In", "-90", ""],
    ]
    listings = {
        "AVGO": _listing(symbol="AVGO", isin="US11135F1012"),
        "1YD": _listing(symbol="1YD", isin="US11135F1012"),
    }

    extracted = _extract(rows, listings=listings)

    assert extracted.rows[0].isin == "US11135F1012"
    assert extracted.rows[0].start_nav == Decimal("90")
    assert extracted.rows[0].end_nav == Decimal("90")


def test_ibkr_spb8_cancelled_transfer_code_ca_is_excluded() -> None:
    rows = [
        *_security_rows(end_qty="100", transfer_direction="In", transfer_qty="100"),
        ["Transfers", "Data", "Stocks", "VWCE", "ACATS", "In", "50", "Ca"],
    ]

    extracted = _extract(rows)

    assert extracted.rows[0].start_nav == Decimal("0")
    assert extracted.rows[0].end_nav == Decimal("100")


def test_ibkr_spb8_transferred_lot_rows_are_not_transfer_movements() -> None:
    rows = [
        *_security_rows(end_qty="100", transfer_direction="In", transfer_qty="100"),
        ["Transfers", "Data", "", "Transferred Lot:", "", "", "100", "ST"],
    ]

    extracted = _extract(rows)

    assert extracted.rows[0].start_nav == Decimal("0")
    assert extracted.rows[0].end_nav == Decimal("100")
    assert not any("Transferred Lot" in warning for warning in extracted.warnings)


def test_ibkr_spb8_combines_trades_and_transfers_for_start_quantity() -> None:
    extracted = _extract(_security_rows(end_qty="80", trade_qty="30", transfer_direction="In", transfer_qty="50"))

    assert extracted.rows[0].start_nav == Decimal("0")
    assert extracted.rows[0].end_nav == Decimal("80")


def test_ibkr_spb8_transfer_for_symbol_not_in_open_positions_is_ignored() -> None:
    rows = _security_rows(end_qty="10", transfer_direction="In", transfer_qty="4", transfer_symbol="TSLA")
    listings = {
        "VWCE": _listing(),
        "TSLA": _listing(symbol="TSLA", isin="US88160R1014"),
    }

    extracted = _extract(rows, listings=listings)

    assert len(extracted.rows) == 1
    assert extracted.rows[0].isin == "IE00BK5BQT80"
    assert extracted.rows[0].start_nav == Decimal("10")
    assert not any("TSLA" in warning for warning in extracted.warnings)


def test_ibkr_spb8_treasury_bill_transfer_uses_existing_resolution() -> None:
    rows = [
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description", "Listing Exchange", "ISIN"],
        ["Financial Instrument Information", "Data", "Treasury Bills", "912797NP8", "Treasury Bill", "IBIS", "US912797NP81"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Summary Quantity", "DataDiscriminator", "Currency", "Cost Basis"],
        ["Open Positions", "Data", "Treasury Bills", "912797NP8", "20", "Summary", "USD", "1000"],
        ["Transfers", "Header", "Asset Category", "Symbol", "Direction", "Qty", "Code"],
        ["Transfers", "Data", "Treasury Bills", "US T-BILL 912797NP8", "In", "20", ""],
    ]
    listings = {"912797NP8": _listing(symbol="912797NP8", isin="US912797NP81")}

    extracted = _extract(rows, listings=listings)

    assert extracted.rows[0].isin == "US912797NP81"
    assert extracted.rows[0].start_nav == Decimal("0")
    assert extracted.rows[0].end_nav == Decimal("20")


def test_ibkr_spb8_unsupported_transfer_asset_category_warns_and_skips() -> None:
    extracted = _extract(_security_rows(end_qty="10", transfer_direction="In", transfer_qty="10", transfer_asset="Options"))

    assert extracted.rows[0].start_nav == Decimal("10")
    assert any("неподдържан Asset Category" in warning and "Options" in warning for warning in extracted.warnings)


def test_ibkr_spb8_unsupported_transfer_direction_warns_and_skips() -> None:
    extracted = _extract(_security_rows(end_qty="10", transfer_direction="Sideways", transfer_qty="10"))

    assert extracted.rows[0].start_nav == Decimal("10")
    assert any("неподдържана Direction" in warning and "Sideways" in warning for warning in extracted.warnings)


def test_ibkr_spb8_invalid_transfer_quantity_warns_and_skips() -> None:
    extracted = _extract(_security_rows(end_qty="10", transfer_direction="In", transfer_qty="not-a-number"))

    assert extracted.rows[0].start_nav == Decimal("10")
    assert any("невалидно Qty" in warning and "not-a-number" in warning for warning in extracted.warnings)


def test_ibkr_spb8_unresolved_transfer_instrument_warns_and_skips() -> None:
    extracted = _extract(_security_rows(end_qty="10", transfer_direction="In", transfer_qty="10", transfer_symbol="UNKNOWN"))

    assert extracted.rows[0].start_nav == Decimal("10")
    assert any("инструментът не може да бъде разпознат" in warning and "UNKNOWN" in warning for warning in extracted.warnings)


def test_ibkr_spb8_transfer_instrument_without_isin_warns_and_skips() -> None:
    rows = _security_rows(end_qty="10", transfer_direction="In", transfer_qty="10", transfer_symbol="NOISIN")
    listings = {
        "VWCE": _listing(),
        "NOISIN": _listing(symbol="NOISIN", isin=""),
    }

    extracted = _extract(rows, listings=listings)

    assert extracted.rows[0].start_nav == Decimal("10")
    assert any("инструментът няма ISIN" in warning and "NOISIN" in warning for warning in extracted.warnings)


def test_ibkr_spb8_recognized_merger_delta_reconstructs_start_quantity() -> None:
    rows = [
        *_security_rows(end_qty="15", trade_qty="3"),
        ["Corporate Actions", "Header", "Asset Category", "Symbol", "Quantity"],
        ["Corporate Actions", "Data", "Stocks", "VWCE", "1"],
    ]
    active_headers, _seen_headers = _build_active_headers(rows)

    extracted = extract_ibkr_spb8_rows(
        rows=rows,
        active_headers=active_headers,
        listings={"VWCE": _listing()},
        account_name="U123",
        corporate_actions_present=False,
        corporate_action_delta_by_isin={"IE00BK5BQT80": Decimal("1")},
    )

    assert extracted.rows[0].start_nav == Decimal("11")
    assert extracted.rows[0].end_nav == Decimal("15")


def test_ibkr_spb8_unsupported_corporate_actions_leave_start_quantity_empty() -> None:
    rows = [
        *_security_rows(end_qty="15", trade_qty="3"),
        ["Corporate Actions", "Header", "Asset Category", "Symbol", "Quantity"],
        ["Corporate Actions", "Data", "Stocks", "VWCE", "1"],
    ]
    active_headers, _seen_headers = _build_active_headers(rows)

    extracted = extract_ibkr_spb8_rows(
        rows=rows,
        active_headers=active_headers,
        listings={"VWCE": _listing()},
        account_name="U123",
        corporate_actions_present=True,
        corporate_action_delta_by_isin={"IE00BK5BQT80": Decimal("1")},
    )

    assert extracted.rows[0].start_nav is None
    assert extracted.rows[0].end_nav == Decimal("15")
