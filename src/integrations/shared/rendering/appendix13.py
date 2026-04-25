from __future__ import annotations

from dataclasses import dataclass

from .common import Money, format_money, is_zero_money


@dataclass(frozen=True, slots=True)
class Appendix13Part2Entry:
    code: str
    gross_income: Money
    acquisition_value: Money
    profit: Money
    loss: Money
    net_result: Money
    trade_count: int


def _is_entry_reportable(entry: Appendix13Part2Entry) -> bool:
    return any(
        not is_zero_money(item)
        for item in (
            entry.gross_income,
            entry.acquisition_value,
            entry.profit,
            entry.loss,
            entry.net_result,
        )
    ) or entry.trade_count > 0


def _should_render_informative(entry: Appendix13Part2Entry) -> bool:
    return (not is_zero_money(entry.net_result)) or entry.trade_count > 0


def render_appendix13_part2(entries: list[Appendix13Part2Entry]) -> list[str]:
    reportable = [entry for entry in entries if _is_entry_reportable(entry)]
    if not reportable:
        return []

    lines = ["Приложение 13", "Част ІІ"]
    for idx, entry in enumerate(reportable):
        if idx > 0:
            lines.append("")
        code = entry.code or "-"
        lines.append(f"- Брутен размер на дохода (EUR) - код {code}: {format_money(entry.gross_income)}")
        lines.append(f"  Цена на придобиване (EUR) - код {code}: {format_money(entry.acquisition_value)}")
        if _should_render_informative(entry):
            lines.append("")
            lines.append("  Информативни")
            lines.append(f"  - печалба (EUR): {format_money(entry.profit)}")
            lines.append(f"  - загуба (EUR): {format_money(entry.loss)}")
            lines.append(f"  - нетен резултат (EUR): {format_money(entry.net_result)}")
            lines.append(f"  - брой сделки: {entry.trade_count}")
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]

