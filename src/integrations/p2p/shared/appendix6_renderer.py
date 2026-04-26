from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re

from integrations.shared.rendering.appendix6 import (
    Appendix6Part1CodeTotal,
    Appendix6Part1CompanyRow,
    Appendix6Part2TaxableTotal,
    Appendix6RenderData,
    render_appendix6,
)
from integrations.shared.rendering.common import Money, format_money
from integrations.shared.rendering.display_currency import (
    build_money_render_context,
    display_currency_technical_lines,
)

from .appendix6_models import P2PAppendix6Result

DECIMAL_TWO = Decimal("0.01")
TECHNICAL_DETAILS_SEPARATOR = (
    "------------------------------ Technical Details ------------------------------"
)


def _fmt_decimal(value: Decimal, *, quant: Decimal = DECIMAL_TWO) -> str:
    return format(value.quantize(quant, rounding=ROUND_HALF_UP), "f")


def _fmt_informative_value(value: Decimal | str) -> str:
    if isinstance(value, Decimal):
        return _fmt_decimal(value)
    return str(value)


def _is_zero_decimal(value: Decimal) -> bool:
    return value == Decimal("0")


def _is_informative_value_empty_or_zero(value: Decimal | str) -> bool:
    if isinstance(value, Decimal):
        return _is_zero_decimal(value)
    text = str(value).strip()
    if text in {"", "-"}:
        return True
    try:
        return Decimal(text) == Decimal("0")
    except Exception:  # noqa: BLE001
        return False


def _has_nonempty_informative_rows(result: P2PAppendix6Result) -> bool:
    return any(not _is_informative_value_empty_or_zero(info.value) for info in result.informative_rows)


_INFO_LABEL_BG: dict[str, str] = {
    "Reporting year": "Отчетна година",
    "Statement period": "Период на извлечението",
    "Income from interest received": "Получен доход от лихви",
    "Income from late interest received": "Получен доход от просрочени лихви",
    "Bonuses": "Бонуси",
    "Income/loss from secondary market discount/premium": "Доход/загуба от вторичен пазар (дисконт/премия)",
    "Net Sum from Appendix": "Нетна сума от приложението",
    "Total WHT from Appendix": "Общо удържан данък (WHT) от приложението",
    "Secondary-market mode used": "Използван режим за вторичен пазар",
    "Interest": "Лихва",
    "Bonus (Borrower)": "Бонус (Borrower)",
    "Penalty": "Неустойка",
    "Indemnity": "Обезщетение",
    "Bonus (EG)": "Бонус (EG)",
    "Secondary market profit/loss": "Печалба/загуба от вторичен пазар",
    "Sale fee": "Такса продажба",
    "AUM fee": "AUM такса",
    "Total": "Общо",
    "Payments Received": "Получени плащания",
    "Principal Amount": "Главница",
    "Late Payment Fees": "Такси за просрочено плащане",
    "Pending Payment interest": "Лихва по чакащо плащане",
    "Campaign rewards and bonuses": "Кампанийни награди и бонуси",
    "Interest income": "Лихвен доход",
    "Late fees": "Такси за просрочие",
    "Secondary market gains": "Печалби от вторичен пазар",
    "Campaign rewards": "Кампанийни награди",
    "Interest income iuvoSAVE": "Лихвен доход iuvoSAVE",
    "Secondary market fees": "Такси вторичен пазар",
    "Secondary market losses": "Загуби от вторичен пазар",
    "Secondary market aggregate used for code 606": "Агрегиран резултат от вторичен пазар, използван за код 606",
    "Early withdraw fees iuvoSAVE": "Такси за ранно теглене iuvoSAVE",
    "Earned interest": "Получена лихва",
    "Earned income from bonuses": "Получен доход от бонуси",
    "Taxes withheld": "Удържани данъци",
    "Capital invested": "Инвестиран капитал",
    "Capital withdrawn": "Изтеглен капитал",
    "Withdrawal fees": "Такси за теглене",
    "Profit realized": "Реализирана печалба",
    "Interest Accrued": "Начислена лихва",
    "Net profit": "Нетна печалба",
    "Bonus income received on Bondora account": "Получен бонус доход по Bondora сметка",
}


def _translate_info_label_bg(label: str) -> str:
    return _INFO_LABEL_BG.get(label, label)


