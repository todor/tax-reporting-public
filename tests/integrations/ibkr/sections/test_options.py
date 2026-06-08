from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from integrations.shared.aggregation import render_aggregated_report
from integrations.shared.result_builders import build_ibkr_result

from tests.integrations.ibkr import support as h

_run = h._run


def _option_rows() -> list[list[str]]:
    return [
        ["Statement", "Header", "Field Name", "Field Value"],
        ["Statement", "Data", "Period", "January 1, 2024 - December 31, 2024"],
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
            "Proceeds",
            "Comm/Fee",
            "Basis",
            "Realized P/L",
            "Code",
        ],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "GLD 15NOV24 242 P", "2024-09-26, 12:18:53", "-", "1", "-372", "-1.05335", "373.05335", "0", "O"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "GLD 15NOV24 242 P", "2024-09-26, 12:18:53", "MERCURY", "1", "-372", "-1.05335", "373.05335", "0", "O"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "GLD 15NOV24 242 P", "2024-09-27, 13:19:59", "-", "-1", "431", "-1.0681218", "-373.05165", "56.881928", "C"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "GLD 15NOV24 242 P", "2024-09-27, 13:19:59", "MERCURY", "-1", "431", "-1.0681218", "-373.05165", "56.881928", "C"],
        ["Trades", "Data", "ClosedLot", "Equity and Index Options", "USD", "GLD 15NOV24 242 P", "9/26/2024", "", "1", "", "", "373.05165", "56.881928", "ST"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "SMH 29NOV24 225 P", "2024-11-20, 15:11:37", "-", "-1", "137", "-0.7081486", "-136.2918514", "0", "O"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "SMH 29NOV24 225 P", "2024-11-20, 15:11:37", "CBOE2", "-1", "137", "-0.7081486", "-136.2918514", "0", "O"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "SMH 29NOV24 225 P", "2024-11-21, 09:35:19", "-", "1", "-36", "-1.05155", "136.291851", "99.240301", "C"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "SMH 29NOV24 225 P", "2024-11-21, 09:35:19", "CBOE2", "1", "-36", "-1.05155", "136.291851", "99.240301", "C"],
        ["Trades", "Data", "ClosedLot", "Equity and Index Options", "USD", "SMH 29NOV24 225 P", "11/20/2024", "", "-1", "", "", "-136.291851", "99.240301", "ST"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "SMH 29NOV24 235 P", "2024-11-20, 15:11:37", "-", "1", "-372", "-0.70155", "372.70155", "0", "O"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "SMH 29NOV24 235 P", "2024-11-20, 15:11:37", "CBOE2", "1", "-372", "-0.70155", "372.70155", "0", "O"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "SMH 29NOV24 235 P", "2024-11-21, 09:35:19", "-", "-1", "136", "-1.0581208", "-372.70155", "-237.759671", "C"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "SMH 29NOV24 235 P", "2024-11-21, 09:35:19", "CBOE2", "-1", "136", "-1.0581208", "-372.70155", "-237.759671", "C"],
        ["Trades", "Data", "ClosedLot", "Equity and Index Options", "USD", "SMH 29NOV24 235 P", "11/20/2024", "", "1", "", "", "372.70155", "-237.759671", "ST"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "SPY 20DEC24 585 P", "2024-11-08, 11:07:15", "-", "1", "-517", "-0.80155", "517.80155", "0", "O"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "SPY 20DEC24 585 P", "2024-11-08, 11:07:15", "PSE", "1", "-517", "-0.80155", "517.80155", "0", "O"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "SPY 20DEC24 585 P", "2024-11-11, 09:59:57", "-", "-1", "490", "-0.877962", "-517.80155", "-28.679512", "C"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "SPY 20DEC24 585 P", "2024-11-11, 09:59:57", "SAPPHIRE", "-1", "490", "-0.877962", "-517.80155", "-28.679512", "C"],
        ["Trades", "Data", "ClosedLot", "Equity and Index Options", "USD", "SPY 20DEC24 585 P", "11/8/2024", "", "1", "", "", "517.80155", "-28.679512", "ST"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "TLT 20DEC24 96 C", "2024-10-23, 09:30:31", "-", "1", "-110", "-0.63295", "110.63295", "0", "O"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "TLT 20DEC24 96 C", "2024-10-23, 09:30:31", "CBOE2", "1", "-110", "-0.63295", "110.63295", "0", "O"],
        ["Trades", "Data", "Order", "Equity and Index Options", "USD", "TLT 20DEC24 96 C", "2024-10-24, 09:32:55", "-", "-1", "120", "-0.639076", "-110.63295", "8.727974", "C"],
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "TLT 20DEC24 96 C", "2024-10-24, 09:32:55", "CBOE2", "-1", "120", "-0.639076", "-110.63295", "8.727974", "C"],
        ["Trades", "Data", "ClosedLot", "Equity and Index Options", "USD", "TLT 20DEC24 96 C", "10/23/2024", "", "1", "", "", "110.63295", "8.727974", "ST"],
        ["Mark-to-Market Performance Summary", "Header", "Asset Category", "Symbol", "Mark-to-Market P/L Total"],
        ["Mark-to-Market Performance Summary", "Data", "Equity and Index Options", "GLD 15NOV24 242 P", "999999"],
        ["Open Positions", "Header", "Asset Category", "Symbol", "Currency", "Summary Quantity", "Cost Basis", "DataDiscriminator"],
        ["Open Positions", "Data", "Equity and Index Options", "TLT 20DEC24 96 C", "USD", "1", "100", "Summary"],
    ]


