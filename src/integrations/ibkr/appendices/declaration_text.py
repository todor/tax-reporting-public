from __future__ import annotations

import re
from decimal import Decimal

from integrations.shared.rendering.appendix13 import (
    Appendix13Part2Entry,
    render_appendix13_part2,
)
from integrations.shared.rendering.appendix5 import (
    Appendix5Table2Entry,
    render_appendix5_table2,
)
from integrations.shared.rendering.appendix6 import (
    Appendix6Part1CodeTotal,
    Appendix6Part2TaxableTotal,
    Appendix6RenderData,
    render_appendix6,
)
from integrations.shared.rendering.appendix8 import (
    Appendix8Part1Row,
    Appendix8Part3Row,
    Appendix8RenderData,
    appendix8_part1_declarative_note_lines,
    render_appendix8,
)
from integrations.shared.rendering.appendix9 import (
    Appendix9Part2Row,
    render_appendix9_part2,
)
from integrations.shared.rendering.common import Money, MoneyRenderContext, render_money_line
from integrations.shared.rendering.display_currency import display_currency_technical_lines

from ..constants import (
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    DECIMAL_TWO,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTED_SYMBOL,
)
from ..models import AnalysisResult, AnalysisSummary, BucketTotals
from ..shared import _fmt

TECHNICAL_DETAILS_SEPARATOR = (
    "------------------------------ Technical Details ------------------------------"
)

_OPEN_POSITION_MISMATCH_RE = re.compile(
    r"OPEN_POSITION_TRADE_QTY_MISMATCH:\s+asset=(?P<asset>\S+)\s+symbol=(?P<symbol>\S+)\s+"
    r"prior_qty=(?P<prior>[-0-9.]+)\s+trade_delta_qty=(?P<trade_delta>[-0-9.]+)\s+"
    r"expected_open_qty=(?P<expected>[-0-9.]+)\s+actual_open_qty=(?P<actual>[-0-9.]+)\s+"
    r"diff=(?P<diff>[-0-9.]+)"
)


def _sum_bucket(bucket: BucketTotals, sale_price_eur: Decimal, purchase_eur: Decimal, pnl_eur: Decimal) -> None:
    bucket.sale_price_eur += sale_price_eur
    bucket.purchase_eur += purchase_eur

    if pnl_eur > 0:
        bucket.wins_eur += pnl_eur
    elif pnl_eur < 0:
        bucket.losses_eur += -pnl_eur
    bucket.rows += 1


def _is_zero_amount(value: Decimal) -> bool:
    return value == Decimal("0")


def _bucket_has_reportable_values(bucket: BucketTotals) -> bool:
    return any(
        not _is_zero_amount(amount)
        for amount in (
            bucket.sale_price_eur,
            bucket.purchase_eur,
            bucket.wins_eur,
            bucket.losses_eur,
            bucket.wins_eur - bucket.losses_eur,
        )
    ) or bucket.rows > 0


def _appendix6_has_reportable_values(summary: AnalysisSummary) -> bool:
    return any(
        not _is_zero_amount(amount)
        for amount in (
            summary.appendix_6_code_603_eur,
            summary.appendix_6_credit_interest_eur,
            summary.appendix_6_syep_interest_eur,
            summary.appendix_6_other_taxable_eur,
            summary.appendix_6_lieu_received_eur,
        )
    )


def _appendix9_has_reportable_values(summary: AnalysisSummary) -> bool:
    if summary.appendix_9_country_results:
        return True
    return any(
        not _is_zero_amount(amount)
        for amount in (
            summary.appendix_9_credit_interest_eur,
            summary.appendix_9_withholding_paid_eur,
        )
    )


