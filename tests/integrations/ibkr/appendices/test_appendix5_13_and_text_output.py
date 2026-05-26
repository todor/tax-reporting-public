from __future__ import annotations

from pathlib import Path

from tests.integrations.ibkr import support as h

_base_rows = h._base_rows
_run = h._run
TECHNICAL_DETAILS_SEPARATOR = "------------------------------ Technical Details ------------------------------"


def _rows_with_forex_review_statuses(statuses: list[str]) -> list[list[str]]:
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
            "Review Status",
        ],
    ]
    for index, status in enumerate(statuses, start=1):
        rows.append(
            [
                "Trades",
                "Data",
                "Forex",
                "USD",
                "EUR.USD",
                f"2025-03-{index:02d}, 10:00:00",
                "IDEALPRO",
                "C",
                "10",
                "Trade",
                "",
                status,
            ]
        )
    rows.extend(
        [
            ["Trades", "Data", "Stocks", "USD", "BMW", "2025-04-01, 10:00:00", "IBIS2", "C", "100", "Trade", "", ""],
            ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-01", "IBIS2", "", "0", "ClosedLot", "20", ""],
        ]
    )
    return rows


def test_known_non_regulated_execution_is_routed_to_appendix_5(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUIBSI"  # non-regulated
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review.rows == 0
    assert result.summary.review_rows == 0
    assert result.summary.appendix_5.rows == 2
    assert result.summary.appendix_13.rows == 0


def test_declaration_text_contains_required_sections(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "GETTEX2"
    result = _run(tmp_path, rows, mode="execution_exchange")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" not in text
    assert "Приложение 5" in text
    assert "Настройки, режими и проверки на анализа" in text
    assert "IBKR — класификация на пазари" in text
    assert "Режим за данъчно освобождаване: execution_exchange." in text
    assert "Приложение 13" not in text
    assert "РЪЧНА ПРОВЕРКА (ИЗКЛЮЧЕНИ ОТ АВТОМАТИЧНИТЕ ТАБЛИЦИ)" not in text
    assert "ВНИМАНИЕ: FOREX ОПЕРАЦИИ" not in text
    assert TECHNICAL_DETAILS_SEPARATOR in text
    assert text.index(TECHNICAL_DETAILS_SEPARATOR) > text.index("Приложение 5")
    assert text.index("Audit Data") > text.index(TECHNICAL_DETAILS_SEPARATOR)
    assert text.index("Sanity Check") > text.index("Audit Data")


def test_declaration_text_contains_effective_listed_symbol_tax_exempt_mode(tmp_path: Path) -> None:
    result = _run(tmp_path, _base_rows())
    text = result.declaration_txt_path.read_text(encoding="utf-8")

    assert "Режим за данъчно освобождаване: listed_symbol." in text
    assert "борсата на изпълнение е само информативна" in text


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
    assert result.summary.forex_review_required_rows == 1
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text
    assert "Forex сделки" in text
    assert "ВНИМАНИЕ: FOREX ОПЕРАЦИИ" not in text
    assert "брой Forex записи" not in text
    assert "общ обем" not in text
    assert text.index("!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!") < text.index("Forex операции")
    assert text.index("Forex операции") < text.index("Приложение 5")


def test_forex_non_taxable_review_status_does_not_require_manual_check(tmp_path: Path) -> None:
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
            "Review Status",
        ],
        ["Trades", "Data", "Forex", "USD", "EUR.USD", "2025-03-01, 10:00:00", "IDEALPRO", "C", "10", "Trade", "", "NON-TAXABLE"],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "Trade", "", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-01", "IBIS2", "", "0", "ClosedLot", "20", ""],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.forex_ignored_rows == 1
    assert result.summary.forex_non_taxable_ignored_rows == 1
    assert result.summary.forex_review_required_rows == 0
    assert result.summary.review_required_rows == 0
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" not in text
    assert "ВНИМАНИЕ: FOREX ОПЕРАЦИИ" not in text


def test_forex_taxable_review_status_requires_manual_check(tmp_path: Path) -> None:
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
            "Review Status",
        ],
        ["Trades", "Data", "Forex", "USD", "EUR.USD", "2025-03-01, 10:00:00", "IDEALPRO", "C", "10", "Trade", "", "TAXABLE"],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2025-01-10, 10:00:00", "IBIS2", "C", "100", "Trade", "", ""],
        ["Trades", "Data", "Stocks", "USD", "BMW", "2024-12-01", "IBIS2", "", "0", "ClosedLot", "20", ""],
    ]
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.forex_ignored_rows == 1
    assert result.summary.forex_non_taxable_ignored_rows == 0
    assert result.summary.forex_review_required_rows == 1
    assert result.summary.review_required_rows == 1
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text


