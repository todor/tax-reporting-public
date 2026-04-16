from __future__ import annotations

from decimal import Decimal

from ..constants import (
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    DECIMAL_TWO,
    TAX_MODE_EXECUTION_EXCHANGE,
)
from ..models import AnalysisResult, AnalysisSummary, BucketTotals
from ..shared import _fmt


def _sum_bucket(bucket: BucketTotals, sale_price_eur: Decimal, purchase_eur: Decimal, pnl_eur: Decimal) -> None:
    bucket.sale_price_eur += sale_price_eur
    bucket.purchase_eur += purchase_eur

    if pnl_eur > 0:
        bucket.wins_eur += pnl_eur
    elif pnl_eur < 0:
        bucket.losses_eur += -pnl_eur
    bucket.rows += 1


def _build_manual_check_reasons(summary: AnalysisSummary) -> list[str]:
    reasons: list[str] = []
    if summary.sanity_failures_count > 0:
        reasons.append(f"sanity checks failed: {summary.sanity_failures_count}")
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
    if summary.forex_ignored_rows > 0:
        reasons.append(f"има {summary.forex_ignored_rows} Forex записа, които са изключени")
    return reasons


def _append_manual_check_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    reasons = _build_manual_check_reasons(summary)
    if not reasons:
        return

    lines.append("!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!")
    lines.append("СТАТУС: REQUIRED")
    for reason in reasons:
        lines.append(f"- {reason}")
    lines.append("")


def _append_sanity_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    lines.append("ПРОВЕРКА НА ИЗЧИСЛЕНИЯТА")
    lines.append(f"- Sanity checks: {'PASS' if summary.sanity_passed else 'FAIL'}")
    lines.append(f"- Проверени Trade редове (entry + exit): {summary.sanity_checked_closing_trades}")
    lines.append(f"- Проверени ClosedLot редове: {summary.sanity_checked_closedlots}")
    lines.append(f"- Проверени SubTotal редове: {summary.sanity_checked_subtotals}")
    lines.append(f"- Проверени Total редове: {summary.sanity_checked_totals}")
    lines.append(f"- Игнорирани Forex редове: {summary.sanity_forex_ignored_rows}")
    if summary.sanity_forex_ignored_rows > 0:
        lines.append("- ВНИМАНИЕ: Forex операциите не са включени в sanity проверките, защото са игнорирани от анализатора в тази версия.")
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


def _append_appendix5_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    app5 = summary.appendix_5
    lines.append("Приложение 5")
    lines.append("Таблица 2")
    lines.append(f"- продажна цена (EUR) - код 508: {_fmt(app5.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- цена на придобиване (EUR) - код 508: {_fmt(app5.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- печалба (EUR) - код 508: {_fmt(app5.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR) - код 508: {_fmt(app5.losses_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- нетен резултат (EUR): {_fmt(app5.wins_eur - app5.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {app5.rows}")
    lines.append("")


def _append_appendix13_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    app13 = summary.appendix_13
    lines.append("Приложение 13")
    lines.append("Част ІІ")
    lines.append(f"- Брутен размер на дохода (EUR) - код 5081: {_fmt(app13.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Цена на придобиване (EUR) - код 5081: {_fmt(app13.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- печалба (EUR): {_fmt(app13.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR): {_fmt(app13.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- нетен резултат (EUR): {_fmt(app13.wins_eur - app13.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {app13.rows}")
    lines.append("")


def _append_appendix6_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    lines.append("Приложение 6")
    lines.append("Част I")
    lines.append("Информативни")
    lines.append(f"- Подател: Credit Interest (EUR): {_fmt(summary.appendix_6_credit_interest_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Подател: IBKR Managed Securities (SYEP) Interest (EUR): {_fmt(summary.appendix_6_syep_interest_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Подател: Other taxable (Review override) (EUR): {_fmt(summary.appendix_6_other_taxable_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Подател: Lieu Received (EUR): {_fmt(summary.appendix_6_lieu_received_eur, quant=DECIMAL_TWO)}")
    lines.append("Декларационна стойност")
    lines.append(f"- Обща сума на доходите с код 603: {_fmt(summary.appendix_6_code_603_eur, quant=DECIMAL_TWO)}")
    if summary.interest_unknown_rows > 0:
        lines.append("- НУЖЕН Е ПРЕГЛЕД: открити са непознати видове лихви")
        lines.append(f"- брой непознати редове: {summary.interest_unknown_rows}")
        lines.append(f"- непознати видове: {', '.join(sorted(summary.interest_unknown_types))}")
    if summary.dividends_unknown_rows > 0:
        lines.append("- НУЖЕН Е ПРЕГЛЕД: открити са неразпознати дивидентни описания")
        lines.append(f"- брой неразпознати редове: {summary.dividends_unknown_rows}")
    lines.append("")