def _build_manual_check_reasons(summary: AnalysisSummary) -> list[str]:
    reasons: list[str] = []
    if summary.sanity_failures_count > 0:
        reasons.append(f"има {summary.sanity_failures_count} неуспешни sanity проверки")
    if summary.review_required_rows > 0:
        reasons.append(f"има {summary.review_required_rows} записа с изисквана ръчна проверка")
    if summary.interest_unknown_rows > 0:
        reasons.append(f"има {summary.interest_unknown_rows} записа с непознат вид лихва")
    if summary.dividends_unknown_rows > 0:
        reasons.append(f"има {summary.dividends_unknown_rows} записа с неразпознат дивидентен ред")
    if summary.dividends_country_errors_rows > 0:
        reasons.append(f"има {summary.dividends_country_errors_rows} дивидентни реда с невалиден ISIN/държава")
    if summary.withholding_country_errors_rows > 0:
        reasons.append(f"има {summary.withholding_country_errors_rows} реда удържан данък с невалиден ISIN/държава")
    if summary.unknown_review_status_rows > 0:
        values = ", ".join(sorted(summary.unknown_review_status_values)) or "-"
        reasons.append(
            f"има {summary.unknown_review_status_rows} записа с непознат Review Status ({values})"
        )
    if summary.forex_review_required_rows > 0:
        reasons.append(
            f"има {summary.forex_review_required_rows} Forex записа "
            "(TAXABLE/липсващ/непознат Review Status), които са изключени"
        )
    return reasons


def _append_manual_check_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    reasons = _build_manual_check_reasons(summary)
    if not reasons:
        return

    lines.append("!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!")
    for reason in reasons:
        lines.append(f"- {reason}")
    manual_actions: list[str] = []
    unmatched_actions = 0
    for warning in summary.warnings:
        match = _OPEN_POSITION_MISMATCH_RE.match(warning)
        if match:
            manual_actions.append(
                "Проверете Open Positions за {asset}/{symbol}: "
                "начално количество за периода {prior} + промяна от Trades/Order {trade_delta} = очаквано {expected}, "
                "а отчетеното е {actual} (разлика {diff}).".format(
                    asset=match.group("asset"),
                    symbol=match.group("symbol"),
                    prior=match.group("prior"),
                    trade_delta=match.group("trade_delta"),
                    expected=match.group("expected"),
                    actual=match.group("actual"),
                    diff=match.group("diff"),
                )
            )
            continue
        if (
            warning.startswith("OPEN_POSITION_")
            or warning.startswith("TRADE_UNMATCHED_INSTRUMENT")
            or warning.startswith("Invalid listing exchange")
            or warning.startswith("Invalid execution exchange")
            or "unmapped" in warning.lower()
        ):
            unmatched_actions += 1
    if manual_actions:
        lines.append("- Конкретни действия:")
        for action in manual_actions[:10]:
            lines.append(f"  {action}")
    if unmatched_actions > 0:
        lines.append(
            f"- Има {unmatched_actions} допълнителни технически записа за ръчна проверка в секция "
            "\"Technical Details\" -> \"Processing Notes\"."
        )
    lines.append("")


def _append_sanity_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    lines.append("Sanity Check")
    lines.append(f"- Sanity checks: {'PASS' if summary.sanity_passed else 'FAIL'}")
    lines.append(f"- Checked Trade rows (entry + exit): {summary.sanity_checked_closing_trades}")
    lines.append(f"- Checked ClosedLot rows: {summary.sanity_checked_closedlots}")
    lines.append(f"- Checked SubTotal rows: {summary.sanity_checked_subtotals}")
    lines.append(f"- Checked Total rows: {summary.sanity_checked_totals}")
    lines.append(f"- Ignored Forex rows: {summary.sanity_forex_ignored_rows}")
    if summary.sanity_forex_ignored_rows > 0:
        lines.append("- NOTE: Forex operations are excluded from sanity checks because they are ignored by this analyzer version.")
    if summary.sanity_debug_artifacts_dir:
        lines.append(f"- Sanity-check debug artifacts path: {summary.sanity_debug_artifacts_dir}")
        lines.append("- Debug artifacts are verification-only and not production tax outputs.")
    if summary.sanity_report_path:
        lines.append(f"- Sanity report: {summary.sanity_report_path}")
    if summary.sanity_failure_messages:
        lines.append("- Sanity diagnostics:")
        for item in summary.sanity_failure_messages[:20]:
            lines.append(f"  {item}")
    lines.append("")


def _append_appendix5_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    money_context: MoneyRenderContext | None = None,
) -> None:
    app5 = summary.appendix_5
    if not _bucket_has_reportable_values(app5):
        return
    appendix_lines = render_appendix5_table2(
        [
            Appendix5Table2Entry(
                code="508",
                sale_value=Money(app5.sale_price_eur, "EUR"),
                acquisition_value=Money(app5.purchase_eur, "EUR"),
                profit=Money(app5.wins_eur, "EUR"),
                loss=Money(app5.losses_eur, "EUR"),
                net_result=Money(app5.wins_eur - app5.losses_eur, "EUR"),
                trade_count=app5.rows,
            )
        ],
        money_context=money_context,
    )
    lines.extend(appendix_lines)
    lines.append("")