def _split_label_and_currency(label: str) -> tuple[str, str | None]:
    match = re.match(r"^(?P<base>.+?)\s*\((?P<currency>[A-Z]{3})\)$", label.strip())
    if match:
        return match.group("base").strip(), match.group("currency")
    return label.strip(), None


def _translate_tax_message_bg(message: str) -> str | None:
    if message.startswith("reporting year in PDF (") and "differs from requested tax year" in message:
        match = re.search(r"reporting year in PDF \(([^)]+)\) differs from requested tax year \(([^)]+)\)", message)
        if match:
            return f"Отчетната година в PDF ({match.group(1)}) се различава от избраната данъчна година ({match.group(2)})."
    if message.startswith("Appendix total row mismatch vs parsed detail rows"):
        return "Несъответствие между общия ред в приложението и сумите от детайлните редове."
    if message == "Estateguru Bonus (Borrower) is negative and is not included in Appendix 6 code 606":
        return "Estateguru: отрицателният Bonus (Borrower) не се включва в Приложение 6, код 606."
    if message == "Estateguru Bonus (EG) is negative and is not included in Appendix 6 code 606":
        return "Estateguru: отрицателният Bonus (EG) не се включва в Приложение 6, код 606."
    if message == "Estateguru secondary-market aggregate is <= 0 and is omitted from Appendix 6 code 606":
        return "Estateguru: агрегираният резултат от вторичен пазар е <= 0 и се пропуска за Приложение 6, код 606."
    if message == "Estateguru Sale fee and AUM fee are informational only and are not mapped to Appendix 6 totals":
        return "Estateguru: Sale fee и AUM fee са само информативни и не участват в сумите за Приложение 6."
    if message == "Iuvo secondary market losses was positive in the report and was normalized as a negative value":
        return "Iuvo: стойността за secondary market losses е положителна и е нормализирана като отрицателна."
    if message == "Iuvo secondary market fees was positive in the report and was normalized as a negative value":
        return "Iuvo: стойността за secondary market fees е положителна и е нормализирана като отрицателна."
    if message == "Iuvo Campaign rewards is negative and is not included in Appendix 6 code 606":
        return "Iuvo: отрицателният Campaign rewards не се включва в Приложение 6, код 606."
    if message == "Iuvo secondary-market aggregate is <= 0 and is omitted from Appendix 6 code 606":
        return "Iuvo: агрегираният резултат от вторичен пазар е <= 0 и се пропуска за Приложение 6, код 606."
    if message == "Iuvo Early withdraw fees iuvoSAVE is informational only and is not mapped to Appendix 6 totals":
        return "Iuvo: Early withdraw fees iuvoSAVE са само информативни и не участват в сумите за Приложение 6."
    if message == "Lendermarket Campaign rewards and bonuses is negative and is not included in Appendix 6 code 606":
        return "Lendermarket: отрицателните campaign rewards and bonuses не се включват в Приложение 6, код 606."
    if message == "Robocash Earned income from bonuses is negative and is not included in Appendix 6 code 606":
        return "Robocash: отрицателният доход от бонуси не се включва в Приложение 6, код 606."
    if message.startswith("Robocash Taxes withheld is parsed but not mapped to structured tax-credit logic"):
        return "Robocash: удържаният данък е разчетен, но не е мапнат към структурирана логика за данъчен кредит поради липса на контекст за държава/платец."
    if message.startswith("Bondora Capital/Profit/Net portfolio fields are informational only"):
        return "Bondora: полетата за капитал/печалба/нетен резултат са само информативни и не участват в сумите за Приложение 6."
    return None


