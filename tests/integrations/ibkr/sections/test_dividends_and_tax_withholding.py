from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tests.integrations.ibkr import support as h

DIVIDEND_TAX_RATE = h.DIVIDEND_TAX_RATE
IbkrAnalyzerError = h.IbkrAnalyzerError
_base_rows = h._base_rows
_dividends_header_and_data = h._dividends_header_and_data
_inject_financial_instrument_rows = h._inject_financial_instrument_rows
_read_rows = h._read_rows
_rows_with_dividends_and_withholding = h._rows_with_dividends_and_withholding
_run = h._run
_tax_credit_debug_payload = h._tax_credit_debug_payload
_withholding_header_and_data = h._withholding_header_and_data
_write_rows = h._write_rows


def test_cli_appendix8_dividend_mode_defaults_to_company(tmp_path: Path) -> None:
    import report_analyzer

    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, _base_rows())
    parser = report_analyzer.build_parser()
    args = parser.parse_args(
        [
            "ibkr",
            "--input",
            str(input_csv),
            "--tax-year",
            "2025",
            "--tax-exempt-mode",
            "listing_exchange",
        ]
    )
    assert args.appendix8_dividend_list_mode == "company"
    assert args.positive_wht_mode == "current-year-net"


def test_cli_skip_period_validation_is_removed(tmp_path: Path) -> None:
    import report_analyzer

    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, _base_rows())
    parser = report_analyzer.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "ibkr",
                "--input",
                str(input_csv),
                "--tax-year",
                "2025",
                "--skip-period-validation",
            ]
        )


def test_cli_tax_exempt_mode_defaults_to_listing_exchange_for_single_mode(tmp_path: Path) -> None:
    import report_analyzer
    from integrations.ibkr.analyzer_definition import ANALYZER

    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, _base_rows())
    parser = report_analyzer.build_parser()
    args = parser.parse_args(
        [
            "ibkr",
            "--input",
            str(input_csv),
            "--tax-year",
            "2025",
        ]
    )

    options = ANALYZER.build_options(args, "single", {})

    assert options["tax_exempt_mode"] == "listing_exchange"


def test_cli_tax_exempt_mode_explicit_override_still_works_for_single_mode(tmp_path: Path) -> None:
    import report_analyzer
    from integrations.ibkr.analyzer_definition import ANALYZER

    input_csv = tmp_path / "input.csv"
    _write_rows(input_csv, _base_rows())
    parser = report_analyzer.build_parser()
    args = parser.parse_args(
        [
            "ibkr",
            "--input",
            str(input_csv),
            "--tax-year",
            "2025",
            "--tax-exempt-mode",
            "execution_exchange",
        ]
    )

    options = ANALYZER.build_options(args, "single", {})

    assert options["tax_exempt_mode"] == "execution_exchange"


def test_cli_tax_exempt_mode_defaults_to_listing_exchange_for_aggregate_mode() -> None:
    import report_analyzer
    from integrations.ibkr.analyzer_definition import ANALYZER

    parser = report_analyzer.build_parser()
    args = parser.parse_args(
        [
            "--input-dir",
            "/tmp/tax-inputs",
            "--tax-year",
            "2025",
        ]
    )

    options = ANALYZER.build_options(args, "aggregate", {})

    assert options["tax_exempt_mode"] == "listing_exchange"


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
    result = _run(tmp_path, rows, mode="listing_exchange")
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

