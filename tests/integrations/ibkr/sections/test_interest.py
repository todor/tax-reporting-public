from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.integrations.ibkr import support as h

APPENDIX_9_ALLOWABLE_CREDIT_RATE = h.APPENDIX_9_ALLOWABLE_CREDIT_RATE
IbkrAnalyzerError = h.IbkrAnalyzerError
analyze_ibkr_activity_statement = h.analyze_ibkr_activity_statement
_base_rows = h._base_rows
_interest_header_and_data = h._interest_header_and_data
_read_rows = h._read_rows
_rows_with_interest = h._rows_with_interest
_run = h._run
_tax_credit_debug_payload = h._tax_credit_debug_payload
_write_rows = h._write_rows


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
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text
    assert "има 1 записа с непознат вид лихва" in text

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
    assert "№ и дата на документа за дохода и съответния данък:" in text
    assert "R-185 / Activity Statement" not in text

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
    assert "Подател: Credit Interest: 10.00 EUR" in text
    assert "Подател: Lieu Received: 5.00 EUR" in text
    assert "Обща сума на доходите с код 603: 15.00 EUR" in text
