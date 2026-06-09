from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from integrations.ibkr.activity_statement_analyzer import analyze_ibkr_activity_statement
from integrations.shared.rendering.common import TECHNICAL_DETAILS_SEPARATOR
import pytest
import report_analyzer

from tests.integrations.ibkr import support as h

_read_rows = h._read_rows
_run = h._run
_write_rows = h._write_rows


def _cfd_rows() -> list[list[str]]:
    return [
        ["Statement", "Header", "Field", "Value"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch", "Description", "ISIN"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2", "Bayerische Motoren Werke AG", "DE0005190003"],
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description", "Conid", "Listing Exch", "Multiplier", "Expiry", "Code"],
        ["Financial Instrument Information", "Data", "CFDs", "FXI", "USD FXI", "134770997", "", "1", "5/1/2036", ""],
        [
            "Trades",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Exchange",
            "Quantity",
            "T. Price",
            "C. Price",
            "Notional Value",
            "Comm/Fee",
            "Basis",
            "Realized P/L",
            "MTM P/L",
            "Code",
        ],
        ["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", "2025-01-10, 10:00:00", "THEUSCFD", "10", "8", "8", "-80", "-1", "80", "0", "0", "O"],
        ["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", "2025-01-20, 10:00:00", "THEUSCFD", "-10", "10", "10", "100", "-1", "-80", "19", "0", "C"],
        ["Trades", "Data", "ClosedLot", "CFDs", "EUR", "FXI", "2025-01-10", "", "10", "8", "", "", "", "80", "19", "", "ST"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Currency", "Summary Quantity", "Cost Basis", "DataDiscriminator"],
        ["Open Positions", "Data", "CFDs", "FXI", "EUR", "10", "100", "Summary"],
        ["Fees", "Header", "Subtitle", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
    ]


def _insert_before_section(rows: list[list[str]], section: str, new_rows: list[list[str]]) -> None:
    index = next(idx for idx, row in enumerate(rows) if row[:1] == [section])
    rows[index:index] = new_rows


def _add_eccc_listing(rows: list[list[str]]) -> None:
    index = next(idx for idx, row in enumerate(rows) if row[:2] == ["Financial Instrument Information", "Data"] and row[2] == "Stocks")
    rows.insert(
        index + 1,
        ["Financial Instrument Information", "Data", "Stocks", "ECCC", "NYSE", "Eagle Point Credit Co", "US2698097035"],
    )


def _add_short_stock_closed_range(rows: list[list[str]], *, symbol: str = "ECCC", close_date: str = "2025-12-01") -> None:
    _insert_before_section(
        rows,
        "Open Positions",
        [
            ["Trades", "Data", "Trade", "Stocks", "USD", symbol, "2025-11-01, 09:30:00", "NYSE", "-10", "0", "0", "0", "0", "0", "0", "0", "O"],
            ["Trades", "Data", "Trade", "Stocks", "USD", symbol, f"{close_date}, 09:30:00", "NYSE", "10", "0", "0", "0", "0", "0", "0", "0", "C"],
            ["Trades", "Data", "ClosedLot", "Stocks", "USD", symbol, "11/01/2025", "", "-10", "0", "", "", "", "0", "0", "", "ST"],
        ],
    )


def _add_short_stock_open_range(rows: list[list[str]], *, symbol: str = "ECCC") -> None:
    _insert_before_section(
        rows,
        "Open Positions",
        [["Trades", "Data", "Trade", "Stocks", "USD", symbol, "2025-11-01, 09:30:00", "NYSE", "-10", "0", "0", "0", "0", "0", "0", "0", "O"]],
    )
    index = next(idx for idx, row in enumerate(rows) if row[:2] == ["Open Positions", "Data"])
    rows.insert(index, ["Open Positions", "Data", "Stocks", symbol, "USD", "-10", "0", "Summary"])


def _add_short_cfd_closed_range(rows: list[list[str]], *, close_date: str = "2025-12-01") -> None:
    _insert_before_section(
        rows,
        "Open Positions",
        [
            ["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", "2025-11-01, 09:30:00", "THEUSCFD", "-10", "0", "0", "0", "0", "0", "0", "0", "O"],
            ["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", f"{close_date}, 09:30:00", "THEUSCFD", "10", "0", "0", "0", "0", "0", "0", "0", "C"],
            ["Trades", "Data", "ClosedLot", "CFDs", "EUR", "FXI", "11/01/2025", "", "-10", "0", "", "", "", "0", "0", "", "ST"],
        ],
    )


def _add_short_cfd_open_range(rows: list[list[str]]) -> None:
    _insert_before_section(
        rows,
        "Open Positions",
        [["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", "2025-11-01, 09:30:00", "THEUSCFD", "-10", "0", "0", "0", "0", "0", "0", "0", "O"]],
    )
    index = next(idx for idx, row in enumerate(rows) if row[:2] == ["Open Positions", "Data"])
    rows.insert(index, ["Open Positions", "Data", "CFDs", "FXI", "EUR", "-10", "0", "Summary"])


def _without_open_cfd_position(rows: list[list[str]]) -> list[list[str]]:
    return [row for row in rows if not (row[:2] == ["Open Positions", "Data"] and row[2] == "CFDs")]


def _with_fees_review_status_header(rows: list[list[str]]) -> None:
    header = next(row for row in rows if row[:2] == ["Fees", "Header"])
    if "Review Status" not in header:
        header.append("Review Status")


def _add_cfd_financing_fee(
    rows: list[list[str]],
    *,
    fee_date: str = "2025-01-24",
    description: str | None = None,
    amount: str = "-2",
    review_status: str = "",
) -> None:
    if description is None:
        parsed_date = date.fromisoformat(fee_date)
        description = f"Long CFD Interest for {parsed_date.strftime('%d-%b-%Y').upper()}"
    row = ["Fees", "Data", "Other Fees", "EUR", fee_date, description, amount]
    if review_status:
        _with_fees_review_status_header(rows)
        header = next(item for item in rows if item[:2] == ["Fees", "Header"])
        row.extend([""] * (len(header) - len(row)))
        row[2 + header[2:].index("Review Status")] = review_status
    rows.insert(-1, row)


def _fees_output_row(result, description_part: str) -> tuple[list[str], list[str]]:
    output_rows = _read_rows(result.output_csv_path)
    header = next(row for row in output_rows if row[:2] == ["Fees", "Header"])
    row = next(row for row in output_rows if row[:2] == ["Fees", "Data"] and description_part in ",".join(row))
    return header, row


def _add_negative_symbol_pil(rows: list[list[str]], *, review_status: str = "") -> None:
    row = ["Dividends", "Data", "USD", "2025-11-29", "ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)", "-10"]
    if review_status:
        header = next(item for item in rows if item[:2] == ["Dividends", "Header"])
        if "Review Status" not in header:
            header.append("Review Status")
        row.extend([""] * (len(header) - len(row)))
        row[2 + header[2:].index("Review Status")] = review_status
    rows.append(row)


def _add_negative_no_symbol_pil(rows: list[list[str]], *, review_status: str = "") -> None:
    row = ["Dividends", "Data", "USD", "2025-11-29", "Payment in Lieu of Dividend (Ordinary Dividend)", "-10"]
    if review_status:
        header = next(item for item in rows if item[:2] == ["Dividends", "Header"])
        if "Review Status" not in header:
            header.append("Review Status")
        row.extend([""] * (len(header) - len(row)))
        row[2 + header[2:].index("Review Status")] = review_status
    rows.append(row)


def _add_dividend_accrual(
    rows: list[list[str]],
    *,
    asset_category: str = "CFDs",
    symbol: str = "FXI",
    currency: str = "USD",
    ex_date: str = "2025-11-12",
    pay_date: str = "2025-11-29",
    quantity: str = "-10",
    amount: str = "-10",
) -> None:
    if not any(row[:2] == ["Change in Dividend Accruals", "Header"] for row in rows):
        _insert_before_section(
            rows,
            "Dividends",
            [
                [
                    "Change in Dividend Accruals",
                    "Header",
                    "Asset Category",
                    "Currency",
                    "Symbol",
                    "Date",
                    "Ex Date",
                    "Pay Date",
                    "Quantity",
                    "Tax",
                    "Fee",
                    "Gross Rate",
                    "Gross Amount",
                    "Net Amount",
                    "Code",
                ],
            ],
        )
    rows.append(
        [
            "Change in Dividend Accruals",
            "Data",
            asset_category,
            currency,
            symbol,
            ex_date,
            ex_date,
            pay_date,
            quantity,
            "0",
            "0",
            "1",
            amount,
            amount,
            "Po",
        ]
    )


def _dividend_output_row(result, description_part: str) -> tuple[list[str], list[str]]:
    output_rows = _read_rows(result.output_csv_path)
    header = next(row for row in output_rows if row[:2] == ["Dividends", "Header"])
    row = next(row for row in output_rows if row[:2] == ["Dividends", "Data"] and description_part in ",".join(row))
    return header, row


def test_financial_instrument_information_supports_stock_and_cfd_headers(tmp_path: Path) -> None:
    result = _run(tmp_path, _cfd_rows(), mode="listing_exchange")

    assert result.summary.cfd_trade_rows == 1
    assert result.summary.cfd_open_position_rows == 1
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0
    assert result.summary.appendix_8_part1_rows == []
    assert result.summary.spb8_rows == []
    assert any("CFD позициите не се включват в СПБ-8" in note for note in result.summary.spb8_notes)


def test_cfd_trade_routes_to_appendix5_code508_without_eu_exemption(tmp_path: Path) -> None:
    result = _run(tmp_path, _cfd_rows(), mode="listing_exchange")

    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "код 508" in text
    assert "CFD сделките са третирани като финансови инструменти" in text
    assert "CFD позициите не се декларират в Приложение 8" in text
    assert "При CFD не се използва пълният notional/номинал на договора" in text
    assert "CFD trades policy: Appendix 5 / Table 2 / code 508" in text
    assert "CFD SPB-8 policy: excluded_from_spb8" in text


def test_negative_cfd_realized_pl_maps_to_appendix5_acquisition_side(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows[7][14] = "-7"
    rows[8][14] = "-7"

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.appendix_5.sale_price_eur == Decimal("0")
    assert result.summary.appendix_5.purchase_eur == Decimal("7")
    assert result.summary.appendix_5.wins_eur == Decimal("0")
    assert result.summary.appendix_5.losses_eur == Decimal("7")


def test_cfd_closedlot_realized_pl_uses_closing_trade_fx_date(tmp_path: Path) -> None:
    rows = _cfd_rows()
    for row in rows:
        if len(row) > 4 and row[0] == "Trades" and row[1] == "Data" and row[3] == "CFDs":
            row[4] = "USD"
    rows[7][14] = "10"
    rows[8][14] = "10"
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)

    def fx_provider(currency: str, on_date: date) -> Decimal:
        if currency == "EUR":
            return Decimal("1")
        if currency == "USD" and on_date == date(2025, 1, 10):
            return Decimal("0.5")
        if currency == "USD" and on_date == date(2025, 1, 20):
            return Decimal("0.8")
        raise AssertionError(f"unexpected FX request: {currency} {on_date}")

    result = analyze_ibkr_activity_statement(
        input_csv=input_csv,
        tax_year=2025,
        tax_exempt_mode="listing_exchange",
        output_dir=tmp_path / "out",
        fx_rate_provider=fx_provider,
    )

    assert result.summary.appendix_5.sale_price_eur == Decimal("8.0")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.appendix_5.wins_eur == Decimal("8.0")


def test_cfd_financing_closed_by_year_end_is_netted_in_position_aware_mode(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-15")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_rows == 1
    assert result.summary.cfd_financing_net_rows == 1
    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("2")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.appendix_5.losses_eur == Decimal("2")
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    assert result.summary.cfd_financing_decisions[0].auto_status == "NET"
    header, row = _fees_output_row(result, "Long CFD Interest")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Review Status"]] == ""
    assert row[2 + idx["Auto Status"]] == "NET"
    assert row[2 + idx["Tax Status"]] == "NET"
    assert "all CFD positions active on the financing date were closed by year-end" in row[2 + idx["Tax Treatment Reason"]]
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Режим за CFD financing / CFD interest: position-aware." in text
    assert "окончателно решение NET" in text
    assert "CFD financing policy: position-aware" in text
    assert "Appendix 5 non-trade adjustment rows not counted as trades: 1" in text
    assert "Appendix 5 CFD financing adjustment rows not counted as trades: 1" in text


def test_modified_csv_annotates_cfd_financing_fee_rows(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-15")
    _add_cfd_financing_fee(rows, fee_date="2024-01-24", amount="-5")

    result = _run(tmp_path, rows, mode="listing_exchange")

    output_rows = _read_rows(result.output_csv_path)
    fees_header = next(row for row in output_rows if row[:2] == ["Fees", "Header"])
    fees_rows = [row for row in output_rows if row[:2] == ["Fees", "Data"]]
    assert "" not in fees_header
    idx = {c: i for i, c in enumerate(fees_header[2:])}
    current_year = next(row for row in fees_rows if "2025" in row[2 + idx["Date"]])
    previous_year = next(row for row in fees_rows if "2024" in row[2 + idx["Date"]])

    assert current_year[2 + idx["Amount (EUR)"]] == "-2.00000000"
    assert current_year[2 + idx["Appendix Target"]] == "APPENDIX_5"
    assert "all CFD positions active on the financing date were closed by year-end" in current_year[2 + idx["Tax Treatment Reason"]]
    assert current_year[2 + idx["Tax Year Scope"]] == "IN_TAX_YEAR"
    assert current_year[2 + idx["Review Status"]] == ""
    assert current_year[2 + idx["Auto Status"]] == "NET"
    assert current_year[2 + idx["Tax Status"]] == "NET"
    assert previous_year[2 + idx["Amount (EUR)"]] == "-5.00000000"
    assert previous_year[2 + idx["Appendix Target"]] == "IGNORED"
    assert previous_year[2 + idx["Auto Status"]] == "IGNORE"
    assert previous_year[2 + idx["Review Status"]] == ""
    assert previous_year[2 + idx["Tax Status"]] == "IGNORE"
    assert "Fees Date is outside the selected tax year" in previous_year[2 + idx["Tax Treatment Reason"]]
    assert previous_year[2 + idx["Tax Year Scope"]] == "OUTSIDE_TAX_YEAR"
    assert result.summary.appendix_5.purchase_eur == Decimal("2")


def test_cfd_financing_positive_is_netted_when_closed_by_year_end(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-15", description="Short CFD Interest for 15-JAN-2025", amount="3")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.appendix_5.sale_price_eur == Decimal("22")
    assert result.summary.appendix_5.wins_eur == Decimal("22")
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_6_code_606_eur == Decimal("0")


def test_cfd_financing_ignore_mode_excludes_positive_and_negative(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_cfd_financing_fee(rows, amount="-2")
    _add_cfd_financing_fee(rows, fee_date="2025-01-25", description="CFD Financing for 25-JAN-2025", amount="3")

    result = _run(tmp_path, rows, mode="listing_exchange", cfd_financing_mode="ignore")

    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.appendix_5.losses_eur == Decimal("0")
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    assert result.summary.cfd_financing_ignore_rows == 2
    assert result.summary.cfd_financing_negative_skipped_eur == Decimal("2")
    header, row = _fees_output_row(result, "CFD Financing")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Auto Status"]] == "IGNORE"
    assert row[2 + idx["Review Status"]] == ""
    assert row[2 + idx["Tax Status"]] == "IGNORE"
    assert row[2 + idx["Tax Treatment Reason"]] == "Excluded from CFD taxable result because CFD financing mode is ignore."
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Режим за CFD financing / CFD interest: ignore." in text
    assert "Избран е режим ignore за CFD financing" in text
    assert "CFD financing policy: ignore" in text


def test_cfd_financing_open_at_year_end_is_deferred(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_cfd_financing_fee(rows, fee_date="2025-01-24")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_defer_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.cfd_financing_decisions[0].auto_status == "DEFER"
    header, row = _fees_output_row(result, "Long CFD Interest")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Review Status"]] == ""
    assert row[2 + idx["Auto Status"]] == "DEFER"
    assert row[2 + idx["Tax Status"]] == "DEFER"
    assert "remained open at year-end" in row[2 + idx["Tax Treatment Reason"]]
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "не са били затворени до края на данъчната година" in text
    assert "Копирайте оригиналните редове от предходната година без промени" in text


def test_cfd_financing_without_trade_date_match_is_accepted_and_netted(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-24")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_net_rows == 1
    assert result.summary.cfd_financing_review_rows == 0
    assert result.summary.cfd_financing_unmatched_by_trade_date_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("2")
    decision = result.summary.cfd_financing_decisions[0]
    assert decision.auto_status == "NET"
    assert decision.assignment_status == "unmatched_by_trade_date_approximation"
    assert "accepted but not matched to a trade-date-open CFD position" in decision.tax_status
    header, row = _fees_output_row(result, "Long CFD Interest")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Review Status"]] == ""
    assert row[2 + idx["Auto Status"]] == "NET"
    assert row[2 + idx["Tax Status"]] == "NET"
    assert "accepted but not matched to a trade-date-open CFD position" in row[2 + idx["Tax Treatment Reason"]]
    main_text = result.declaration_txt_path.read_text(encoding="utf-8").split(TECHNICAL_DETAILS_SEPARATOR, 1)[0]
    assert "CFD financing реда, които изискват ръчен преглед" not in main_text
    assert "Проверете дали в предходни години има CFD такси за финансиране" in main_text


def test_prior_year_deferred_cfd_financing_reminder_is_shown_for_cfd_activity(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_trade_rows > 0
    assert result.summary.cfd_financing_defer_rows == 0
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Проверете дали в предходни години има CFD такси за финансиране" in text


def test_cfd_financing_fees_date_drives_assignment_and_tax_year(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(
        rows,
        fee_date="2025-01-30",
        description="Long CFD Interest for 15-JAN-2025",
    )

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.cfd_financing_decisions[0]
    assert decision.date == date(2025, 1, 30)
    assert decision.embedded_fee_date == "2025-01-15"
    assert decision.embedded_fee_date_status == "DIFFERS_FROM_FEES_DATE"
    assert decision.assignment_status == "unmatched_by_trade_date_approximation"
    assert decision.auto_status == "NET"
    assert result.summary.cfd_financing_net_rows == 1


def test_cfd_financing_settlement_drift_without_trade_date_match_is_accepted(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(
        rows,
        fee_date="2025-01-22",
        description="Long CFD Interest for 22-JAN-2025",
    )

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.cfd_financing_decisions[0]
    assert decision.auto_status == "NET"
    assert decision.assignment_status == "unmatched_by_trade_date_approximation"
    assert result.summary.cfd_financing_net_rows == 1
    assert result.summary.cfd_financing_review_rows == 0
    assert result.summary.appendix_5.purchase_eur == Decimal("2")


def test_cfd_fee_containing_cfd_without_embedded_date_is_accepted(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-24", description="Daily CFD financing charge")

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.cfd_financing_decisions[0]
    assert decision.auto_status == "NET"
    assert decision.assignment_status == "unmatched_by_trade_date_approximation"
    assert decision.embedded_fee_date == ""
    assert decision.embedded_fee_date_status.startswith("NOT_FOUND")
    assert result.summary.cfd_financing_net_rows == 1
    assert result.summary.cfd_financing_review_rows == 0
    header, row = _fees_output_row(result, "Daily CFD")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Tax Status"]] == "NET"
    assert row[2 + idx["Appendix Target"]] == "APPENDIX_5"


def test_all_fees_rows_containing_cfd_are_cfd_financing_candidates(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-24", description="Monthly CFD carrying fee")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_detected_rows == 1
    assert result.summary.cfd_financing_rows == 1
    assert result.summary.cfd_financing_net_rows == 1


def test_cfd_financing_malformed_amount_fails_fast(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-24", amount="not-a-number")

    with pytest.raises(Exception, match="Amount"):
        _run(tmp_path, rows, mode="listing_exchange")


def test_cfd_financing_missing_currency_fails_fast(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2025-01-24")
    fee_row = next(row for row in rows if row[:2] == ["Fees", "Data"])
    fee_row[3] = ""

    with pytest.raises(Exception):
        _run(tmp_path, rows, mode="listing_exchange")


def test_cfd_financing_mixed_closed_and_open_ranges_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_cfd_financing_fee(rows, fee_date="2025-01-15")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_review_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    decision = result.summary.cfd_financing_decisions[0]
    assert decision.auto_status == "REVIEW"
    assert "overlaps both closed and open CFD positions" in decision.tax_status
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    main_text = text.split(TECHNICAL_DETAILS_SEPARATOR, 1)[0]
    assert "Auto Status = REVIEW" in main_text
    assert "Review Status" in main_text
    assert "review_status=-" not in main_text


def test_cfd_financing_position_closing_after_tax_year_is_deferred(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _insert_before_section(
        rows,
        "Open Positions",
        [
            ["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", "2025-10-01, 09:30:00", "THEUSCFD", "10", "0", "0", "0", "0", "0", "0", "0", "O"],
            ["Trades", "Data", "Trade", "CFDs", "EUR", "FXI", "2026-01-15, 09:30:00", "THEUSCFD", "-10", "0", "0", "0", "0", "0", "0", "0", "C"],
            ["Trades", "Data", "ClosedLot", "CFDs", "EUR", "FXI", "2025-10-01", "", "10", "0", "", "", "", "0", "0", "", "ST"],
        ],
    )
    _add_cfd_financing_fee(rows, fee_date="2025-10-24")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_defer_rows == 1
    assert result.summary.cfd_financing_decisions[0].auto_status == "DEFER"
    assert result.summary.appendix_5.purchase_eur == Decimal("0")


def test_cfd_financing_always_net_mode_includes_rows_and_warns(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_cfd_financing_fee(rows, amount="-2")

    result = _run(tmp_path, rows, mode="listing_exchange", cfd_financing_mode="always-net")

    assert result.summary.cfd_financing_net_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("2")
    header, row = _fees_output_row(result, "Long CFD Interest")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Auto Status"]] == "NET"
    assert row[2 + idx["Review Status"]] == ""
    assert row[2 + idx["Tax Status"]] == "NET"
    assert row[2 + idx["Tax Treatment Reason"]] == "Included in CFD taxable result because CFD financing mode is always-net."
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Внимание: избран е режим always-net за CFD financing" in text


def test_cfd_financing_review_status_override_wins_and_is_preserved(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_cfd_financing_fee(rows, fee_date="2025-01-24", review_status="NET")

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.cfd_financing_decisions[0]
    assert decision.auto_status == "DEFER"
    assert decision.review_status == "NET"
    assert decision.final_status == "NET"
    assert result.summary.appendix_5.purchase_eur == Decimal("2")
    header, row = _fees_output_row(result, "Long CFD Interest")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Review Status"]] == "NET"
    assert row[2 + idx["Auto Status"]] == "DEFER"
    assert row[2 + idx["Tax Status"]] == "NET"
    assert "User override applied from Review Status" in row[2 + idx["Tax Treatment Reason"]]


def test_symbol_negative_pil_matching_short_stock_closed_by_year_end_is_netted(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    _add_short_stock_closed_range(rows)
    _add_negative_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_rows == 1
    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("9")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.appendix_5.losses_eur == Decimal("9")
    assert result.summary.appendix_5.rows == 2
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "NET"
    header, row = _dividend_output_row(result, "Payment in Lieu")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Auto Status"]] == "NET"
    assert row[2 + idx["Status"]] == "NET"
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Отрицателният Payment in Lieu of Dividend (PIL) е третиран като position-related cost/adjustment" in text
    assert "IBKR — проверки за Payment in Lieu" in text
    assert "Проверете всички предходни години, не само непосредствено предходната" in text
    assert "PIL policy: position-aware" in text
    assert "Appendix 5 non-trade adjustment rows not counted as trades: 1" in text
    assert "Appendix 5 negative PIL adjustment rows not counted as trades: 1" in text


def test_synthetic_sample_symbol_negative_pil_uses_linked_accrual_date(tmp_path: Path) -> None:
    rows = _read_rows(Path("examples/inputs/ibkr_activity_statement_sample_synthetic.csv"))
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, rows)

    result = analyze_ibkr_activity_statement(
        input_csv=input_csv,
        tax_year=2025,
        tax_exempt_mode="listing_exchange",
        output_dir=tmp_path / "out",
        fx_rate_provider=lambda currency, on_date: Decimal("1"),  # noqa: ARG005
    )

    header, row = _dividend_output_row(result, "ECCC(US2698097035) Payment in Lieu")
    assert row[2 + header[2:].index("Auto Status")] == "NET"
    assert row[2 + header[2:].index("Status")] == "NET"
    assert "linked dividend accrual Ex Date 2025-11-12" in row[2 + header[2:].index("Tax Status")]
    decision = next(item for item in result.summary.negative_pil_decisions if item.row_number == 850)
    assert decision.final_status == "NET"
    assert any("[2025-11-11, 2025-11-12]" in candidate for candidate in decision.candidate_ranges)


def test_symbol_negative_pil_matching_short_stock_open_at_year_end_is_deferred(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    _add_short_stock_open_range(rows)
    _add_negative_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_defer_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "DEFER"
    header, row = _dividend_output_row(result, "Payment in Lieu")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Auto Status"]] == "DEFER"
    assert row[2 + idx["Status"]] == "DEFER"


def test_symbol_negative_pil_without_matching_short_stock_range_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    _add_negative_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_review_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "REVIEW"
    assert "No matching short-security exposure" in result.summary.negative_pil_decisions[0].tax_status


def test_symbol_negative_pil_mixed_closed_and_open_short_stock_ranges_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    _add_short_stock_closed_range(rows)
    _add_short_stock_open_range(rows)
    _add_negative_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_review_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "REVIEW"
    assert "ambiguous" in result.summary.negative_pil_decisions[0].tax_status


def test_no_symbol_negative_pil_unique_cfd_accrual_uses_symbol_and_ex_date(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)
    _add_dividend_accrual(rows, symbol="FXI", ex_date="2025-11-12")
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.negative_pil_decisions[0]
    assert decision.auto_status == "NET"
    assert decision.accrual_link_status == "unique"
    assert decision.accrual_asset_category == "CFDs"
    assert decision.accrual_asset_classification == "cfd"
    assert decision.accrual_symbol == "FXI"
    assert decision.accrual_ex_date == date(2025, 11, 12)
    assert decision.matching_date_source == "linked_dividend_accrual_ex_date"
    assert "unique CFD-mapped dividend-accrual event with Symbol FXI" in decision.tax_status
    assert any("short-cfd FXI/- [2025-11-01, 2025-12-01]" in item for item in decision.candidate_ranges)


def test_previous_year_deferred_pil_reminder_shows_when_short_exposure_closes_without_current_pil(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_rows == 0
    assert result.summary.negative_pil_closed_exposure_ranges > 0
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "IBKR — проверки за Payment in Lieu" in text
    assert "Проверете всички предходни години, не само непосредствено предходната" in text


def test_previous_year_deferred_pil_reminder_does_not_show_for_current_pil_without_closed_exposure(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_open_range(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_rows == 1
    assert result.summary.negative_pil_closed_exposure_ranges == 0
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Проверете всички предходни години" not in text


def test_no_symbol_negative_pil_unique_cfd_accrual_closed_by_year_end_is_netted(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)
    _add_dividend_accrual(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_net_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("9")
    assert result.summary.negative_pil_decisions[0].auto_status == "NET"
    assert "all matching ranges were closed by year-end" in result.summary.negative_pil_decisions[0].tax_status


def test_no_symbol_negative_pil_unique_cfd_accrual_open_at_year_end_is_deferred(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_open_range(rows)
    _add_dividend_accrual(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_defer_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "DEFER"
    assert "accrual Ex Date but remained open at year-end" in result.summary.negative_pil_decisions[0].tax_status


def test_no_symbol_negative_pil_unique_cfd_accrual_without_short_cfd_exposure_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)
    _add_dividend_accrual(rows, ex_date="2025-10-15")
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_review_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "REVIEW"
    assert "no matching short-CFD exposure was found on the accrual Ex Date" in result.summary.negative_pil_decisions[0].tax_status


def test_no_symbol_negative_pil_multiple_matching_accrual_events_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)
    _add_dividend_accrual(rows, symbol="FXI", ex_date="2025-11-12")
    _add_dividend_accrual(rows, symbol="KWEB", ex_date="2025-11-13")
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.negative_pil_decisions[0]
    assert result.summary.pil_negative_review_rows == 1
    assert decision.auto_status == "REVIEW"
    assert decision.accrual_link_status == "multiple"
    assert "multiple possible dividend-accrual events matched" in decision.tax_status


def test_no_symbol_negative_pil_duplicate_accrual_rows_are_deduped(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)
    _add_dividend_accrual(rows)
    _add_dividend_accrual(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_net_rows == 1
    assert result.summary.negative_pil_decisions[0].auto_status == "NET"
    assert result.summary.negative_pil_decisions[0].accrual_link_status == "unique"


def test_no_symbol_negative_pil_unsupported_accrual_asset_category_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_dividend_accrual(rows, asset_category="Warrants", symbol="WXYZ")
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    decision = result.summary.negative_pil_decisions[0]
    assert result.summary.pil_negative_review_rows == 1
    assert decision.auto_status == "REVIEW"
    assert decision.accrual_asset_category == "Warrants"
    assert decision.accrual_asset_classification == "unsupported"
    assert "unsupported or ambiguous" in decision.tax_status


def test_no_symbol_negative_pil_matching_short_cfd_closed_by_year_end_is_netted_with_heuristic(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_closed_range(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_net_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("9")
    assert result.summary.negative_pil_decisions[0].auto_status == "NET"
    assert "Heuristic no-symbol negative PIL" in result.summary.negative_pil_decisions[0].tax_status


def test_no_symbol_negative_pil_matching_short_cfd_open_at_year_end_is_deferred(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_short_cfd_open_range(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_defer_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "DEFER"
    assert "most likely short CFD / derivative" in result.summary.negative_pil_decisions[0].tax_status


def test_no_symbol_negative_pil_without_matching_short_cfd_range_requires_review(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.pil_negative_review_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.negative_pil_decisions[0].auto_status == "REVIEW"


def test_defer_and_review_negative_pil_rows_are_visible_in_csv_and_main_report(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    _add_short_stock_open_range(rows)
    _add_negative_symbol_pil(rows)
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange")

    output_rows = _read_rows(result.output_csv_path)
    header = next(row for row in output_rows if row[:2] == ["Dividends", "Header"])
    idx = {c: i for i, c in enumerate(header[2:])}
    pil_rows = [row for row in output_rows if row[:2] == ["Dividends", "Data"] and "Payment in Lieu" in ",".join(row)]
    statuses = {row[2 + idx["Auto Status"]] for row in pil_rows}
    assert {"DEFER", "REVIEW"} <= statuses
    assert all(row[2 + idx["Tax Status"]] for row in pil_rows)

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Има отрицателни Payment in Lieu редове в CFD/PIL обработката" in text
    assert 'Подробните инструкции са в секцията "Изискват ръчен преглед"' in text
    assert "Засегнати редове:" in text
    assert "Открити са редове, които изискват ръчна проверка или са отложени" not in text
    assert "NEGATIVE_PIL REVIEW: rows" not in text
    assert "NEGATIVE_PIL DEFER: rows" not in text


def test_negative_pil_review_status_override_wins_over_auto_status(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    _add_short_stock_open_range(rows)
    _add_negative_symbol_pil(rows, review_status="NET")

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.negative_pil_decisions[0].auto_status == "DEFER"
    assert result.summary.negative_pil_decisions[0].final_status == "NET"
    assert result.summary.appendix_5.purchase_eur == Decimal("9")


def test_negative_pil_always_net_mode_forces_auto_status_net(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_negative_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange", negative_pil_mode="always-net")

    assert result.summary.negative_pil_decisions[0].auto_status == "NET"
    assert result.summary.appendix_5.purchase_eur == Decimal("9")
    header, row = _dividend_output_row(result, "Payment in Lieu")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Auto Status"]] == "NET"
    assert row[2 + idx["Tax Status"]] == "Negative PIL netted because --negative-pil-mode=always-net."


def test_negative_pil_ignore_mode_skips_payment_in_lieu(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_negative_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange", negative_pil_mode="ignore")

    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.negative_pil_decisions[0].auto_status == "IGNORE"
    assert result.summary.pil_negative_skipped_eur == Decimal("9")
    header, row = _dividend_output_row(result, "Payment in Lieu")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Auto Status"]] == "IGNORE"
    assert row[2 + idx["Tax Status"]] == "Negative PIL ignored because --negative-pil-mode=ignore."
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Режим за отрицателен Payment in Lieu of Dividend (PIL): ignore." in text
    assert "Има отрицателни Payment in Lieu редове, маркирани за игнориране" in text
    assert "Тези редове не са включени в декларацията." in text
    assert "Засегнати редове:" in text
    assert "само тези редове не са включени в декларацията" not in text
    assert "Отрицателният PIL не е включен в декларацията." not in text
    assert "PIL policy: ignore" in text


def test_negative_pil_ignore_mode_still_respects_review_status_net(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_negative_symbol_pil(rows, review_status="NET")

    result = _run(tmp_path, rows, mode="listing_exchange", negative_pil_mode="ignore")

    decision = result.summary.negative_pil_decisions[0]
    assert decision.auto_status == "IGNORE"
    assert decision.review_status == "NET"
    assert decision.final_status == "NET"
    assert result.summary.pil_negative_net_rows == 1
    assert result.summary.pil_negative_ignore_rows == 0
    assert result.summary.appendix_5.purchase_eur == Decimal("9")
    header, row = _dividend_output_row(result, "Payment in Lieu")
    idx = {c: i for i, c in enumerate(header[2:])}
    assert row[2 + idx["Review Status"]] == "NET"
    assert row[2 + idx["Auto Status"]] == "IGNORE"
    assert row[2 + idx["Status"]] == "NET"
    assert (
        "Review Status override NET applied; final status is NET. "
        "Auto decision was IGNORE: Negative PIL ignored because --negative-pil-mode=ignore."
    ) in row[2 + idx["Tax Status"]]


def test_negative_pil_ignore_note_row_refs_use_final_ignored_rows_only(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_negative_symbol_pil(rows, review_status="NET")
    _add_negative_no_symbol_pil(rows)

    result = _run(tmp_path, rows, mode="listing_exchange", negative_pil_mode="ignore")

    assert [decision.final_status for decision in result.summary.negative_pil_decisions] == ["NET", "IGNORE"]
    ignored_row = result.summary.negative_pil_decisions[1].row_number
    overridden_row = result.summary.negative_pil_decisions[0].row_number
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert f"Засегнати редове: {ignored_row}." in text
    assert f"Засегнати редове: {overridden_row}" not in text


def test_positive_payment_in_lieu_goes_to_appendix8_as_dividend_like_income(tmp_path: Path) -> None:
    rows = _cfd_rows()
    _add_eccc_listing(rows)
    rows.append(["Dividends", "Data", "USD", "2025-11-29", "ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)", "10"])

    result = _run(tmp_path, rows, mode="listing_exchange", negative_pil_mode="ignore")

    assert result.summary.appendix_6_positive_pil_eur == Decimal("0")
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    assert result.summary.pil_appendix8_rows == 1
    assert result.summary.appendix_8_output_rows[0].gross_dividend_eur == Decimal("9")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert 'IBKR редове "Payment in Lieu of Dividend" са третирани като дивидентоподобен доход' in text
    assert "Positive PIL policy: appendix_6_code_606" not in text


def test_readme_documents_negative_pil_manual_review_workflow() -> None:
    readme = Path("src/integrations/ibkr/README.md").read_text(encoding="utf-8")
    assert "## Manual review workflow" in readme
    assert "### Negative Payment in Lieu manual review" in readme
    assert "NET    -> include in current-year Appendix 5, code 508 netting" in readme
    assert "DEFER  -> do not include this year" in readme
    assert "--negative-pil-mode position-aware" in readme
    assert "--negative-pil-mode always-net" in readme
    assert "--negative-pil-mode ignore" in readme
    assert "linked ex date is used as the exposure matching date" in readme
    assert "no-symbol PIL falls back to the heuristic" in readme
    assert "Only unique accrual events are trusted" in readme
    assert "Unknown or unsupported accrual asset categories also produce `REVIEW`" in readme
    assert "Previous-year `DEFER` workflow" in readme
    assert "review all prior years, not only the immediately preceding year" in readme


def test_normal_cash_dividend_is_not_reclassified_as_payment_in_lieu(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.append(["Dividends", "Data", "USD", "2025-12-16", "BMW(DE0005190003) Cash Dividend USD 0.20 per Share (Ordinary Dividend)", "4"])

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.dividends_cash_rows == 1
    assert result.summary.pil_positive_rows == 0
    assert result.summary.appendix_8_by_country["DE"].gross_dividend_eur == Decimal("3.6")


def test_non_cfd_fees_are_not_treated_as_cfd_financing(tmp_path: Path) -> None:
    rows = _cfd_rows()
    for description in [
        "USD Debit Interest for Jan-2025",
        "Borrow Fees",
        "Market data subscription",
        "ADR Fee USD 0.02 per Share",
        "Wire Fee",
        "US Consolidated Snapshot",
    ]:
        rows.insert(-1, ["Fees", "Data", "Other Fees", "EUR", "2025-01-24", description, "-2"])

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_rows == 0
    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")


def test_outside_tax_year_cfd_financing_and_pil_are_detected_but_not_included(tmp_path: Path) -> None:
    rows = _without_open_cfd_position(_cfd_rows())
    _add_cfd_financing_fee(rows, fee_date="2024-01-24")
    rows.append(["Dividends", "Data", "USD", "2024-11-29", "ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)", "-10"])

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.cfd_financing_detected_rows == 1
    assert result.summary.cfd_financing_rows == 1
    assert result.summary.cfd_financing_outside_tax_year_rows == 1
    assert result.summary.cfd_financing_ignore_rows == 1
    assert result.summary.cfd_financing_review_rows == 0
    assert result.summary.pil_detected_rows == 1
    assert result.summary.pil_negative_rows == 0
    assert result.summary.pil_outside_tax_year_rows == 1
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "CFD financing rows detected in statement: 1" in text
    assert "CFD financing rows processed: 1" in text
    assert "CFD financing rows outside tax year in input: 1" in text
    assert "PIL rows detected in statement: 1" in text
    assert "PIL rows included in tax year: 0" in text
    assert "PIL rows outside tax year ignored: 1" in text


def test_cli_flags_for_cfd_financing_and_pil_are_supported(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, _cfd_rows())
    parser = report_analyzer.build_parser()
    args = parser.parse_args(
        [
            "ibkr",
            "--input",
            str(input_csv),
            "--tax-year",
            "2025",
            "--cfd-financing-mode",
            "ignore",
            "--negative-pil-mode",
            "ignore",
        ]
    )

    from integrations.ibkr.analyzer_definition import ANALYZER

    options = ANALYZER.build_options(args, "single", {})
    assert options["cfd_financing_mode"] == "ignore"
    assert options["negative_pil_mode"] == "ignore"
