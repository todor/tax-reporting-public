from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.shared.contracts import UserFacingTaxError
from integrations.shared.aggregation import render_aggregated_report
from integrations.shared.reporting import classify_exception, user_message_lines_bg
from integrations.shared.result_builders import build_ibkr_result

from tests.integrations.ibkr import support as h

_run = h._run


def _futures_rows(*, include_mtm: bool = True) -> list[list[str]]:
    rows = [
        ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Listing Exch", "Description"],
        ["Financial Instrument Information", "Data", "Stocks", "BMW", "IBIS2", "Bayerische Motoren Werke AG"],
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
        ["Trades", "Data", "Trade", "Futures", "EUR", "HEM5", "2025-01-10, 10:00:00", "ECBOT", "1", "1", "1", "1000", "-1", "0", "-55.94", "0", "C"],
        ["Trades", "Data", "Trade", "Futures", "EUR", "HEQ5", "2025-01-11, 10:00:00", "ECBOT", "1", "1", "1", "2000", "-1", "0", "-175.94", "0", "C"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Currency", "Summary Quantity", "Cost Basis", "DataDiscriminator"],
        ["Open Positions", "Data", "Futures", "HEM5", "EUR", "1", "1000", "Summary"],
        ["Cash Report", "Header", "Currency Summary", "Currency", "Total", "Securities", "Futures"],
        ["Cash Report", "Data", "Cash Settling MTM", "EUR", "9999", "0", "9999"],
    ]
    if include_mtm:
        rows.extend(
            [
                [
                    "Mark-to-Market Performance Summary",
                    "Header",
                    "Asset Category",
                    "Symbol",
                    "Mark-to-Market P/L Position",
                    "Mark-to-Market P/L Transaction",
                    "Mark-to-Market P/L Commissions",
                    "Mark-to-Market P/L Other",
                    "Mark-to-Market P/L Total",
                ],
                ["Mark-to-Market Performance Summary", "Data", "Futures", "FXXP DEC 24", "-335", "115", "-2.56", "0", "-222.56"],
                ["Mark-to-Market Performance Summary", "Data", "Futures", "HEM5", "-54.4842", "9.122", "-5.3921835", "0", "-50.7543835"],
                ["Mark-to-Market Performance Summary", "Data", "Futures", "HEQ5", "-72.6456", "-81.7204", "-5.3921835", "0", "-159.7581835"],
            ]
        )
    return rows


def test_futures_mtm_rows_contribute_to_appendix5_without_trade_or_cash_double_count(tmp_path: Path) -> None:
    result = _run(tmp_path, _futures_rows(), mode="listed_symbol")

    expected_loss = Decimal("433.0725670")
    assert result.summary.futures_trade_rows == 2
    assert result.summary.futures_mtm_rows == 3
    assert result.summary.appendix_5.sale_price_eur == Decimal("0")
    assert result.summary.appendix_5.purchase_eur == expected_loss
    assert result.summary.appendix_5.losses_eur == expected_loss
    assert result.summary.appendix_5.wins_eur == Decimal("0")
    assert result.summary.appendix_5.rows == 2
    assert result.summary.futures_mtm_total_eur == -expected_loss
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "- Брой сделки: 2" in text
    assert "- Брой сделки: 3" not in text


def test_positive_futures_mtm_maps_to_appendix5_sale_side(tmp_path: Path) -> None:
    rows = _futures_rows()
    rows[-3][-5:] = ["20", "5", "-1", "0", "24"]

    result = _run(tmp_path, rows, mode="listed_symbol")

    assert result.summary.appendix_5.sale_price_eur == Decimal("24")
    assert result.summary.appendix_5.wins_eur == Decimal("24")


def test_futures_are_excluded_from_spb8_and_main_report_explains_policy(tmp_path: Path) -> None:
    result = _run(tmp_path, _futures_rows(), mode="listed_symbol")

    assert [row for row in result.summary.spb8_rows if row.type_code == "04"] == []
    assert any("IBKR фючърсите не се включват като ценни книжа в СПБ-8" in note for note in result.summary.spb8_notes)
    assert all(not note.startswith("СПБ-8: IBKR фючърсите") for note in result.summary.spb8_notes)
    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Фючърси — IBKR daily cash-settled MTM" in text
    assert "Trades редовете за фючърси не се добавят отделно" in text
    assert "Класификация на IBKR фючърси" in text
    assert "СПБ-8: IBKR фючърсите" not in text


def test_futures_spb8_exclusion_note_appears_once_in_aggregate_report(tmp_path: Path) -> None:
    result = _run(tmp_path, _futures_rows(), mode="listed_symbol")
    tax_result = build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=2025,
        output_paths={"declaration": result.declaration_txt_path},
        summary=result.summary,
    )

    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=[tax_result],
        analyzer_errors={},
        spb8_notes=result.summary.spb8_notes,
    )

    note = "IBKR фючърсите не се включват като ценни книжа в СПБ-8"
    assert rendered.count(note) == 1
    assert "Бележки към СПБ-8" in rendered
    lines = rendered.splitlines()
    futures_index = lines.index("Фючърси — IBKR daily cash-settled MTM")
    futures_block: list[str] = []
    for line in lines[futures_index + 1 :]:
        if line == "":
            break
        futures_block.append(line)
    assert note not in "\n".join(futures_block)