def build_appendix6_text(
    *,
    result: P2PAppendix6Result,
    tax_year: int,
    display_currency: str = "EUR",
    cache_dir: str | Path | None = None,
) -> str:
    money_context = build_money_render_context(
        tax_year=tax_year,
        display_currency=display_currency,
        cache_dir=cache_dir,
    )
    lines: list[str] = []

    appendix_lines = render_appendix6(
        Appendix6RenderData(
            part1_company_rows=[
                Appendix6Part1CompanyRow(
                    payer_name=row.payer_name,
                    payer_eik=row.payer_eik or "-",
                    code=row.code,
                    amount=Money(row.amount, "EUR"),
                )
                for row in result.part1_rows
            ],
            part1_code_totals=[
                Appendix6Part1CodeTotal(code="603", amount=Money(result.aggregate_code_603, "EUR")),
                Appendix6Part1CodeTotal(code="606", amount=Money(result.aggregate_code_606, "EUR")),
            ],
            part2_taxable_totals=[
                Appendix6Part2TaxableTotal(code="603", amount=Money(result.taxable_code_603, "EUR")),
                Appendix6Part2TaxableTotal(code="606", amount=Money(result.taxable_code_606, "EUR")),
            ],
            part3_withheld_tax=Money(result.withheld_tax, "EUR"),
        ),
        money_context=money_context,
    )
    lines.extend(appendix_lines)

    if _has_nonempty_informative_rows(result):
        if lines:
            lines.append("")
        lines.append("Информативни")
        for info in result.informative_rows:
            if _is_informative_value_empty_or_zero(info.value):
                continue
            base_label, currency = _split_label_and_currency(info.label)
            translated_label = _translate_info_label_bg(base_label)
            if isinstance(info.value, Decimal) and currency is not None:
                lines.append(
                    f"- {translated_label}: {format_money(Money(info.value, currency), context=money_context)}"
                )
            else:
                lines.append(f"- {translated_label}: {_fmt_informative_value(info.value)}")

    has_tax_notes = bool(result.warnings or result.informational_messages)
    if has_tax_notes:
        if lines:
            lines.append("")
        lines.append("Бележки по обработката")
        for idx, warning in enumerate(result.warnings, start=1):
            translated = _translate_tax_message_bg(warning)
            if translated is not None:
                lines.append(f"- {translated}")
            else:
                lines.append(
                    f"- Налична е бележка за ръчна проверка ({idx}); вижте \"Technical Details\" -> \"Processing Notes\"."
                )
        for idx, message in enumerate(result.informational_messages, start=1):
            translated = _translate_tax_message_bg(message)
            if translated is not None:
                lines.append(f"- {translated}")
            else:
                lines.append(
                    f"- Налична е допълнителна обработваща бележка ({idx}); вижте \"Technical Details\" -> \"Processing Notes\"."
                )

    if result.warnings:
        if lines:
            lines.append("")
        lines.append("!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!")
        for idx, warning in enumerate(result.warnings, start=1):
            translated = _translate_tax_message_bg(warning)
            if translated is not None:
                lines.append(f"- {translated}")
            else:
                lines.append(
                    f"- Има причина за ръчна проверка ({idx}); вижте \"Technical Details\" -> \"Processing Notes\"."
                )

    technical_lines: list[str] = []
    untranslated_notes: list[str] = []
    for item in [*result.warnings, *result.informational_messages]:
        if _translate_tax_message_bg(item) is None:
            untranslated_notes.append(item)
    if untranslated_notes:
        technical_lines.append("Processing Notes")
        for note in untranslated_notes:
            technical_lines.append(f"- [UNTRANSLATED] {note}")
        technical_lines.append("")

    technical_lines.append("Audit Data")
    technical_lines.append(f"- platform: {result.platform}")
    technical_lines.append(
        f"- tax_year: {result.tax_year if result.tax_year is not None else '-'}"
    )
    technical_lines.append(f"- part1_rows: {len(result.part1_rows)}")
    technical_lines.append(f"- warnings_count: {len(result.warnings)}")
    technical_lines.append(f"- informational_messages_count: {len(result.informational_messages)}")
    technical_lines.extend(f"- {line}" for line in display_currency_technical_lines(money_context))

    if technical_lines:
        if lines:
            lines.append("")
        lines.append(TECHNICAL_DETAILS_SEPARATOR)
        lines.append("")
        lines.extend(technical_lines)

    return "\n".join(lines).rstrip() + "\n"


def write_appendix6_text(
    path: Path,
    *,
    result: P2PAppendix6Result,
    tax_year: int,
    display_currency: str = "EUR",
    cache_dir: str | Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_appendix6_text(
            result=result,
            tax_year=tax_year,
            display_currency=display_currency,
            cache_dir=cache_dir,
        ),
        encoding="utf-8",
    )


__all__ = [name for name in globals() if not name.startswith("__")]