def test_ambiguous_slash_dates_use_default_ibkr_report_format_across_sections(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [["Dividends", "Data", "USD", "1/2/2025", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"]],
        [["Withholding Tax", "Data", "USD", "1/2/2025", "TPR(US8760301072) Cash Dividend USD 0.35 per Share - US Tax", "-2", ""]],
    )
    rows.extend(
        [
            ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
            ["Interest", "Data", "USD", "1/2/2025", "USD Credit Interest for Jan-2025", "5"],
            ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Mark-to-Market P/L Total"],
            ["Mark-to-Market Performance Summary", "Data", "Withholding on Interest Received", "-1"],
        ]
    )
    rows[8][5] = "1/2/2024"

    result = _run(tmp_path, rows, mode="listing_exchange")

    assert result.summary.report_date_format_label == "M/D/YYYY"
    assert result.summary.report_date_format_reason == "all slash dates were ambiguous; defaulted to IBKR M/D/YYYY"
    assert result.summary.interest_processed_rows == 1
    assert result.summary.dividends_processed_rows == 1
    assert result.summary.withholding_processed_rows == 1

def test_dividend_total_rows_are_skipped(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "Total", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", ""],
            ["Dividends", "Data", "Total in EUR", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", ""],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "10"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
            ["Withholding Tax", "Data", "USD", "2025-03-02", "Withholding @ 20% on Credit Interest for Mar-2025", "-1", "", "Ireland", "-2.00000000", "Appendix 9", "TAXABLE"],
        ]
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.appendix_8_by_country["LU"].withholding_tax_paid_eur == Decimal("5")
    assert result.summary.appendix_9_withholding_paid_eur == Decimal("2")
    assert result.summary.appendix_9_country_results["IE"].aggregated_foreign_tax_paid_eur == Decimal("2")

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
    result = _run(tmp_path, rows, mode="listing_exchange")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert result.summary.dividend_tax_rate == Decimal("0.10")
    assert "Приложение 8" in text
    assert "Брутен размер на дохода: 100.00" in text
    assert "Код за прилагане на метод за избягване на двойното данъчно облагане: 1" in text
    assert "Платен данък в чужбина: 7.00" in text
    assert "Допустим размер на данъчния кредит: 7.00" in text
    assert "Размер на признатия данъчен кредит: 7.00" in text
    assert "Дължим данък, подлежащ на внасяне: 3.00" in text

def test_appendix_8_method_code_is_3_when_withholding_is_zero(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "0", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Код за прилагане на метод за избягване на двойното данъчно облагане: 3" in text
    assert "Платен данък в чужбина: 0.00" in text
    assert "Дължим данък, подлежащ на внасяне: 5.00" in text

def test_appendix_8_method_code_is_3_when_withholding_is_missing_or_blank(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "Total in EUR", "2025-03-31", "Totals", "", ""],
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Код за прилагане на метод за избягване на двойното данъчно облагане: 3" in text
    assert "Платен данък в чужбина: 0.00" in text
    assert "Дължим данък, подлежащ на внасяне: 5.00" in text


def test_positive_dividend_withholding_nets_against_negative_withholding(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-2", ""],
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "0.75", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(rows, [("Stocks", "AAA", "NYSE", "Alpha Corp")])

    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")

    company_row = result.summary.appendix_8_output_rows[0]
    assert result.summary.appendix_8_by_company[("US", "Alpha Corp")].withholding_tax_paid_eur == Decimal("1.25")
    assert company_row.foreign_tax_paid_eur == Decimal("1.25")
    assert company_row.recognized_credit_eur == Decimal("1.25")
    assert company_row.method_code == "1"
    assert result.summary.withholding_positive_dividend_rows == 1
    assert result.summary.positive_wht_rows_netted == 1
    assert not any("Открит е положителен ред в IBKR Withholding Tax" in warning for warning in result.summary.warnings)


def test_withholding_rows_outside_tax_year_are_included_when_in_statement(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2024-11-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2024-11-20", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-15", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(rows, [("Stocks", "AAA", "NYSE", "Alpha Corp")])

    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")

    company_row = result.summary.appendix_8_output_rows[0]
    assert company_row.gross_dividend_eur == Decimal("100")
    assert company_row.foreign_tax_paid_eur == Decimal("15")


def test_positive_wht_prior_year_correction_mode_excludes_positive_rows_from_current_wht(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-15", ""],
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "2", ""],
            ["Withholding Tax", "Data", "EUR", "2024-11-20", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "4", ""],
            ["Withholding Tax", "Data", "EUR", "2024-12-20", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "6", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(rows, [("Stocks", "AAA", "NYSE", "Alpha Corp")])

    result = _run(
        tmp_path,
        rows,
        mode="listing_exchange",
        appendix8_dividend_list_mode="company",
        positive_wht_mode="prior-year-correction",
    )

    company_row = result.summary.appendix_8_output_rows[0]
    assert company_row.foreign_tax_paid_eur == Decimal("13")
    assert result.summary.positive_wht_rows_netted == 1
    assert result.summary.positive_wht_rows_prior_year_corrections == 2
    correction = result.summary.appendix_8_positive_wht_corrections[0]
    assert correction.tax_date.year == 2024
    assert correction.payer_name == "Alpha Corp"
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Корекции към предходни години" in text
    assert "Приложение 8, Част III" in text
    assert (
        "Тази секция не се попълва в текущата декларация за 2025; използва се само за проверка/корекция "
        "на вече подадени декларации за предходни години."
    ) in text
    assert "Година: 2024" in text
    assert "Година: 2025" not in text
    assert "Alpha Corp; САЩ; код 8141; метод 1; корекция 10.00000000 EUR" in text
    assert "редове 24-25" in text
    assert "Какво да направите за съответната предходна декларация:" in text
    assert "- Платен данък в чужбина = max(0" in text
    assert text.index("Приложение 8, Част III") < text.index("Тази секция не се попълва")
    assert text.index("Методологични бележки") < text.index("Какво да направите за съответната предходна декларация:")


def test_positive_wht_prior_year_correction_country_mode_groups_by_country(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
            ["Dividends", "Data", "EUR", "2025-03-01", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2024-11-20", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "4", ""],
            ["Withholding Tax", "Data", "EUR", "2024-11-21", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share - US Tax", "6", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(
        rows,
        [
            ("Stocks", "AAA", "NYSE", "Alpha Corp"),
            ("Stocks", "BBB", "NYSE", "Beta Corp"),
        ],
    )

    result = _run(
        tmp_path,
        rows,
        mode="listing_exchange",
        appendix8_dividend_list_mode="country",
        positive_wht_mode="prior-year-correction",
    )

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert text.count("корекция 10.00000000 EUR") == 1
    assert "Различни чуждестранни дружества (чрез Interactive Brokers)" in text


def test_payment_in_lieu_with_numeric_conid_uses_symbol_mapping_not_isin(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-05-01", "HTGC(34822226) Payment in Lieu of Dividend (Bonus Dividend)", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-05-02", "HTGC(34822226) Payment in Lieu of Dividend - US Tax", "-15", ""],
        ],
    )
    insert_at = next(idx for idx, row in enumerate(rows) if row[:2] == ["Dividends", "Header"])
    rows[insert_at:insert_at] = [
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch", "Description", "ISIN"],
        [
            "Financial Instrument Information",
            "Data",
            "Stocks",
            "HTGC",
            "NYSE",
            "Hercules Capital Inc",
            "US4270965084",
        ],
    ]

    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")

    assert result.summary.pil_appendix8_rows == 1
    assert result.summary.review_required_rows == 0
    company_row = result.summary.appendix_8_output_rows[0]
    assert company_row.payer_name == "Hercules Capital Inc"
    assert company_row.country_bulgarian == "САЩ"
    assert company_row.gross_dividend_eur == Decimal("100")
    assert company_row.foreign_tax_paid_eur == Decimal("15")
    output_rows = _read_rows(result.output_csv_path)
    dividends_header = next(row for row in output_rows if row[:2] == ["Dividends", "Header"])
    dividends_row = next(row for row in output_rows if row[:2] == ["Dividends", "Data"])
    assert dividends_row[dividends_header.index("ISIN")] == "US4270965084"
    assert dividends_row[dividends_header.index("Is Payment In Lieu")] == "YES"
    withholding_header = next(row for row in output_rows if row[:2] == ["Withholding Tax", "Header"])
    withholding_row = next(row for row in output_rows if row[:2] == ["Withholding Tax", "Data"])
    assert withholding_row[withholding_header.index("ISIN")] == "US4270965084"
    assert withholding_row[withholding_header.index("Is Payment In Lieu")] == "YES"


def test_positive_only_dividend_withholding_creates_no_credit(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "1", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(rows, [("Stocks", "AAA", "NYSE", "Alpha Corp")])

    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")

    company_row = result.summary.appendix_8_output_rows[0]
    assert result.summary.appendix_8_by_company[("US", "Alpha Corp")].withholding_tax_paid_eur == Decimal("-1")
    assert company_row.foreign_tax_paid_eur == Decimal("0")
    assert company_row.allowable_credit_eur == Decimal("0")
    assert company_row.recognized_credit_eur == Decimal("0")
    assert company_row.method_code == "3"
    assert result.summary.withholding_non_positive_net_buckets == 1
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Платен данък в чужбина: 0.00" in text
    assert "Нетният чуждестранен данък за част от дивидентите е нула или отрицателен" not in text


def test_positive_dividend_withholding_larger_than_negative_withholding_creates_no_credit(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-1", ""],
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "2", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(rows, [("Stocks", "AAA", "NYSE", "Alpha Corp")])

    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")

    company_row = result.summary.appendix_8_output_rows[0]
    assert result.summary.appendix_8_by_company[("US", "Alpha Corp")].withholding_tax_paid_eur == Decimal("-1")
    assert company_row.foreign_tax_paid_eur == Decimal("0")
    assert company_row.method_code == "3"
    assert result.summary.withholding_non_positive_net_buckets == 1
    assert not any("Нетният чуждестранен данък" in warning for warning in result.summary.warnings)
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "IBKR — положителен Withholding Tax" in text
    assert "Открити положителни IBKR Withholding Tax редове за дивиденти: 1." in text
    assert "Избран режим: current-year-net." in text
    assert "--positive-wht-mode prior-year-correction" in text
    assert "--ibkr-positive-wht-mode prior-year-correction" in text


def test_positive_dividend_withholding_amount_eur_stays_signed_in_enriched_csv(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "1", ""],
        ],
    )

    result = _run(tmp_path, rows, mode="listing_exchange")
    out_rows = _read_rows(result.output_csv_path)
    header, data_rows = _withholding_header_and_data(out_rows)
    idx = {column: i for i, column in enumerate(header[2:])}
    row = next(item for item in data_rows if "US1111111111" in item[2 + idx["Description"]])

    assert row[2 + idx["Amount"]] == "1"
    assert row[2 + idx["Amount (EUR)"]] == "1.00000000"


def test_appendix_8_company_mode_groups_rows_and_computes_credit_per_company(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
            ["Dividends", "Data", "EUR", "2025-03-02", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "50"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-3", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(
        rows,
        [("Stocks", "AAA", "NYSE", "Alpha Corp")],
    )
    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")
    assert result.summary.appendix8_dividend_list_mode == "company"
    assert len(result.summary.appendix_8_company_results) == 1
    company_row = result.summary.appendix_8_company_results[0]
    assert company_row.payer_name == "Alpha Corp"
    assert company_row.method_code == "1"
    assert company_row.gross_dividend_eur == Decimal("150")
    assert company_row.foreign_tax_paid_eur == Decimal("3")
    assert company_row.allowable_credit_eur == Decimal("3")
    assert company_row.tax_due_bg_eur == Decimal("4.5")

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "  Наименование на лицето, изплатило дохода: Alpha Corp" in text
    assert "Код за прилагане на метод за избягване на двойното данъчно облагане: 1" in text

def test_appendix_8_country_mode_aggregates_company_rows_with_same_method(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share", "100"],
            ["Dividends", "Data", "EUR", "2025-03-02", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share", "200"],
        ],
        [
            ["Withholding Tax", "Data", "EUR", "2025-03-01", "AAA(US1111111111) Cash Dividend EUR 1.00 per Share - US Tax", "-1", ""],
            ["Withholding Tax", "Data", "EUR", "2025-03-02", "BBB(US2222222222) Cash Dividend EUR 1.00 per Share - US Tax", "-2", ""],
        ],
    )
    rows = _inject_financial_instrument_rows(
        rows,
        [
            ("Stocks", "AAA", "NYSE", "Alpha Corp"),
            ("Stocks", "BBB", "NYSE", "Beta Corp"),
        ],
    )
    company_result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="company")
    country_result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="country")

    assert len(company_result.summary.appendix_8_output_rows) == 2
    assert len(country_result.summary.appendix_8_output_rows) == 1
    country_row = country_result.summary.appendix_8_output_rows[0]
    assert country_row.payer_name == "Различни чуждестранни дружества (чрез Interactive Brokers)"
    assert country_row.method_code == "1"
    assert country_row.gross_dividend_eur == sum(
        (item.gross_dividend_eur for item in company_result.summary.appendix_8_output_rows),
        Decimal("0"),
    )
    assert country_row.foreign_tax_paid_eur == sum(
        (item.foreign_tax_paid_eur for item in company_result.summary.appendix_8_output_rows),
        Decimal("0"),
    )
    assert country_row.recognized_credit_eur == sum(
        (item.recognized_credit_eur for item in company_result.summary.appendix_8_output_rows),
        Decimal("0"),
    )
    text = country_result.declaration_txt_path.read_text(encoding="utf-8")
    assert "  Наименование на лицето, изплатило дохода: Различни чуждестранни дружества (чрез Interactive Brokers)" in text

def test_appendix_8_country_mode_splits_same_country_by_method_code(tmp_path: Path) -> None:
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
    rows = _inject_financial_instrument_rows(
        rows,
        [
            ("Stocks", "AAA", "NYSE", "Alpha Corp"),
            ("Stocks", "BBB", "NYSE", "Beta Corp"),
        ],
    )
    country_result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="country")
    output_rows = country_result.summary.appendix_8_output_rows
    assert len(output_rows) == 2
    assert {item.method_code for item in output_rows} == {"1", "3"}
    assert all(
        item.payer_name == "Различни чуждестранни дружества (чрез Interactive Brokers)"
        for item in output_rows
    )

    by_method = {item.method_code: item for item in output_rows}
    assert by_method["1"].recognized_credit_eur == Decimal("5")
    assert by_method["3"].recognized_credit_eur == Decimal("0")
    assert by_method["1"].gross_dividend_eur == Decimal("100")
    assert by_method["3"].gross_dividend_eur == Decimal("100")

def test_appendix_8_country_mode_never_recomputes_country_credit_min(tmp_path: Path) -> None:
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
    rows = _inject_financial_instrument_rows(
        rows,
        [
            ("Stocks", "AAA", "NYSE", "Alpha Corp"),
            ("Stocks", "BBB", "NYSE", "Beta Corp"),
        ],
    )
    result = _run(tmp_path, rows, mode="listing_exchange", appendix8_dividend_list_mode="country")
    recognized_total = sum((item.recognized_credit_eur for item in result.summary.appendix_8_output_rows), Decimal("0"))
    assert recognized_total == Decimal("5")

    payload = _tax_credit_debug_payload(result)
    debug_entries = payload["appendix_8_country_debug"]
    assert isinstance(debug_entries, list)
    us_entry = next(item for item in debug_entries if item["country_iso"] == "US")
    assert Decimal(us_entry["recognized_credit_sum_company"]) == Decimal("5")
    assert Decimal(us_entry["recognized_credit_wrong_country_recomputed"]) == Decimal("10")

def test_dividend_unknown_or_bad_isin_marks_review(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "XYZ(US1234567890) Special Distribution", "1"],
            ["Dividends", "Data", "USD", "2025-03-02", "No isin Cash Dividend text", "1"],
            ["Dividends", "Data", "USD", "2025-03-03", "ABC(ZZ1234567890) Cash Dividend USD 1.00 per Share", "1"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
    assert result.summary.review_required_rows >= 3
    assert result.summary.dividends_unknown_rows == 1
    assert result.summary.dividends_country_errors_rows == 2
    assert any("unknown dividend description" in w for w in result.summary.warnings)
    assert any("unknown ISIN country code=ZZ" in w for w in result.summary.warnings)

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text

def test_dividend_status_column_auto_fill_and_unknown_triggers_review(tmp_path: Path) -> None:
    rows = _rows_with_dividends_and_withholding(
        [
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share", "1"],
            ["Dividends", "Data", "USD", "2025-03-02", "ABC(US1234567890) Lieu Received USD 0.10 per Share", "1"],
            ["Dividends", "Data", "USD", "2025-03-03", "XYZ(US1234567890) Special Distribution", "1"],
        ],
        [],
    )
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
    result = _run(tmp_path, rows, mode="listing_exchange")
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
        _ = _run(tmp_path, rows, mode="listing_exchange")

def test_dividends_missing_required_column_fails(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Dividends", "Header", "Currency", "Date", "Description"],
            ["Dividends", "Data", "USD", "2025-03-01", "TPR(US8760301072) Cash Dividend USD 0.35 per Share"],
        ]
    )
    with pytest.raises(IbkrAnalyzerError, match="Dividends header at row"):
        _ = _run(tmp_path, rows, mode="listing_exchange")
