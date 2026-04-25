from __future__ import annotations

from dataclasses import dataclass

from .common import Money, is_zero_money, render_money_line


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
        lines.append(f"- Код {code}")
        lines.append(render_money_line("  Брутен размер на дохода", entry.gross_income))
        lines.append(render_money_line("  Цена на придобиване", entry.acquisition_value))
        if _should_render_informative(entry):
            lines.append("")
            lines.append("  Информативни")
            lines.append(render_money_line("  - печалба", entry.profit))
            lines.append(render_money_line("  - загуба", entry.loss))
            lines.append(render_money_line("  - нетен резултат", entry.net_result))
            lines.append(f"  - брой сделки: {entry.trade_count}")
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