def _append_appendix13_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    money_context: MoneyRenderContext | None = None,
) -> None:
    app13 = summary.appendix_13
    if not _bucket_has_reportable_values(app13):
        return
    appendix_lines = render_appendix13_part2(
        [
            Appendix13Part2Entry(
                code="5081",
                gross_income=Money(app13.sale_price_eur, "EUR"),
                acquisition_value=Money(app13.purchase_eur, "EUR"),
                profit=Money(app13.wins_eur, "EUR"),
                loss=Money(app13.losses_eur, "EUR"),
                net_result=Money(app13.wins_eur - app13.losses_eur, "EUR"),
                trade_count=app13.rows,
            )
        ],
        money_context=money_context,
    )
    lines.extend(appendix_lines)
    lines.append("")


def _append_appendix6_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    money_context: MoneyRenderContext | None = None,
) -> None:
    if not _appendix6_has_reportable_values(summary):
        return
    appendix_lines = render_appendix6(
        Appendix6RenderData(
            part1_code_totals=[
                Appendix6Part1CodeTotal(
                    code="603",
                    amount=Money(summary.appendix_6_code_603_eur, "EUR"),
                )
            ],
            part2_taxable_totals=[
                Appendix6Part2TaxableTotal(
                    code="603",
                    amount=Money(summary.appendix_6_code_603_eur, "EUR"),
                )
            ],
            part3_withheld_tax=Money(Decimal("0"), "EUR"),
        ),
        money_context=money_context,
    )
    lines.extend(appendix_lines)
    if any(
        not _is_zero_amount(amount)
        for amount in (
            summary.appendix_6_credit_interest_eur,
            summary.appendix_6_syep_interest_eur,
            summary.appendix_6_other_taxable_eur,
            summary.appendix_6_lieu_received_eur,
        )
    ):
        lines.append("Информативни")
        lines.append(
            render_money_line(
                "- Подател: Credit Interest",
                Money(summary.appendix_6_credit_interest_eur, "EUR"),
                quant=DECIMAL_TWO,
                context=money_context,
            )
        )
        lines.append(
            render_money_line(
                "- Подател: IBKR Managed Securities (SYEP) Interest",
                Money(summary.appendix_6_syep_interest_eur, "EUR"),
                quant=DECIMAL_TWO,
                context=money_context,
            )
        )
        lines.append(
            render_money_line(
                "- Подател: Other taxable (Review override)",
                Money(summary.appendix_6_other_taxable_eur, "EUR"),
                quant=DECIMAL_TWO,
                context=money_context,
            )
        )
        lines.append(
            render_money_line(
                "- Подател: Lieu Received",
                Money(summary.appendix_6_lieu_received_eur, "EUR"),
                quant=DECIMAL_TWO,
                context=money_context,
            )
        )
    if summary.interest_unknown_rows > 0:
        lines.append("- НУЖЕН Е ПРЕГЛЕД: открити са непознати видове лихви")
        lines.append(f"- брой непознати редове: {summary.interest_unknown_rows}")
        lines.append(f"- непознати видове: {', '.join(sorted(summary.interest_unknown_types))}")
    if summary.dividends_unknown_rows > 0:
        lines.append("- НУЖЕН Е ПРЕГЛЕД: открити са неразпознати дивидентни описания")
        lines.append(f"- брой неразпознати редове: {summary.dividends_unknown_rows}")
    lines.append("")