def test_equity_and_index_options_use_stock_style_closedlot_appendix5_model(tmp_path: Path) -> None:
    result = _run(tmp_path, _option_rows(), mode="listing_exchange", year=2024)

    sale_native = Decimal("429.9318782") + Decimal("136.291851") + Decimal("134.9418792") + Decimal("489.122038") + Decimal("119.360924")
    purchase_native = Decimal("373.05165") + Decimal("37.05155") + Decimal("372.70155") + Decimal("517.80155") + Decimal("110.63295")
    expected_fx = Decimal("0.9")

    assert result.summary.option_trade_rows == 10
    assert result.summary.option_closedlot_rows == 5
    assert result.summary.option_unhandled_trade_rows == 0
    assert result.summary.option_closedlot_realized_pl_by_currency["USD"] == Decimal("-101.58898")
    assert result.summary.appendix_5.sale_price_eur == sale_native * expected_fx
    assert result.summary.appendix_5.purchase_eur == purchase_native * expected_fx
    assert result.summary.appendix_5.wins_eur == (Decimal("56.8802282") + Decimal("99.240301") + Decimal("8.727974")) * expected_fx
    assert result.summary.appendix_5.losses_eur == (Decimal("237.7596708") + Decimal("28.679512")) * expected_fx
    assert result.summary.appendix_5.rows == 5


def test_options_ignore_order_rows_mtm_and_spb8_holdings(tmp_path: Path) -> None:
    result = _run(tmp_path, _option_rows(), mode="listing_exchange", year=2024)
    tax_result = build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=2024,
        output_paths={"declaration": result.declaration_txt_path},
        summary=result.summary,
    )

    assert result.summary.order_discriminator_rows == 10
    assert result.summary.option_open_position_rows == 1
    assert result.summary.option_unhandled_trade_rows == 0
    assert result.summary.spb8_rows == []
    assert result.summary.appendix_8_part1_rows == []
    assert result.summary.futures_mtm_rows == 0
    assert result.summary.appendix_5.rows == 5
    assert all(diagnostic.code != "IBKR_OPTIONS_UNHANDLED_ROWS" for diagnostic in tax_result.diagnostics)