def test_forex_taxable_from_here_applies_to_current_and_following_blank_rows(tmp_path: Path) -> None:
    rows = _rows_with_forex_review_statuses(["TAXABLE-FROM-HERE", ""])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.forex_ignored_rows == 2
    assert result.summary.forex_non_taxable_ignored_rows == 0
    assert result.summary.forex_review_required_rows == 2
    assert result.summary.review_required_rows == 2
    assert result.summary.review_status_overrides_rows == 1


def test_forex_non_taxable_from_here_applies_to_current_and_following_blank_rows(tmp_path: Path) -> None:
    rows = _rows_with_forex_review_statuses(["NON-TAXABLE-FROM-HERE", ""])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.forex_ignored_rows == 2
    assert result.summary.forex_non_taxable_ignored_rows == 2
    assert result.summary.forex_review_required_rows == 0
    assert result.summary.review_required_rows == 0
    assert result.summary.review_status_overrides_rows == 1


def test_forex_explicit_status_overrides_inherited_status_for_one_row_only(tmp_path: Path) -> None:
    rows = _rows_with_forex_review_statuses(["NON-TAXABLE-FROM-HERE", "", "TAXABLE", ""])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.forex_ignored_rows == 4
    assert result.summary.forex_non_taxable_ignored_rows == 3
    assert result.summary.forex_review_required_rows == 1
    assert result.summary.review_required_rows == 1
    assert result.summary.review_status_overrides_rows == 2


def test_forex_later_from_here_directive_changes_inherited_status(tmp_path: Path) -> None:
    rows = _rows_with_forex_review_statuses(["TAXABLE-FROM-HERE", "", "NON-TAXABLE-FROM-HERE", ""])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.forex_ignored_rows == 4
    assert result.summary.forex_non_taxable_ignored_rows == 2
    assert result.summary.forex_review_required_rows == 2
    assert result.summary.review_required_rows == 2
    assert result.summary.review_status_overrides_rows == 2


def test_forex_missing_status_before_from_here_still_requires_review(tmp_path: Path) -> None:
    rows = _rows_with_forex_review_statuses(["", "NON-TAXABLE-FROM-HERE", ""])

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.forex_ignored_rows == 3
    assert result.summary.forex_non_taxable_ignored_rows == 2
    assert result.summary.forex_review_required_rows == 1
    assert result.summary.review_required_rows == 1


def test_forex_inherited_status_does_not_affect_non_forex_rows(tmp_path: Path) -> None:
    rows = _rows_with_forex_review_statuses(["NON-TAXABLE-FROM-HERE", ""])
    rows[-2][6] = "EUIBSI"

    result = _run(tmp_path, rows, mode="execution_exchange")

    assert result.summary.forex_non_taxable_ignored_rows == 2
    assert result.summary.forex_review_required_rows == 0
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0


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
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" not in text
    assert "СТАТУС: NOT REQUIRED" not in text


def test_listed_symbol_execution_exchange_note_is_global_not_per_row_warning(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "EUDARK"  # execution exchange should be ignored for classification in listed_symbol mode
    result = _run(tmp_path, rows, mode="listed_symbol")
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert not any("informational only in listed_symbol mode" in warning for warning in result.summary.warnings)
    assert result.summary.review_required_rows == 0
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" not in text
    assert (
        "In listed_symbol mode, execution exchange does not participate in classification and is informational only."
        in text
    )