def _append_appendix8_sections(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    money_context: MoneyRenderContext | None = None,
) -> None:
    appendix_lines = render_appendix8(
        Appendix8RenderData(
            part1_rows=[
                Appendix8Part1Row(
                    asset_type="Акции",
                    country=part1.country_bulgarian,
                    quantity=_fmt(part1.quantity),
                    acquisition_date=part1.acquisition_date.strftime("%d.%m.%Y"),
                    acquisition_native=Money(part1.cost_basis_original, part1.cost_basis_original_currency or "-"),
                    acquisition_eur=Money(part1.cost_basis_eur, "EUR"),
                    native_currency_label=part1.cost_basis_original_currency or "-",
                )
                for part1 in summary.appendix_8_part1_rows
            ],
            part3_rows=[
                Appendix8Part3Row(
                    payer=bucket.payer_name,
                    country=bucket.country_bulgarian,
                    code="8141",
                    treaty_method=bucket.method_code,
                    gross_income=Money(bucket.gross_dividend_eur, "EUR"),
                    foreign_tax=Money(bucket.foreign_tax_paid_eur, "EUR"),
                    allowable_credit=Money(bucket.allowable_credit_eur, "EUR"),
                    recognized_credit=Money(bucket.recognized_credit_eur, "EUR"),
                    tax_due=Money(bucket.tax_due_bg_eur, "EUR"),
                )
                for bucket in summary.appendix_8_output_rows
            ],
        ),
        money_context=money_context,
    )
    if not appendix_lines:
        return
    lines.extend(appendix_lines)
    lines.append("")


def _append_appendix8_part1_note(lines: list[str], *, has_part1_rows: bool) -> None:
    if not has_part1_rows:
        return
    lines.extend(appendix8_part1_declarative_note_lines())
    lines.append("")


def _append_appendix9_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    appendix9_allowable_credit_rate: Decimal,
    money_context: MoneyRenderContext | None = None,
) -> None:
    if not _appendix9_has_reportable_values(summary):
        return
    rows: list[Appendix9Part2Row] = []
    if summary.appendix_9_country_results:
        for country_iso in sorted(summary.appendix_9_country_results):
            country_result = summary.appendix_9_country_results[country_iso]
            rows.append(
                Appendix9Part2Row(
                    country=country_result.country_bulgarian,
                    code="603",
                    gross_income=Money(country_result.aggregated_gross_eur, "EUR"),
                    tax_base=Money(country_result.aggregated_gross_eur, "EUR"),
                    foreign_tax=Money(country_result.aggregated_foreign_tax_paid_eur, "EUR"),
                    allowable_credit=Money(country_result.allowable_credit_aggregated_eur, "EUR"),
                    recognized_credit=Money(country_result.recognized_credit_correct_eur, "EUR"),
                    document_ref="R-185 / Activity Statement",
                )
            )
    else:
        rows.append(
            Appendix9Part2Row(
                country="Ирландия",
                code="603",
                gross_income=Money(summary.appendix_9_credit_interest_eur, "EUR"),
                tax_base=Money(summary.appendix_9_credit_interest_eur, "EUR"),
                foreign_tax=Money(summary.appendix_9_withholding_paid_eur, "EUR"),
                allowable_credit=Money(
                    summary.appendix_9_credit_interest_eur * appendix9_allowable_credit_rate,
                    "EUR",
                ),
                recognized_credit=Money(
                    min(
                        summary.appendix_9_withholding_paid_eur,
                        summary.appendix_9_credit_interest_eur * appendix9_allowable_credit_rate,
                    ),
                    "EUR",
                ),
                document_ref="R-185 / Activity Statement",
            )
        )
    lines.extend(render_appendix9_part2(rows, money_context=money_context))
    lines.append("")