def test_option_expiry_with_closedlot_is_processed_without_c_token(tmp_path: Path) -> None:
    rows = _option_rows()[:5]
    rows.extend(
        [
            ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "ABC 17JAN24 10 P", "2024-01-17, 16:00:00", "CBOE", "-1", "0", "0", "-25", "-25", "Ep"],
            ["Trades", "Data", "ClosedLot", "Equity and Index Options", "USD", "ABC 17JAN24 10 P", "1/10/2024", "", "1", "", "", "25", "-25", "ST"],
        ]
    )

    result = _run(tmp_path, rows, mode="listing_exchange", year=2024)

    assert result.summary.option_closedlot_rows == 1
    assert result.summary.appendix_5.purchase_eur == Decimal("22.5")
    assert result.summary.appendix_5.losses_eur == Decimal("22.5")


def test_option_assignment_without_closedlot_is_informational_and_not_taxed(tmp_path: Path) -> None:
    rows = _option_rows()[:5]
    rows.append(
        ["Trades", "Data", "Trade", "Equity and Index Options", "USD", "ABC 17JAN24 10 P", "2024-01-17, 16:00:00", "CBOE", "-1", "0", "0", "-25", "-25", "AEx"]
    )

    result = _run(tmp_path, rows, mode="listing_exchange", year=2024)
    tax_result = build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=2024,
        output_paths={"declaration": result.declaration_txt_path},
        summary=result.summary,
    )

    assert result.summary.appendix_5.rows == 0
    assert result.summary.option_exercise_assignment_without_closedlot_rows == 1
    assert any(item.code == "IBKR_OPTIONS_EXERCISE_ASSIGNMENT_NO_CLOSEDLOT" for item in tax_result.diagnostics)


def test_option_policy_note_is_rendered_in_individual_and_aggregate_reports(tmp_path: Path) -> None:
    result = _run(tmp_path, _option_rows(), mode="listing_exchange", year=2024)

    individual_text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Опции върху акции и индекси" in individual_text
    assert "Реализираните печалби/загуби от затворени или изтекли опции" in individual_text
    assert "Опциите не се включват в СПБ-8 като притежавани ценни книжа." in result.summary.spb8_notes
    option_section = individual_text.split("Опции върху акции и индекси", 1)[1].split("\n\n", 1)[0]
    assert "Опциите не се включват в СПБ-8" not in option_section
    assert "Бележки към СПБ-8" in individual_text

    tax_result = build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=2024,
        output_paths={"declaration": result.declaration_txt_path},
        summary=result.summary,
    )
    rendered = render_aggregated_report(
        tax_year=2024,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=[tax_result],
        analyzer_errors={},
        spb8_notes=result.summary.spb8_notes,
    )

    assert "Опции върху акции и индекси" in rendered
    assert rendered.count("Реализираните печалби/загуби от затворени или изтекли опции") == 1
    assert rendered.count("Опциите не се включват в СПБ-8 като притежавани ценни книжа.") == 1
    aggregate_option_section = rendered.split("Опции върху акции и индекси", 1)[1].split("\n\n", 1)[0]
    assert "Опциите не се включват в СПБ-8" not in aggregate_option_section
    assert "Бележки към СПБ-8" in rendered


def test_outside_tax_year_option_rows_do_not_render_main_policy_notes(tmp_path: Path) -> None:
    rows = [
        ["Statement", "Header", "Field Name", "Field Value"],
        ["Statement", "Data", "Period", "January 1, 2025 - December 31, 2025"],
        *_option_rows()[2:-3],
    ]

    result = _run(tmp_path, rows, mode="listing_exchange", year=2025)
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

    assert result.summary.option_trade_rows == 10
    assert result.summary.option_closedlot_rows == 0
    assert result.summary.option_open_position_rows == 0
    assert result.summary.appendix_5.rows == 0
    assert "Опции върху акции и индекси" not in result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Опции върху акции и индекси" not in rendered
    assert "Опциите не се включват в СПБ-8 като притежавани ценни книжа." not in result.summary.spb8_notes
