from __future__ import annotations

from dataclasses import dataclass

from .common import Money, MoneyRenderContext, is_zero_money, render_money_line


@dataclass(frozen=True, slots=True)
class Appendix5Table2Entry:
    code: str
    sale_value: Money
    acquisition_value: Money
    profit: Money
    loss: Money
    net_result: Money
    trade_count: int


def _is_entry_reportable(entry: Appendix5Table2Entry) -> bool:
    return any(
        not is_zero_money(item)
        for item in (
            entry.sale_value,
            entry.acquisition_value,
            entry.profit,
            entry.loss,
            entry.net_result,
        )
    ) or entry.trade_count > 0


def _should_render_informative(entry: Appendix5Table2Entry) -> bool:
    return (not is_zero_money(entry.net_result)) or entry.trade_count > 0


def render_appendix5_table2(
    entries: list[Appendix5Table2Entry],
    *,
    money_context: MoneyRenderContext | None = None,
) -> list[str]:
    reportable = [entry for entry in entries if _is_entry_reportable(entry)]
    if not reportable:
        return []

    lines = ["Приложение 5", "Таблица 2"]
    for idx, entry in enumerate(reportable):
        if idx > 0:
            lines.append("")
        code = entry.code or "-"
        lines.append(f"- Код {code}")
        lines.append(render_money_line("  Продажна цена", entry.sale_value, context=money_context))
        lines.append(render_money_line("  Цена на придобиване", entry.acquisition_value, context=money_context))
        lines.append(render_money_line("  Печалба", entry.profit, context=money_context))
        lines.append(render_money_line("  Загуба", entry.loss, context=money_context))
        if _should_render_informative(entry):
            lines.append("")
            lines.append("  Информативни")
            lines.append(render_money_line("  - Нетен резултат", entry.net_result, context=money_context))
            lines.append(f"  - Брой сделки: {entry.trade_count}")
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