def _append_review_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    money_context: MoneyRenderContext | None = None,
) -> None:
    if summary.tax_exempt_mode != TAX_MODE_EXECUTION_EXCHANGE:
        return
    if summary.review_rows <= 0:
        return

    review = summary.review
    lines.append("РЪЧНА ПРОВЕРКА (ИЗКЛЮЧЕНИ ОТ АВТОМАТИЧНИТЕ ТАБЛИЦИ)")
    lines.append(f"- изключени записи: {summary.review_rows}")
    lines.append(
        render_money_line(
            "- продажна цена",
            Money(review.sale_price_eur, "EUR"),
            quant=DECIMAL_TWO,
            context=money_context,
        )
    )
    lines.append(
        render_money_line(
            "- цена на придобиване",
            Money(review.purchase_eur, "EUR"),
            quant=DECIMAL_TWO,
            context=money_context,
        )
    )
    lines.append(
        render_money_line(
            "- печалба",
            Money(review.wins_eur, "EUR"),
            quant=DECIMAL_TWO,
            context=money_context,
        )
    )
    lines.append(
        render_money_line(
            "- загуба",
            Money(review.losses_eur, "EUR"),
            quant=DECIMAL_TWO,
            context=money_context,
        )
    )
    lines.append(
        render_money_line(
            "- нетен резултат",
            Money(review.wins_eur - review.losses_eur, "EUR"),
            quant=DECIMAL_TWO,
            context=money_context,
        )
    )
    lines.append("")
    for entry in summary.review_entries:
        lines.append(
            "- row={row} symbol={symbol} date={dt} listing={listing} execution={execution} "
            "reason={reason} proceeds_eur={proceeds} basis_eur={basis} pnl_eur={pnl}".format(
                row=entry.row_number,
                symbol=entry.symbol,
                dt=entry.trade_date,
                listing=entry.listing_exchange,
                execution=entry.execution_exchange,
                reason=entry.reason,
                proceeds=_fmt(entry.proceeds_eur, quant=DECIMAL_TWO),
                basis=_fmt(entry.basis_eur, quant=DECIMAL_TWO),
                pnl=_fmt(entry.pnl_eur, quant=DECIMAL_TWO),
            )
        )
    lines.append("")


def _append_forex_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    money_context: MoneyRenderContext | None = None,
) -> None:
    if summary.forex_review_required_rows <= 0:
        return

    lines.append("ВНИМАНИЕ: FOREX ОПЕРАЦИИ")
    lines.append("- Forex сделки (конвертиране на валута или търговия) НЕ са включени в изчисленията за Приложение 5 и Приложение 13")
    lines.append("- Тези операции са игнорирани от анализатора в тази версия")
    lines.append("- Forex ред с Review Status=NON-TAXABLE е обработен като нетаксируем и не изисква ръчна проверка")
    lines.append("- Forex ред с Review Status=TAXABLE, празен или непознат статус изисква ръчна проверка")
    lines.append(f"- брой Forex записи (общо): {summary.forex_ignored_rows}")
    lines.append(f"- брой Forex записи с NON-TAXABLE: {summary.forex_non_taxable_ignored_rows}")
    lines.append(f"- брой Forex записи с изисквана ръчна проверка: {summary.forex_review_required_rows}")
    lines.append(
        render_money_line(
            "- общ обем",
            Money(summary.forex_ignored_abs_proceeds_eur, "EUR"),
            quant=DECIMAL_TWO,
            context=money_context,
        )
    )
    lines.append("")


def _append_processing_notes_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    if summary.warnings:
        lines.append("Processing Notes")
        for warning in summary.warnings:
            lines.append(f"- {warning}")
        lines.append("")


