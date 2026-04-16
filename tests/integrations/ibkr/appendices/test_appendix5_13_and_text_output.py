from __future__ import annotations

from pathlib import Path

from tests.integrations.ibkr import support as h

_base_rows = h._base_rows
_run = h._run


def test_review_bucket_excluded_from_appendix_totals(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUIBSI"  # non-regulated
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review.rows == 1
    assert result.summary.appendix_13.rows == 0


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


def test_informational_warnings_do_not_trigger_manual_check_required(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUDARK"  # informational only in listed_symbol mode for EU-listed symbol
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert any("informational only" in warning for warning in result.summary.warnings)
    assert result.summary.review_required_rows == 0
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" not in text