def test_futures_trades_without_mtm_rows_fail_with_bulgarian_error(tmp_path: Path) -> None:
    with pytest.raises(UserFacingTaxError) as exc_info:
        _run(tmp_path, _futures_rows(include_mtm=False), mode="listed_symbol")

    diagnostic = classify_exception(exc_info.value, analyzer_alias="ibkr")
    assert diagnostic.code == "IBKR_FUTURES_MISSING_MTM_ROWS"
    assert any("липсват Futures редове в Mark-to-Market Performance Summary" in line for line in user_message_lines_bg(diagnostic))


def test_futures_mtm_missing_required_column_names_column(tmp_path: Path) -> None:
    rows = _futures_rows()
    rows[-4] = [column for column in rows[-4] if column != "Mark-to-Market P/L Total"]

    with pytest.raises(UserFacingTaxError) as exc_info:
        _run(tmp_path, rows, mode="listed_symbol")

    diagnostic = classify_exception(exc_info.value, analyzer_alias="ibkr")
    assert diagnostic.code == "IBKR_FUTURES_MISSING_MTM_COLUMNS"
    assert diagnostic.params["columns"] == ["Mark-to-Market P/L Total"]
    assert "Mark-to-Market P/L Total" in "\n".join(user_message_lines_bg(diagnostic))


def test_futures_mtm_arithmetic_mismatch_warns(tmp_path: Path) -> None:
    rows = _futures_rows()
    rows[-1][-1] = "-100"

    result = _run(tmp_path, rows, mode="listed_symbol")
    tax_result = build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=2025,
        output_paths={"declaration": result.declaration_txt_path},
        summary=result.summary,
    )

    assert len(result.summary.futures_mtm_arithmetic_mismatches) == 1
    assert result.summary.futures_mtm_arithmetic_mismatches[0]["symbol"] == "HEQ5"
    assert any(
        diagnostic.code == "IBKR_FUTURES_MTM_ARITHMETIC_MISMATCH" and diagnostic.severity == "WARNING"
        for diagnostic in tax_result.diagnostics
    )


def test_futures_mtm_other_is_included_via_total_and_reported(tmp_path: Path) -> None:
    rows = _futures_rows()
    rows[-1][-2] = "10"
    rows[-1][-1] = "-149.7581835"

    result = _run(tmp_path, rows, mode="listed_symbol")
    tax_result = build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=2025,
        output_paths={"declaration": result.declaration_txt_path},
        summary=result.summary,
    )

    assert result.summary.futures_mtm_other_rows == 1
    assert result.summary.futures_mtm_other_eur == Decimal("10")
    assert result.summary.appendix_5.purchase_eur == Decimal("423.0725670")
    assert any(
        diagnostic.code == "IBKR_FUTURES_MTM_OTHER_INCLUDED" and diagnostic.severity == "INFO"
        for diagnostic in tax_result.diagnostics
    )