def _append_proof_section(
    lines: list[str],
    *,
    result: AnalysisResult,
    money_context: MoneyRenderContext | None = None,
) -> None:
    summary = result.summary
    def _fmt_set(values: set[str]) -> str:
        cleaned = sorted(value for value in values if value.strip() != "")
        if not cleaned:
            return "-"
        return ", ".join(cleaned)

    lines.append("Audit Data")
    lines.append(f"- market classification mode: {summary.exchange_classification_mode or '-'}")
    if summary.tax_exempt_mode == TAX_MODE_LISTED_SYMBOL:
        lines.append(
            "- In listed_symbol mode, execution exchange does not participate in classification and is informational only."
        )
    lines.append(
        "- additional CLI EU-regulated markets: "
        f"{_fmt_set(summary.cli_eu_regulated_overrides)}"
    )
    lines.append(
        "- EU-regulated markets found in report: "
        f"{_fmt_set(summary.encountered_eu_regulated_exchanges)}"
    )
    lines.append(
        "- EU non-regulated markets found in report: "
        f"{_fmt_set(summary.encountered_eu_non_regulated_exchanges)}"
    )
    lines.append(
        "- non-EU markets found in report: "
        f"{_fmt_set(summary.encountered_non_eu_exchanges)}"
    )
    lines.append(
        "- unmapped markets found in report: "
        f"{_fmt_set(summary.encountered_unmapped_exchanges)}"
    )
    lines.append(
        "- invalid/unreadable market values found in report: "
        f"{_fmt_set(summary.encountered_invalid_exchange_values)}"
    )
    lines.append(f"- selected mode: {summary.tax_exempt_mode}")
    lines.append(f"- Appendix 8 dividend list mode: {summary.appendix8_dividend_list_mode}")
    lines.append(f"- report alias: {result.report_alias or '-'}")
    lines.append(f"- tax year: {summary.tax_year}")
    lines.append(f"- processed trades (in tax year): {summary.processed_trades_in_tax_year}")
    lines.append(f"- trades outside tax year: {summary.trades_outside_tax_year}")
    lines.append(f"- ignored rows without token C: {summary.ignored_non_closing_trade_rows}")
    lines.append(f"- review overrides (TAXABLE/NON-TAXABLE): {summary.review_status_overrides_rows}")
    lines.append(f"- unknown Review Status rows: {summary.unknown_review_status_rows}")
    if summary.unknown_review_status_values:
        lines.append(f"- unknown Review Status values: {', '.join(sorted(summary.unknown_review_status_values))}")
    lines.append(f"- interest processed rows: {summary.interest_processed_rows}")
    lines.append(f"- interest total rows skipped: {summary.interest_total_rows_skipped}")
    lines.append(f"- interest taxable rows: {summary.interest_taxable_rows}")
    lines.append(f"- interest non-taxable rows: {summary.interest_non_taxable_rows}")
    lines.append(f"- interest unknown rows: {summary.interest_unknown_rows}")
    lines.append(f"- dividends processed rows: {summary.dividends_processed_rows}")
    lines.append(f"- dividends total rows skipped: {summary.dividends_total_rows_skipped}")
    lines.append(f"- dividends cash rows: {summary.dividends_cash_rows}")
    lines.append(f"- dividends lieu rows: {summary.dividends_lieu_rows}")
    lines.append(f"- dividends unknown rows: {summary.dividends_unknown_rows}")
    lines.append(f"- withholding processed rows: {summary.withholding_processed_rows}")
    lines.append(f"- withholding total rows skipped: {summary.withholding_total_rows_skipped}")
    lines.append(f"- withholding dividend rows: {summary.withholding_dividend_rows}")
    lines.append(f"- withholding non-dividend rows: {summary.withholding_non_dividend_rows}")
    lines.append(f"- open positions summary rows: {summary.open_positions_summary_rows}")
    lines.append(f"- Appendix 8 Part I rows: {summary.open_positions_part1_rows}")
    lines.append(f"- dividend tax rate: {_fmt(summary.dividend_tax_rate)}")
    lines.append(
        "- interest withholding source found: "
        + ("YES" if summary.appendix_9_withholding_source_found else "NO")
    )
    if summary.tax_credit_debug_report_path:
        lines.append(f"- tax credit debug report: {summary.tax_credit_debug_report_path}")
    if money_context is not None:
        lines.extend(f"- {line}" for line in display_currency_technical_lines(money_context))
    lines.append("")


def _build_declaration_text(
    result: AnalysisResult,
    *,
    appendix9_allowable_credit_rate: Decimal = APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    money_context: MoneyRenderContext | None = None,
) -> str:
    summary = result.summary
    lines: list[str] = []
    _append_manual_check_section(lines, summary=summary)
    _append_forex_section(lines, summary=summary, money_context=money_context)
    _append_appendix5_section(lines, summary=summary, money_context=money_context)
    _append_appendix13_section(lines, summary=summary, money_context=money_context)
    _append_appendix6_section(lines, summary=summary, money_context=money_context)
    _append_appendix8_sections(lines, summary=summary, money_context=money_context)
    _append_appendix9_section(
        lines,
        summary=summary,
        appendix9_allowable_credit_rate=appendix9_allowable_credit_rate,
        money_context=money_context,
    )
    _append_review_section(lines, summary=summary, money_context=money_context)
    _append_appendix8_part1_note(lines, has_part1_rows=bool(summary.appendix_8_part1_rows))
    technical_lines: list[str] = []
    _append_processing_notes_section(technical_lines, summary=summary)
    _append_proof_section(technical_lines, result=result, money_context=money_context)
    _append_sanity_section(technical_lines, summary=summary)
    if technical_lines:
        lines.append(TECHNICAL_DETAILS_SEPARATOR)
        lines.append("")
        lines.extend(technical_lines)

    return "\n".join(lines).rstrip() + "\n"
