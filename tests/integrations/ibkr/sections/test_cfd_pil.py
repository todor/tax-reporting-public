from __future__ import annotations

from decimal import Decimal
from pathlib import Path

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


def test_financial_instrument_information_supports_stock_and_cfd_headers(tmp_path: Path) -> None:
    result = _run(tmp_path, _cfd_rows(), mode="listed_symbol")

    assert result.summary.cfd_trade_rows == 1
    assert result.summary.cfd_open_position_rows == 1
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0
    assert result.summary.appendix_8_part1_rows == []
    assert result.summary.spb8_rows == []
    assert any("CFD позициите не се включват в СПБ-8" in note for note in result.summary.spb8_notes)


def test_cfd_trade_routes_to_appendix5_code508_without_eu_exemption(tmp_path: Path) -> None:
    result = _run(tmp_path, _cfd_rows(), mode="listed_symbol")

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

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.appendix_5.sale_price_eur == Decimal("0")
    assert result.summary.appendix_5.purchase_eur == Decimal("7")
    assert result.summary.appendix_5.wins_eur == Decimal("0")
    assert result.summary.appendix_5.losses_eur == Decimal("7")


def test_cfd_financing_negative_is_netted_to_appendix5_by_default(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.insert(-1, ["Fees", "Data", "Other Fees", "EUR", "2025-01-24", "Long CFD Interest for 24-JAN-2025", "-2"])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.cfd_financing_rows == 1
    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("2")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.appendix_5.losses_eur == Decimal("2")
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "CFD financing / CFD interest корекциите са третирани като част от CFD trading economics" in text
    assert "Положителните CFD financing стойности увеличават продажната страна" in text
    assert "CFD financing policy: netted_to_appendix_5" in text


def test_cfd_financing_positive_is_netted_to_appendix5_by_default(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.insert(-1, ["Fees", "Data", "Other Fees", "EUR", "2025-01-24", "Short CFD Interest", "3"])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.appendix_5.sale_price_eur == Decimal("22")
    assert result.summary.appendix_5.wins_eur == Decimal("22")
    assert result.summary.appendix_6_code_606_eur == Decimal("0")


def test_no_net_cfd_financing_puts_positive_in_code606_and_skips_negative(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.insert(-1, ["Fees", "Data", "Other Fees", "EUR", "2025-01-24", "Long CFD Interest for 24-JAN-2025", "-2"])
    rows.insert(-1, ["Fees", "Data", "Other Fees", "EUR", "2025-01-25", "CFD Financing", "3"])

    result = _run(tmp_path, rows, mode="listed_symbol", net_cfd_financing=False)

    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.appendix_5.losses_eur == Decimal("0")
    assert result.summary.appendix_6_code_606_eur == Decimal("3")
    assert result.summary.cfd_financing_negative_skipped_eur == Decimal("2")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Нетиране на CFD financing / CFD interest е изключено чрез --no-net-cfd-financing." in text
    assert "Положителните CFD financing стойности са декларирани в Приложение 6, код 606." in text
    assert "Отрицателните CFD financing стойности не са включени в декларацията." in text
    assert "CFD financing policy: conservative_no_netting" in text


def test_negative_payment_in_lieu_is_netted_to_appendix5_by_default(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.append(["Dividends", "Data", "USD", "2025-11-29", "ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)", "-10"])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.pil_negative_rows == 1
    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("9")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.appendix_5.losses_eur == Decimal("9")
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Отрицателният Payment in Lieu of Dividend (PIL) е третиран като short/synthetic exposure economics" in text
    assert "PIL policy: negative_pil_netted_to_appendix_5" in text


def test_no_net_pil_skips_negative_payment_in_lieu(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.append(["Dividends", "Data", "USD", "2025-11-29", "ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)", "-10"])

    result = _run(tmp_path, rows, mode="listed_symbol", net_pil=False)

    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")
    assert result.summary.appendix_5.wins_eur == Decimal("19")
    assert result.summary.pil_negative_skipped_eur == Decimal("9")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Нетиране на отрицателен Payment in Lieu of Dividend (PIL) е изключено чрез --no-net-pil." in text
    assert "Отрицателният PIL не е включен в декларацията." in text
    assert "PIL policy: negative_pil_skipped" in text


def test_positive_payment_in_lieu_always_goes_to_appendix6_code606(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.append(["Dividends", "Data", "USD", "2025-11-29", "ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)", "10"])

    result = _run(tmp_path, rows, mode="listed_symbol", net_pil=False)

    assert result.summary.appendix_6_positive_pil_eur == Decimal("9")
    assert result.summary.appendix_6_code_606_eur == Decimal("9")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "код 606" in text
    assert "Положителният Payment in Lieu of Dividend (PIL) е деклариран в Приложение 6, код 606." in text
    assert "Positive PIL policy: appendix_6_code_606" in text


def test_normal_cash_dividend_is_not_reclassified_as_payment_in_lieu(tmp_path: Path) -> None:
    rows = _cfd_rows()
    rows.append(["Dividends", "Data", "USD", "2025-12-16", "BMW(DE0005190003) Cash Dividend USD 0.20 per Share (Ordinary Dividend)", "4"])

    result = _run(tmp_path, rows, mode="listed_symbol")

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

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.cfd_financing_rows == 0
    assert result.summary.appendix_5.sale_price_eur == Decimal("19")
    assert result.summary.appendix_5.purchase_eur == Decimal("0")


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
            "--no-net-cfd-financing",
            "--no-net-pil",
        ]
    )

    from integrations.ibkr.analyzer_definition import ANALYZER

    options = ANALYZER.build_options(args, "single", {})
    assert options["net_cfd_financing"] is False
    assert options["net_pil"] is False
