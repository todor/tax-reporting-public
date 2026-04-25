from __future__ import annotations

from dataclasses import dataclass

from .common import Money, format_money, is_zero_money


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


def render_appendix5_table2(entries: list[Appendix5Table2Entry]) -> list[str]:
    reportable = [entry for entry in entries if _is_entry_reportable(entry)]
    if not reportable:
        return []

    lines = ["Приложение 5", "Таблица 2"]
    for idx, entry in enumerate(reportable):
        if idx > 0:
            lines.append("")
        code = entry.code or "-"
        lines.append(f"- Продажна цена (EUR) - код {code}: {format_money(entry.sale_value)}")
        lines.append(f"  Цена на придобиване (EUR) - код {code}: {format_money(entry.acquisition_value)}")
        lines.append(f"  Печалба (EUR) - код {code}: {format_money(entry.profit)}")
        lines.append(f"  Загуба (EUR) - код {code}: {format_money(entry.loss)}")
        if _should_render_informative(entry):
            lines.append("")
            lines.append("  Информативни")
            lines.append(f"  - Нетен резултат (EUR): {format_money(entry.net_result)}")
            lines.append(f"  - Брой сделки: {entry.trade_count}")
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]