def _append_appendix8_part1_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    lines.append("Приложение 8")
    lines.append("Част І, Акции, ред 1.N")
    if summary.appendix_8_part1_rows:
        for idx, part1 in enumerate(summary.appendix_8_part1_rows, start=1):
            lines.append(f"- Приложение 8, Част І, Акции, ред 1.{idx}")
            lines.append("- Вид: Акции")
            lines.append(f"- Държава: {part1.country_bulgarian}")
            lines.append(f"- Брой: {_fmt(part1.quantity)}")
            lines.append(
                f"- Дата и година на придобиване: {part1.acquisition_date.strftime('%d.%m.%Y')}"
            )
            lines.append(
                f"- Обща цена на придобиване в съответната валута: "
                f"{_fmt(part1.cost_basis_original, quant=DECIMAL_TWO)}"
            )
            lines.append(f"- В EUR: {_fmt(part1.cost_basis_eur, quant=DECIMAL_TWO)}")
            lines.append("")
    else:
        lines.append("- Няма разпознаваеми Open Positions Summary записи за данъчната година")
        lines.append("")
    lines.append("Напомняне: Към Приложение 8, Част I следва да се приложи файл с open positions.")
    lines.append("")


def _append_appendix8_part3_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    lines.append("Част III, ред 1.N")
    if summary.appendix_8_output_rows:
        for bucket in summary.appendix_8_output_rows:
            lines.append(
                f"- Наименование на лицето, изплатило дохода: {bucket.payer_name}"
            )
            lines.append(f"- Държава: {bucket.country_bulgarian}")
            lines.append("- Код вид доход: 8141")
            lines.append(f"- Код за прилагане на метод за избягване на двойното данъчно облагане: {bucket.method_code}")
            lines.append(f"- Брутен размер на дохода: {_fmt(bucket.gross_dividend_eur, quant=DECIMAL_TWO)}")
            lines.append("- Документално доказана цена на придобиване: ")
            lines.append(f"- Платен данък в чужбина: {_fmt(bucket.foreign_tax_paid_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Допустим размер на данъчния кредит: {_fmt(bucket.allowable_credit_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Размер на признатия данъчен кредит: {_fmt(bucket.recognized_credit_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Дължим данък, подлежащ на внасяне: {_fmt(bucket.tax_due_bg_eur, quant=DECIMAL_TWO)}")
            lines.append("")
    else:
        lines.append("- Няма разпознаваеми Cash Dividend записи за данъчната година")
        lines.append("")


def _append_appendix9_section(
    lines: list[str],
    *,
    summary: AnalysisSummary,
    appendix9_allowable_credit_rate: Decimal,
) -> None:
    lines.append("Приложение 9")
    lines.append("Част II")
    if summary.appendix_9_country_results:
        for country_iso in sorted(summary.appendix_9_country_results):
            country_result = summary.appendix_9_country_results[country_iso]
            lines.append(f"- Държава: {country_result.country_bulgarian}")
            lines.append("- Код вид доход: 603")
            lines.append(
                f"- Брутен размер на дохода (включително платеният данък): "
                f"{_fmt(country_result.aggregated_gross_eur, quant=DECIMAL_TWO)}"
            )
            lines.append("- Нормативно определени разходи: 0")
            lines.append("- Задължителни осигурителни вноски: 0")
            lines.append(f"- Годишна данъчна основа: {_fmt(country_result.aggregated_gross_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Платен данък в чужбина: {_fmt(country_result.aggregated_foreign_tax_paid_eur, quant=DECIMAL_TWO)}")
            lines.append(
                f"- Допустим размер на данъчния кредит: "
                f"{_fmt(country_result.allowable_credit_aggregated_eur, quant=DECIMAL_TWO)}"
            )
            lines.append(
                f"- Размер на признатия данъчен кредит: "
                f"{_fmt(country_result.recognized_credit_correct_eur, quant=DECIMAL_TWO)}"
            )
            lines.append("- № и дата на документа за дохода и съответния данък: R-185 / Activity Statement")
            lines.append("")
        return

    lines.append("- Държава: Ирландия")
    lines.append("- Код вид доход: 603")
    lines.append(
        f"- Брутен размер на дохода (включително платеният данък): "
        f"{_fmt(summary.appendix_9_credit_interest_eur, quant=DECIMAL_TWO)}"
    )
    lines.append("- Нормативно определени разходи: 0")
    lines.append("- Задължителни осигурителни вноски: 0")
    lines.append(f"- Годишна данъчна основа: {_fmt(summary.appendix_9_credit_interest_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Платен данък в чужбина: {_fmt(summary.appendix_9_withholding_paid_eur, quant=DECIMAL_TWO)}")
    lines.append(
        f"- Допустим размер на данъчния кредит: "
        f"{_fmt(summary.appendix_9_credit_interest_eur * appendix9_allowable_credit_rate, quant=DECIMAL_TWO)}"
    )
    lines.append(
        f"- Размер на признатия данъчен кредит: "
        f"{_fmt(min(summary.appendix_9_withholding_paid_eur, summary.appendix_9_credit_interest_eur * appendix9_allowable_credit_rate), quant=DECIMAL_TWO)}"
    )
    lines.append("- № и дата на документа за дохода и съответния данък: R-185 / Activity Statement")
    lines.append("")


