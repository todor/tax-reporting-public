from __future__ import annotations

import re
from dataclasses import dataclass, field
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
from integrations.shared.rendering.common import (
    Money,
    MoneyRenderContext,
    append_technical_details,
    render_manual_review_section,
    render_money_line,
)
from integrations.shared.rendering.display_currency import display_currency_technical_lines
from integrations.shared.contracts import MainReportNote
from integrations.shared.spb8 import render_spb8_section

from ..constants import (
    APPENDIX8_COUNTRY_MODE_PAYER_LABEL,
    APPENDIX8_LIST_MODE_COUNTRY,
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    DECIMAL_EIGHT,
    DECIMAL_TWO,
    NEGATIVE_PIL_MODE_POSITION_AWARE,
    NEGATIVE_PIL_STATUS_DEFER,
    NEGATIVE_PIL_STATUS_IGNORE,
    NEGATIVE_PIL_STATUS_REVIEW,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTING_EXCHANGE,
    ZERO,
)
from ..models import AnalysisResult, AnalysisSummary, BucketTotals, PositiveWhtCorrection
from ..shared import _fmt

_OPEN_POSITION_MISMATCH_RE = re.compile(
    r"OPEN_POSITION_TRADE_QTY_MISMATCH:\s+asset=(?P<asset>\S+)\s+symbol=(?P<symbol>\S+)\s+"
    r"prior_qty=(?P<prior>[-0-9.]+)\s+trade_delta_qty=(?P<trade_delta>[-0-9.]+)\s+"
    r"(?:transfer_delta_qty=(?P<transfer_delta>[-0-9.]+)\s+)?"
    r"(?:corporate_action_delta_qty=(?P<corporate_action_delta>[-0-9.]+)\s+)?"
    r"expected_open_qty=(?P<expected>[-0-9.]+)\s+actual_open_qty=(?P<actual>[-0-9.]+)\s+"
    r"diff=(?P<diff>[-0-9.]+)"
)

_PRIOR_YEAR_CORRECTIONS_SECTION_TITLE = "Корекции към предходни години"
_APPENDIX8_PRIOR_YEAR_CORRECTIONS_TITLE = "Приложение 8, Част III"
_APPENDIX8_PRIOR_YEAR_CORRECTIONS_METHODOLOGY_TITLE = (
    "Корекции към предходни години — Приложение 8, Част III"
)


@dataclass(slots=True)
class _PositiveWhtCorrectionGroup:
    year: int
    payer_name: str
    country_bulgarian: str
    income_code: str
    method_code: str
    amount_eur: Decimal = ZERO
    row_numbers: list[int] = field(default_factory=list)
    review_required: bool = False
    review_reasons: set[str] = field(default_factory=set)


def _positive_wht_correction_payer(summary: AnalysisSummary, correction: PositiveWhtCorrection) -> str:
    if correction.review_required:
        return correction.payer_name
    if summary.appendix8_dividend_list_mode == APPENDIX8_LIST_MODE_COUNTRY:
        return APPENDIX8_COUNTRY_MODE_PAYER_LABEL
    return correction.payer_name


def _positive_wht_correction_groups(summary: AnalysisSummary) -> list[_PositiveWhtCorrectionGroup]:
    groups: dict[tuple[int, str, str, str, str, bool], _PositiveWhtCorrectionGroup] = {}
    for correction in summary.appendix_8_positive_wht_corrections:
        payer_name = _positive_wht_correction_payer(summary, correction)
        key = (
            correction.tax_date.year,
            payer_name,
            correction.country_bulgarian,
            correction.income_code,
            correction.method_code,
            correction.review_required,
        )
        group = groups.get(key)
        if group is None:
            group = _PositiveWhtCorrectionGroup(
                year=correction.tax_date.year,
                payer_name=payer_name,
                country_bulgarian=correction.country_bulgarian,
                income_code=correction.income_code,
                method_code=correction.method_code,
                review_required=correction.review_required,
            )
            groups[key] = group
        group.amount_eur += correction.amount_eur
        group.row_numbers.append(correction.row_number)
        if correction.review_reason:
            group.review_reasons.add(correction.review_reason)
    return sorted(
        groups.values(),
        key=lambda item: (
            item.year,
            item.country_bulgarian,
            item.payer_name,
            item.income_code,
            item.method_code,
            min(item.row_numbers) if item.row_numbers else 0,
        ),
    )


