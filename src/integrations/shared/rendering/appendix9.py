from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .common import Money, MoneyRenderContext, is_zero_money, render_money_line


@dataclass(frozen=True, slots=True)
class Appendix9Part2Row:
    country: str
    code: str
    gross_income: Money
    tax_base: Money
    foreign_tax: Money
    allowable_credit: Money
    recognized_credit: Money
    document_ref: str


ZERO_EUR = Money(Decimal("0"), "EUR")


def _has_row_data(row: Appendix9Part2Row) -> bool:
    return any(
        not is_zero_money(item)
        for item in (
            row.gross_income,
            row.tax_base,
            row.foreign_tax,
            row.allowable_credit,
            row.recognized_credit,
        )
    )


def render_appendix9_part2(
    rows: list[Appendix9Part2Row],
    *,
    money_context: MoneyRenderContext | None = None,
) -> list[str]:
    reportable = [row for row in rows if _has_row_data(row)]
    if not reportable:
        return []

    lines = ["Приложение 9", "Част II"]
    for row in reportable:
        lines.append(f"- Държава: {row.country}")
        lines.append(f"  Код вид доход: {row.code}")
        lines.append(
            render_money_line(
                "  Брутен размер на дохода (включително платеният данък)",
                row.gross_income,
                context=money_context,
            )
        )
        lines.append(render_money_line("  Нормативно определени разходи", ZERO_EUR, context=money_context))
        lines.append(render_money_line("  Задължителни осигурителни вноски", ZERO_EUR, context=money_context))
        lines.append(render_money_line("  Годишна данъчна основа", row.tax_base, context=money_context))
        lines.append(render_money_line("  Платен данък в чужбина", row.foreign_tax, context=money_context))
        lines.append(
            render_money_line(
                "  Допустим размер на данъчния кредит",
                row.allowable_credit,
                context=money_context,
            )
        )
        lines.append(
            render_money_line(
                "  Размер на признатия данъчен кредит",
                row.recognized_credit,
                context=money_context,
            )
        )
        lines.append(
            "  № и дата на документа за дохода и съответния данък: "
            f"{row.document_ref or '-'}"
        )
        lines.append("")
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