def _append_review_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    if summary.tax_exempt_mode != TAX_MODE_EXECUTION_EXCHANGE:
        return

    review = summary.review
    lines.append("РЪЧНА ПРОВЕРКА (ИЗКЛЮЧЕНИ ОТ АВТОМАТИЧНИТЕ ТАБЛИЦИ)")
    lines.append(f"- изключени записи: {summary.review_rows}")
    lines.append(f"- продажна цена (EUR): {_fmt(review.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- цена на придобиване (EUR): {_fmt(review.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- печалба (EUR): {_fmt(review.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR): {_fmt(review.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- нетен резултат (EUR): {_fmt(review.wins_eur - review.losses_eur, quant=DECIMAL_TWO)}")
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


def _append_forex_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    lines.append("ВНИМАНИЕ: FOREX ОПЕРАЦИИ")
    lines.append("- Forex сделки (конвертиране на валута или търговия) НЕ са включени в изчисленията за Приложение 5 и Приложение 13")
    lines.append("- Тези операции са игнорирани от анализатора в тази версия")
    lines.append("- При наличие на значителни Forex операции е необходима ръчна проверка")
    lines.append(f"- брой Forex записи: {summary.forex_ignored_rows}")
    lines.append(f"- общ обем (EUR): {_fmt(summary.forex_ignored_abs_proceeds_eur, quant=DECIMAL_TWO)}")
    lines.append("")


def _append_proof_section(lines: list[str], *, result: AnalysisResult) -> None:
    summary = result.summary
    lines.append("Доказателствена част")
    lines.append(f"- избран режим: {summary.tax_exempt_mode}")
    lines.append(f"- Приложение 8 дивидентен режим: {summary.appendix8_dividend_list_mode}")
    lines.append(f"- report alias: {result.report_alias or '-'}")
    lines.append(f"- данъчна година: {summary.tax_year}")
    lines.append(f"- обработени сделки (в данъчната година): {summary.processed_trades_in_tax_year}")
    lines.append(f"- сделки извън данъчната година: {summary.trades_outside_tax_year}")
    lines.append(f"- игнорирани редове без token C: {summary.ignored_non_closing_trade_rows}")
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
    lines.append(f"- използвани execution борси: {', '.join(sorted(summary.exchanges_used)) or '-'}")
    lines.append(f"- review execution борси: {', '.join(sorted(summary.review_exchanges)) or '-'}")
    lines.append("")

    if summary.warnings:
        lines.append("Warnings")
        for warning in summary.warnings:
            lines.append(f"- {warning}")
        lines.append("")


def _build_declaration_text(
    result: AnalysisResult,
    *,
    appendix9_allowable_credit_rate: Decimal = APPENDIX_9_ALLOWABLE_CREDIT_RATE,
) -> str:
    summary = result.summary
    lines: list[str] = []
    _append_manual_check_section(lines, summary=summary)
    _append_sanity_section(lines, summary=summary)
    _append_appendix5_section(lines, summary=summary)
    _append_appendix13_section(lines, summary=summary)
    _append_appendix6_section(lines, summary=summary)
    _append_appendix8_part1_section(lines, summary=summary)
    _append_appendix8_part3_section(lines, summary=summary)
    _append_appendix9_section(
        lines,
        summary=summary,
        appendix9_allowable_credit_rate=appendix9_allowable_credit_rate,
    )
    _append_review_section(lines, summary=summary)
    _append_forex_section(lines, summary=summary)
    _append_proof_section(lines, result=result)

    return "\n".join(lines).rstrip() + "\n"