def _sum_bucket(
    bucket: BucketTotals,
    sale_price_eur: Decimal,
    purchase_eur: Decimal,
    pnl_eur: Decimal,
    *,
    count_row: bool = True,
) -> None:
    bucket.sale_price_eur += sale_price_eur
    bucket.purchase_eur += purchase_eur

    if pnl_eur > 0:
        bucket.wins_eur += pnl_eur
    elif pnl_eur < 0:
        bucket.losses_eur += -pnl_eur
    if count_row:
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
            summary.appendix_6_code_606_eur,
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

    lines.extend(render_manual_review_section(reasons))
    manual_actions: list[str] = []
    unmatched_actions = 0
    for warning in summary.warnings:
        match = _OPEN_POSITION_MISMATCH_RE.match(warning)
        if match:
            manual_actions.append(
                "Проверете Open Positions за {asset}/{symbol}: "
                "начално количество за периода {prior} + промяна от Trades/Order {trade_delta} + промяна от Transfers {transfer_delta} = очаквано {expected}, "
                "а отчетеното е {actual} (разлика {diff}).".format(
                    asset=match.group("asset"),
                    symbol=match.group("symbol"),
                    prior=match.group("prior"),
                    trade_delta=match.group("trade_delta"),
                    transfer_delta=match.group("transfer_delta") or "0",
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
                ),
                Appendix6Part1CodeTotal(
                    code="606",
                    amount=Money(summary.appendix_6_code_606_eur, "EUR"),
                ),
            ],
            part2_taxable_totals=[
                Appendix6Part2TaxableTotal(
                    code="603",
                    amount=Money(summary.appendix_6_code_603_eur, "EUR"),
                ),
                Appendix6Part2TaxableTotal(
                    code="606",
                    amount=Money(summary.appendix_6_code_606_eur, "EUR"),
                ),
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
            summary.appendix_6_positive_pil_eur,
            summary.appendix_6_positive_cfd_financing_eur,
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
        if not _is_zero_amount(summary.appendix_6_positive_pil_eur):
            lines.append(
                render_money_line(
                    "- Подател: Positive Payment in Lieu (PIL), код 606",
                    Money(summary.appendix_6_positive_pil_eur, "EUR"),
                    quant=DECIMAL_TWO,
                    context=money_context,
                )
            )
        if not _is_zero_amount(summary.appendix_6_positive_cfd_financing_eur):
            lines.append(
                render_money_line(
                    "- Подател: Positive CFD financing / CFD interest, код 606",
                    Money(summary.appendix_6_positive_cfd_financing_eur, "EUR"),
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
    if appendix_lines:
        lines.extend(appendix_lines)
        lines.append("")


def _append_prior_year_actions_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    correction_lines = _positive_wht_corrections_section_lines(summary)
    if not correction_lines:
        return
    lines.extend(correction_lines)
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
                    document_ref="",
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
                document_ref="",
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
            "- row={row} symbol={symbol} date={dt} listing={listing} "
            "listing_raw={listing_raw} mapped_classification={mapped_classification} "
            "execution={execution} reason={reason} proceeds_eur={proceeds} basis_eur={basis} pnl_eur={pnl}".format(
                row=entry.row_number,
                symbol=entry.symbol,
                dt=entry.trade_date,
                listing=entry.listing_exchange,
                listing_raw=entry.listing_exchange_raw,
                mapped_classification=entry.mapped_listing_classification,
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
    _ = money_context
    if summary.forex_ignored_rows <= 0:
        return

    lines.append("Forex операции")
    lines.append("- Forex сделки (конвертиране на валута или търговия) не се включват автоматично в Приложение 5/13 в тази версия.")
    lines.append("- Forex редове с Review Status=NON-TAXABLE се третират като нетаксируеми.")
    lines.append("- Forex редове с Review Status=NON-TAXABLE-FROM-HERE прилагат NON-TAXABLE за текущия и следващите празни Forex редове.")
    lines.append("- Forex редове с Review Status=TAXABLE, празен или непознат статус изискват ръчен преглед.")
    lines.append("- Forex редове с Review Status=TAXABLE-FROM-HERE прилагат TAXABLE за текущия и следващите празни Forex редове.")
    lines.append("")


def cfd_pil_policy_notes(summary: AnalysisSummary) -> list[str]:
    notes: list[str] = []
    if summary.cfd_trade_rows > 0 or summary.cfd_open_position_rows > 0:
        notes.append(
            "CFD сделките са третирани като финансови инструменти и са включени в "
            "Приложение 5, Таблица 2, код 508."
        )
        notes.append(
            "CFD позициите не се декларират в Приложение 8, защото не представляват "
            "реално притежание на акции/дялове."
        )
        notes.append(
            "При CFD не се използва пълният notional/номинал на договора като "
            "продажна цена или цена на придобиване. В Приложение 5 се включва "
            "икономическият резултат от CFD позицията — реализираният P/L и "
            "свързаните CFD financing/PIL корекции — защото няма реално "
            "придобиване/продажба на базовия актив."
        )
    if summary.cfd_financing_rows > 0:
        if summary.net_cfd_financing:
            notes.append(
                "CFD financing / CFD interest корекциите са третирани като част от "
                "CFD trading economics и са включени в Приложение 5, Таблица 2, код 508."
            )
            notes.append(
                "Положителните CFD financing стойности увеличават продажната страна, "
                "а отрицателните стойности увеличават разходната страна."
            )
        else:
            notes.append(
                "Нетиране на CFD financing / CFD interest е изключено чрез "
                "--no-net-cfd-financing."
            )
            notes.append("Положителните CFD financing стойности са декларирани в Приложение 6, код 606.")
            notes.append("Отрицателните CFD financing стойности не са включени в декларацията.")
    if summary.pil_negative_rows > 0:
        notes.append(f"Режим за отрицателен Payment in Lieu of Dividend (PIL): {summary.negative_pil_mode}.")
        if summary.pil_negative_net_rows > 0:
            notes.append(
                "Отрицателният Payment in Lieu of Dividend (PIL) е третиран като "
                "position-related cost/adjustment и е включен в Приложение 5, "
                "Таблица 2, код 508 само за редовете, чието окончателно решение е "
                "включване за текущата година (NET)."
            )
        if summary.pil_negative_ignore_rows > 0:
            ignored_rows = _negative_pil_ignore_row_refs(summary)
            ignored_text = f" Засегнати редове: {ignored_rows}." if ignored_rows else ""
            notes.append(
                "Има отрицателни Payment in Lieu редове, маркирани за игнориране чрез "
                "автоматичния режим или Review Status. Тези редове не са включени "
                f"в декларацията.{ignored_text}"
            )
        if summary.pil_negative_defer_rows > 0 or summary.pil_negative_review_rows > 0:
            affected_rows = _negative_pil_attention_row_refs(summary)
            affected_text = f" Засегнати редове: {affected_rows}." if affected_rows else ""
            notes.append(
                "Има отрицателни Payment in Lieu редове в CFD/PIL обработката, които са отложени "
                'или изискват ръчна проверка. Подробните инструкции са в секцията "Изискват '
                f'ръчен преглед".{affected_text}'
            )
    if summary.pil_appendix8_rows > 0:
        notes.append(
            'Информационно: IBKR редове "Payment in Lieu of Dividend" са третирани като '
            "дивидентоподобен доход и са включени в Приложение 8 заедно с чуждестранните дивиденти."
        )
    return notes


def _compact_row_numbers(row_numbers: list[int]) -> str:
    if not row_numbers:
        return ""
    sorted_rows = sorted(set(row_numbers))
    ranges: list[str] = []
    start = prev = sorted_rows[0]
    for row in sorted_rows[1:]:
        if row == prev + 1:
            prev = row
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = row
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(ranges)


def _negative_pil_attention_row_refs(summary: AnalysisSummary) -> str:
    return _compact_row_numbers(
        [
            decision.row_number
            for decision in summary.negative_pil_decisions
            if decision.final_status in {NEGATIVE_PIL_STATUS_REVIEW, NEGATIVE_PIL_STATUS_DEFER}
            or decision.auto_status in {NEGATIVE_PIL_STATUS_REVIEW, NEGATIVE_PIL_STATUS_DEFER}
        ]
    )


def _negative_pil_ignore_row_refs(summary: AnalysisSummary) -> str:
    return _compact_row_numbers(
        [
            decision.row_number
            for decision in summary.negative_pil_decisions
            if decision.final_status == NEGATIVE_PIL_STATUS_IGNORE
        ]
    )


def cfd_pil_policy_audit_lines(summary: AnalysisSummary) -> list[str]:
    if not any(
        (
            summary.cfd_trade_rows,
            summary.cfd_open_position_rows,
            summary.cfd_financing_detected_rows,
            summary.cfd_financing_rows,
            summary.cfd_financing_outside_tax_year_rows,
            summary.pil_detected_rows,
            summary.pil_positive_rows,
            summary.pil_negative_rows,
            summary.pil_outside_tax_year_rows,
        )
    ):
        return []
    included_pil_rows = summary.pil_appendix8_rows + summary.pil_negative_net_rows
    appendix5_cfd_financing_adjustment_rows = summary.cfd_financing_rows if summary.net_cfd_financing else 0
    appendix5_negative_pil_adjustment_rows = summary.pil_negative_net_rows
    appendix5_non_trade_adjustment_rows = (
        appendix5_cfd_financing_adjustment_rows + appendix5_negative_pil_adjustment_rows
    )
    lines = [
        "- CFD trades policy: Appendix 5 / Table 2 / code 508",
        "- CFD holdings policy: excluded_from_appendix_8",
        "- CFD SPB-8 policy: excluded_from_spb8",
        (
            "- CFD financing policy: "
            f"{'netted_to_appendix_5' if summary.net_cfd_financing else 'conservative_no_netting'}"
        ),
        (
            "- PIL policy: "
            f"{summary.negative_pil_mode}"
        ),
        "- Positive/dividend-like PIL policy: appendix_8_dividend_like_income",
        f"- CFD trade rows count: {summary.cfd_trade_rows}",
        f"- CFD open position rows excluded from Appendix 8/SPB-8: {summary.cfd_open_position_rows}",
        f"- Appendix 5 non-trade adjustment rows not counted as trades: {appendix5_non_trade_adjustment_rows}",
        (
            "- Appendix 5 CFD financing adjustment rows not counted as trades: "
            f"{appendix5_cfd_financing_adjustment_rows}"
        ),
        (
            "- Appendix 5 negative PIL adjustment rows not counted as trades: "
            f"{appendix5_negative_pil_adjustment_rows}"
        ),
        f"- CFD financing rows detected in statement: {summary.cfd_financing_detected_rows}",
        f"- CFD financing rows included in tax year: {summary.cfd_financing_rows}",
        f"- CFD financing rows outside tax year ignored: {summary.cfd_financing_outside_tax_year_rows}",
        f"- CFD financing positive EUR total: {_fmt(summary.cfd_financing_positive_eur)}",
        f"- CFD financing negative EUR total: {_fmt(summary.cfd_financing_negative_eur)}",
        f"- CFD financing negative skipped EUR total: {_fmt(summary.cfd_financing_negative_skipped_eur)}",
        f"- PIL rows detected in statement: {summary.pil_detected_rows}",
        f"- PIL rows included in tax year: {included_pil_rows}",
        f"- PIL rows included in Appendix 8 dividend flow: {summary.pil_appendix8_rows}",
        f"- PIL rows outside tax year ignored: {summary.pil_outside_tax_year_rows}",
        f"- Positive PIL EUR total: {_fmt(summary.pil_positive_eur)}",
        f"- Negative PIL EUR total: {_fmt(summary.pil_negative_eur)}",
        f"- Negative PIL NET rows: {summary.pil_negative_net_rows}",
        f"- Negative PIL DEFER rows: {summary.pil_negative_defer_rows}",
        f"- Negative PIL IGNORE rows: {summary.pil_negative_ignore_rows}",
        f"- Negative PIL REVIEW rows: {summary.pil_negative_review_rows}",
        f"- Negative PIL netted EUR total: {_fmt(summary.pil_negative_netted_eur)}",
        f"- Negative PIL deferred EUR total: {_fmt(summary.pil_negative_deferred_eur)}",
        f"- Negative PIL review EUR total: {_fmt(summary.pil_negative_review_eur)}",
        f"- Negative PIL closed short exposure ranges: {summary.negative_pil_closed_exposure_ranges}",
    ]
    if summary.negative_pil_decisions:
        lines.append("- Negative PIL decisions:")
        for decision in summary.negative_pil_decisions:
            ranges = "; ".join(decision.candidate_ranges) if decision.candidate_ranges else "-"
            lines.append(
                "  - "
                f"negative_pil_decision row={decision.row_number} date={decision.date.isoformat()} "
                f"currency={decision.currency} amount={_fmt(decision.amount)} "
                f"amount_eur={_fmt(decision.amount_eur)} "
                f"parsed_symbol={decision.parsed_symbol or '-'} parsed_isin={decision.parsed_isin or '-'} "
                f"likely_source={decision.likely_source or '-'} "
                f"accrual_link_status={decision.accrual_link_status or '-'} "
                f"accrual_asset_category={decision.accrual_asset_category or '-'} "
                f"accrual_asset_classification={decision.accrual_asset_classification or '-'} "
                f"accrual_symbol={decision.accrual_symbol or '-'} "
                f"accrual_ex_date={decision.accrual_ex_date.isoformat() if decision.accrual_ex_date else '-'} "
                f"accrual_pay_date={decision.accrual_pay_date.isoformat() if decision.accrual_pay_date else '-'} "
                f"accrual_amount={_fmt(decision.accrual_amount) if decision.accrual_amount is not None else '-'} "
                f"matching_date_source={decision.matching_date_source or '-'} "
                f"candidate_ranges={ranges} "
                f"auto_status={decision.auto_status or '-'} review_status={decision.review_status or '-'} "
                f"final_status={decision.final_status or '-'} tax_status={decision.tax_status}"
            )
    return lines


def _has_futures_current_year_policy(summary: AnalysisSummary) -> bool:
    return summary.futures_mtm_rows > 0


def _has_options_current_year_policy(summary: AnalysisSummary) -> bool:
    return any(
        (
            summary.option_closedlot_rows,
            summary.option_open_position_rows,
            summary.option_exercise_assignment_without_closedlot_rows,
            summary.option_unhandled_trade_rows,
        )
    )


def futures_policy_notes(summary: AnalysisSummary) -> list[str]:
    if not _has_futures_current_year_policy(summary):
        return []
    return [
        (
            "IBKR фючърсите се отчитат по дневна mark-to-market сетълмент логика. "
            "Затова за тях не се използват номиналните стойности на контрактите като "
            "„продажна“ и „придобивна“ цена. В Приложение 5, Таблица 2 сумите са "
            "представени като парично сетълнати MTM печалби и загуби за годината: "
            "положителните MTM резултати са включени в „продажна цена“, а отрицателните "
            "MTM резултати — в „цена на придобиване“. Trades редовете за фючърси не се "
            "добавят отделно, за да не се получи двойно броене."
        )
    ]


def futures_policy_audit_lines(summary: AnalysisSummary) -> list[str]:
    if summary.futures_trade_rows <= 0 and summary.futures_mtm_rows <= 0:
        return []
    return [
        "- Futures policy: Mark-to-Market Performance Summary / cash-settled MTM",
        "- Futures Appendix 5 mapping: positive MTM to sale value; negative MTM to acquisition value",
        "- Futures Trades rows policy: not used for taxable Futures P/L to avoid double counting",
        "- Futures Cash Report / Cash Settling MTM policy: not used for taxable Futures P/L",
        "- Futures SPB-8 policy: excluded_from_spb8",
        f"- Futures Trades rows count: {summary.futures_trade_rows}",
        f"- Futures MTM rows count: {summary.futures_mtm_rows}",
        f"- Futures MTM total EUR: {_fmt(summary.futures_mtm_total_eur)}",
        f"- Futures MTM positive EUR total: {_fmt(summary.futures_mtm_positive_eur)}",
        f"- Futures MTM negative EUR total: {_fmt(summary.futures_mtm_negative_eur)}",
        f"- Futures MTM rows with non-zero Other: {summary.futures_mtm_other_rows}",
        f"- Futures MTM Other EUR total: {_fmt(summary.futures_mtm_other_eur)}",
    ]


def options_policy_notes(summary: AnalysisSummary) -> list[str]:
    if not _has_options_current_year_policy(summary):
        return []
    return [
        "Реализираните печалби/загуби от затворени или изтекли опции са включени в Приложение 5, Таблица 2, код 508.",
        "За Приложение 5 се използват стойностите за продажна цена и цена на придобиване, както при останалите затворени позиции.",
        "MTM стойностите за опции не се използват за данъчната калкулация.",
        (
            "При упражняване/assignment не се създава отделен данъчен резултат за опцията; "
            "премията се очаква да е отразена в базата/постъпленията на получения/продадения базов актив."
        ),
    ]


def options_policy_audit_lines(summary: AnalysisSummary) -> list[str]:
    if summary.option_trade_rows <= 0 and summary.option_closedlot_rows <= 0 and summary.option_open_position_rows <= 0:
        return []
    lines = [
        "- Equity/index options policy: Appendix 5 / Table 2 / code 508",
        "- Equity/index options calculation source: Trades/ClosedLot",
        "- Equity/index options MTM policy: ignored for tax calculation",
        "- Equity/index options SPB-8 policy: excluded_from_spb8",
        f"- Equity/index option Trade rows count: {summary.option_trade_rows}",
        f"- Equity/index option ClosedLot rows included: {summary.option_closedlot_rows}",
        f"- Equity/index option open position rows excluded from Appendix 8/SPB-8: {summary.option_open_position_rows}",
        f"- Equity/index option exercise/assignment rows without ClosedLot: {summary.option_exercise_assignment_without_closedlot_rows}",
        f"- Equity/index option unhandled Trade rows: {summary.option_unhandled_trade_rows}",
    ]
    for currency, amount in sorted(summary.option_closedlot_realized_pl_by_currency.items()):
        lines.append(f"- Equity/index option ClosedLot realized P/L {currency} total: {_fmt(amount)}")
    return lines


def _append_options_notes_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    notes = options_policy_notes(summary)
    if not notes:
        return
    lines.append("Опции върху акции и индекси")
    lines.extend(f"- {note}" for note in notes)
    lines.append("")


def _append_spb8_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    section = render_spb8_section(summary.spb8_rows, notes=summary.spb8_notes, include_notes=False)
    if not section:
        return
    lines.extend(section)
    lines.append("")


def _append_methodology_notes_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    notes = [
        note
        for note in analysis_settings_main_report_notes(summary)
        if note.category == "methodology" and note.text.strip()
    ]
    notes.extend(
        MainReportNote(
            section_title="СПБ-8",
            text=note,
            analyzer_alias="ibkr",
            category="methodology",
        )
        for note in summary.spb8_notes
        if note.strip()
    )
    if not notes:
        return

    grouped: dict[str, list[str]] = {}
    ordered_titles: list[str] = []
    seen_by_section: dict[str, set[str]] = {}
    for note in notes:
        section_title = note.section_title.strip()
        text = note.text.strip()
        if not section_title or not text:
            continue
        seen = seen_by_section.setdefault(section_title, set())
        if text in seen:
            continue
        seen.add(text)
        if section_title not in grouped:
            grouped[section_title] = []
            ordered_titles.append(section_title)
        grouped[section_title].append(text)

    if not grouped:
        return
    lines.append("Методологични бележки")
    for section_title in ordered_titles:
        lines.append("")
        lines.append(section_title)
        lines.extend(f"- {text}" for text in grouped[section_title])
    lines.append("")


def _append_cfd_pil_notes_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    notes = cfd_pil_policy_notes(summary)
    if not notes:
        return
    lines.append("CFD и PIL")
    lines.extend(f"- {note}" for note in notes)
    lines.append("")


def _append_futures_notes_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    notes = futures_policy_notes(summary)
    if not notes:
        return
    lines.append("Фючърси — IBKR daily cash-settled MTM")
    lines.extend(f"- {note}" for note in notes)
    lines.append("")


def _tax_exempt_mode_description(tax_exempt_mode: str) -> str:
    if tax_exempt_mode == TAX_MODE_LISTING_EXCHANGE:
        return (
            "При този режим данъчното третиране се определя според пазара, "
            "на който е листнат инструментът, а борсата на изпълнение е само информативна."
        )
    if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
        return (
            "При този режим данъчното третиране се определя според борсата на изпълнение "
            "на сделката."
        )
    return "Прегледайте избрания режим ръчно."


def _classification_mode_description(mode: str) -> str:
    if "OPEN_WORLD" in mode:
        return "Неразпознатите пазари не се приемат автоматично за регулирани пазари и трябва да бъдат проверени."
    if "CLOSED_WORLD" in mode:
        return (
            "Неразпознатите четими пазари се третират като нерегулирани, освен ако са вградени "
            "като регулирани или са подадени с CLI override."
        )
    return "Прегледайте режима за класификация на пазарите ръчно."


def _fmt_set_bg(values: set[str]) -> str:
    cleaned = sorted(value for value in values if value.strip() != "")
    return ", ".join(cleaned) if cleaned else "няма"


def _positive_wht_corrections_note_text(summary: AnalysisSummary) -> str:
    correction_lines = _positive_wht_corrections_section_lines(summary)
    if not correction_lines:
        return ""
    return "\n".join(correction_lines[1:])


def _positive_wht_corrections_methodology_text() -> str:
    return "\n".join(
        [
            "В режим prior-year-correction положителните IBKR Withholding Tax редове се третират като корекции "
            "към вече деклариран чуждестранен данък за предходни години.",
            "Какво да направите за съответната предходна декларация:",
            "- Платен данък в чужбина = max(0, Платен данък в чужбина преди корекция - Платен данък в чужбина (корекция))",
            "- Размер на признатия данъчен кредит = min(Платен данък в чужбина, Допустим размер на данъчния кредит)",
            "- Дължим данък, подлежащ на внасяне = Допустим размер на данъчния кредит - Размер на признатия данъчен кредит",
            "Инструментът не променя автоматично вече подадени декларации.",
        ]
    )


def _positive_wht_mode_methodology_text(summary: AnalysisSummary) -> str:
    lines = [
        f"Открити положителни IBKR Withholding Tax редове за дивиденти: {summary.positive_wht_rows_found}.",
        f"Избран режим: {summary.positive_wht_mode}.",
    ]
    if summary.positive_wht_mode == "prior-year-correction":
        lines.extend(
            [
                "В този режим положителните WHT редове с дата в текущата данъчна година се приспадат от "
                "чуждестранния данък за текущия отчет.",
                "Положителните WHT редове с дата в предходни години се показват отделно в секцията "
                '"Корекции към предходни години".',
            ]
        )
    else:
        lines.extend(
            [
                "В режим current-year-net инструментът приспада положителните WHT редове от чуждестранния "
                "данък за текущия отчет.",
                "Ако искате да ги разглеждате като корекции към вече деклариран чуждестранен данък за "
                "предходни години, използвайте --positive-wht-mode prior-year-correction.",
                "В aggregate режим може да зададете IBKR-специфичен override с "
                "--ibkr-positive-wht-mode prior-year-correction, ако общият режим трябва да остане различен.",
                "Ако използвате прагматичния current-year-net подход, не е нужно да прехвърляте тези редове "
                "ръчно към предходна декларация.",
            ]
        )
    return "\n".join(lines)


def _positive_wht_corrections_section_lines(summary: AnalysisSummary) -> list[str]:
    if not summary.appendix_8_positive_wht_corrections:
        return []
    lines: list[str] = []
    lines.append(_PRIOR_YEAR_CORRECTIONS_SECTION_TITLE)
    lines.append(_APPENDIX8_PRIOR_YEAR_CORRECTIONS_TITLE)
    grouped_by_year: dict[int, list[_PositiveWhtCorrectionGroup]] = {}
    for correction_group in _positive_wht_correction_groups(summary):
        grouped_by_year.setdefault(correction_group.year, []).append(correction_group)
    for correction_year in sorted(grouped_by_year):
        if lines[-1] != _APPENDIX8_PRIOR_YEAR_CORRECTIONS_TITLE:
            lines.append("")
        lines.append(f"- Година: {correction_year}")
        for correction in grouped_by_year[correction_year]:
            review_reason = "; ".join(sorted(correction.review_reasons))
            review_suffix = f" (нужен преглед: {review_reason})" if correction.review_required else ""
            lines.append(
                f"  - {correction.payer_name}{review_suffix}; "
                f"{correction.country_bulgarian}; код {correction.income_code}; метод {correction.method_code}; "
                f"корекция {_fmt(correction.amount_eur, quant=DECIMAL_EIGHT)} EUR; "
                f"редове {_compact_row_numbers(correction.row_numbers)}"
            )
    lines.extend(
        [
            "",
            f"Тази секция не се попълва в текущата декларация за {summary.tax_year}; "
            "използва се само за проверка/корекция на вече подадени декларации за предходни години.",
        ]
    )
    return lines


def analysis_settings_main_report_notes(summary: AnalysisSummary) -> list[MainReportNote]:
    market_section = "IBKR — класификация на пазари"
    instrument_methods_section = "IBKR — използвани методи за инструменти"
    pil_review_section = "IBKR — проверки за Payment in Lieu"
    notes = [
        MainReportNote(
            section_title="Настройки на анализа",
            text=(
                "Класификация на IBKR сделките за данъчно освобождаване: "
                f"{summary.tax_exempt_mode}. {_tax_exempt_mode_description(summary.tax_exempt_mode)}"
            ),
            analyzer_alias="ibkr",
            category="duplicate_individual_context",
        )
    ]
    if summary.positive_wht_rows_found > 0:
        notes.append(
            MainReportNote(
                section_title="IBKR — положителен Withholding Tax",
                text=_positive_wht_mode_methodology_text(summary),
                analyzer_alias="ibkr",
                category="methodology",
            )
        )
    if summary.corporate_actions_recognized_rows > 0:
        notes.append(
            MainReportNote(
                section_title="IBKR — Corporate Actions",
                text=(
                    "Разпознати са IBKR Merged(Acquisition) WITH корпоративни събития. "
                    "За този поддържан модел инструментът третира събитието като необлагаема "
                    "корпоративна операция, прилага премахнатите/получените количества към съответните "
                    "ISIN-и и ги използва за Open Positions reconciliation и СПБ-8 реконструкция. "
                    "Редовете за merger/acquisition не създават облагаем доход или реализирана печалба/загуба "
                    "от Proceeds/Value/Realized P/L."
                ),
                analyzer_alias="ibkr",
                category="methodology",
            )
        )
    notes.append(
        MainReportNote(
            section_title=market_section,
            text=(
                f"Режим за данъчно освобождаване: {summary.tax_exempt_mode}. "
                f"{_tax_exempt_mode_description(summary.tax_exempt_mode)}"
            ),
            analyzer_alias="ibkr",
            category="setting",
        )
    )
    notes.append(
        MainReportNote(
            section_title=market_section,
            text=(
                f"Режим за класификация на пазарите: {summary.exchange_classification_mode or '-'}. "
                f"{_classification_mode_description(summary.exchange_classification_mode)}"
            ),
            analyzer_alias="ibkr",
            category="setting",
        )
    )
    notes.append(
        MainReportNote(
            section_title=market_section,
            text=(
                "Разпознати регулирани пазари от ЕС в отчета: "
                f"{_fmt_set_bg(summary.encountered_eu_regulated_exchanges)}."
            ),
            analyzer_alias="ibkr",
            category="info",
        )
    )
    notes.append(
        MainReportNote(
            section_title=market_section,
            text=(
                "Разпознати нерегулирани/други пазари от ЕС за целите на данъчното освобождаване: "
                f"{_fmt_set_bg(summary.encountered_eu_non_regulated_exchanges)}."
            ),
            analyzer_alias="ibkr",
            category="info",
        )
    )
    notes.append(
        MainReportNote(
            section_title=market_section,
            text=f"Разпознати пазари извън ЕС: {_fmt_set_bg(summary.encountered_non_eu_exchanges)}.",
            analyzer_alias="ibkr",
            category="info",
        )
    )
    notes.append(
        MainReportNote(
            section_title=market_section,
            text=(
                "Неразпознати пазари: "
                f"{_fmt_set_bg(summary.encountered_unmapped_exchanges)}; "
                "невалидни/нечетими стойности: "
                f"{_fmt_set_bg(summary.encountered_invalid_exchange_values)}."
            ),
            analyzer_alias="ibkr",
            category="review" if summary.encountered_unmapped_exchanges or summary.encountered_invalid_exchange_values else "info",
        )
    )
    if summary.report_date_format_label:
        notes.append(
            MainReportNote(
                section_title=market_section,
                text=f"Разпознат формат на датите в IBKR отчета: {summary.report_date_format_label}.",
                analyzer_alias="ibkr",
                category="info",
            )
        )
    if summary.appendix_8_positive_wht_corrections:
        notes.append(
            MainReportNote(
                section_title=_PRIOR_YEAR_CORRECTIONS_SECTION_TITLE,
                text=_positive_wht_corrections_note_text(summary),
                analyzer_alias="ibkr",
                category="appendix8_corrections",
            )
        )
        notes.append(
            MainReportNote(
                section_title=_APPENDIX8_PRIOR_YEAR_CORRECTIONS_METHODOLOGY_TITLE,
                text=_positive_wht_corrections_methodology_text(),
                analyzer_alias="ibkr",
                category="methodology",
            )
        )
    if cfd_pil_policy_notes(summary):
        notes.append(
            MainReportNote(
                section_title=instrument_methods_section,
                text=(
                    "CFD/PIL: използва се реализиран икономически резултат и свързани "
                    "CFD financing/PIL корекции; пълният notional/номинал не се използва "
                    "като продажна/придобивна стойност."
                ),
                analyzer_alias="ibkr",
                category="setting",
            )
        )
    if (
        summary.negative_pil_mode == NEGATIVE_PIL_MODE_POSITION_AWARE
        and summary.negative_pil_closed_exposure_ranges > 0
    ):
        notes.append(
            MainReportNote(
                section_title=pil_review_section,
                text=(
                    "Проверете всички предходни години, не само непосредствено предходната, "
                    "за отрицателни Payment in Lieu редове, маркирани като отложени. "
                    "Ако свързаната позиция е затворена през текущата година, тези редове може да следва "
                    "да бъдат включени при изчисляване на резултата за текущата година."
                ),
                analyzer_alias="ibkr",
                category="info",
            )
        )
    if _has_futures_current_year_policy(summary):
        notes.append(
            MainReportNote(
                section_title=instrument_methods_section,
                text=(
                    "Фючърси: използва се Mark-to-Market Performance Summary / "
                    "cash-settled MTM за данъчната година."
                ),
                analyzer_alias="ibkr",
                category="setting",
            )
        )
        for note in futures_policy_notes(summary):
            notes.append(
                MainReportNote(
                    section_title="Фючърси — IBKR daily cash-settled MTM",
                    text=note,
                    analyzer_alias="ibkr",
                    category="methodology",
                )
            )
    if _has_options_current_year_policy(summary):
        notes.append(
            MainReportNote(
                section_title=instrument_methods_section,
                text=(
                    "Опции: използват се реализирани резултати от затворени/изтекли опции; "
                    "MTM стойностите не се използват за данъчната калкулация."
                ),
                analyzer_alias="ibkr",
                category="setting",
            )
        )
        for note in options_policy_notes(summary):
            notes.append(
                MainReportNote(
                    section_title="Опции върху акции и индекси",
                    text=note,
                    analyzer_alias="ibkr",
                    category="methodology",
                )
            )
    return notes


def _append_configuration_section(lines: list[str], *, summary: AnalysisSummary) -> None:
    notes = [
        note
        for note in analysis_settings_main_report_notes(summary)
        if note.category not in {"methodology", "duplicate_individual_context", "appendix8_corrections"}
    ]
    if not notes:
        return
    grouped: dict[str, list[str]] = {}
    for note in notes:
        grouped.setdefault(note.section_title, []).append(note.text)
    lines.append("Настройки, режими и проверки на анализа")
    for section_title, section_notes in grouped.items():
        lines.append(section_title)
        for text in section_notes:
            lines.append(f"- {text}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
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
    if summary.tax_exempt_mode == TAX_MODE_LISTING_EXCHANGE:
        lines.append(
            "- In listing_exchange mode, execution exchange does not participate in classification and is informational only."
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
    if summary.unsupported_trade_asset_categories:
        lines.append(
            "- unsupported Trades asset categories skipped: "
            f"{', '.join(sorted(summary.unsupported_trade_asset_categories))}"
        )
        lines.append(
            "- unsupported Trades rows skipped: "
            f"{summary.unsupported_trade_asset_category_rows}"
        )
    lines.extend(cfd_pil_policy_audit_lines(summary))
    lines.extend(futures_policy_audit_lines(summary))
    lines.extend(options_policy_audit_lines(summary))
    if summary.report_date_format_label:
        lines.append(f"- IBKR report date format: {summary.report_date_format_label}")
    if summary.report_date_format_reason:
        lines.append(f"- IBKR report date format reason: {summary.report_date_format_reason}")
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
    if summary.corporate_actions_rows > 0:
        lines.append(f"- Corporate Actions data rows: {summary.corporate_actions_rows}")
        lines.append(f"- Corporate Actions ignored rows: {summary.corporate_actions_ignored_rows}")
        lines.append(f"- Corporate Actions recognized non-taxable merger rows: {summary.corporate_actions_recognized_rows}")
        lines.append(f"- Corporate Actions unsupported rows: {summary.corporate_actions_unsupported_rows}")
    lines.append(f"- positive WHT mode: {summary.positive_wht_mode}")
    lines.append(f"- positive dividend withholding rows: {summary.withholding_positive_dividend_rows}")
    lines.append(f"- positive dividend withholding rows found: {summary.positive_wht_rows_found}")
    lines.append(f"- positive dividend withholding rows netted: {summary.positive_wht_rows_netted}")
    lines.append(
        "- positive dividend withholding rows listed as prior-year corrections: "
        f"{summary.positive_wht_rows_prior_year_corrections}"
    )
    lines.append(f"- positive dividend withholding rows mapped: {summary.positive_wht_rows_mapped}")
    lines.append(f"- positive dividend withholding rows requiring review: {summary.positive_wht_rows_unmapped}")
    lines.append(
        "- Appendix 8 withholding buckets with non-positive net tax paid: "
        f"{summary.withholding_non_positive_net_buckets}"
    )
    for correction in summary.appendix_8_positive_wht_corrections:
        lines.append(
            "- positive WHT prior-year correction: "
            f"row={correction.row_number}; date={correction.tax_date.isoformat()}; "
            f"amount={_fmt(correction.amount)} {correction.currency}; amount_eur={_fmt(correction.amount_eur)}; "
            f"payer={correction.payer_name}; country={correction.country_english}; "
            f"review_required={correction.review_required}; reason={correction.review_reason or '-'}; "
            f"description={correction.description}"
        )
    lines.append(f"- open positions summary rows: {summary.open_positions_summary_rows}")
    lines.append(f"- Appendix 8 Part I rows: {summary.open_positions_part1_rows}")
    lines.append(f"- dividend tax rate: {_fmt(summary.dividend_tax_rate)}")
    lines.append(
        "- interest withholding source found: "
        + ("YES" if summary.appendix_9_withholding_source_found else "NO")
    )
    lines.append(
        "- interest withholding detail source found: "
        + ("YES" if summary.appendix_9_withholding_detail_source_found else "NO")
    )
    lines.append(f"- interest withholding detail paid EUR: {_fmt(summary.appendix_9_withholding_detail_paid_eur)}")
    lines.append(f"- positive interest withholding rows: {summary.appendix_9_positive_withholding_rows}")
    lines.append(
        "- Appendix 9 withholding buckets with non-positive net tax paid: "
        f"{summary.appendix_9_non_positive_net_buckets}"
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
    _append_configuration_section(lines, summary=summary)
    _append_manual_check_section(lines, summary=summary)
    _append_forex_section(lines, summary=summary, money_context=money_context)
    _append_cfd_pil_notes_section(lines, summary=summary)
    _append_futures_notes_section(lines, summary=summary)
    _append_options_notes_section(lines, summary=summary)
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
    _append_prior_year_actions_section(lines, summary=summary)
    _append_review_section(lines, summary=summary, money_context=money_context)
    _append_spb8_section(lines, summary=summary)
    _append_appendix8_part1_note(lines, has_part1_rows=bool(summary.appendix_8_part1_rows))
    _append_methodology_notes_section(lines, summary=summary)
    technical_lines: list[str] = []
    _append_processing_notes_section(technical_lines, summary=summary)
    _append_proof_section(technical_lines, result=result, money_context=money_context)
    _append_sanity_section(technical_lines, summary=summary)
    append_technical_details(lines, technical_lines)

    return "\n".join(lines).rstrip() + "\n"
