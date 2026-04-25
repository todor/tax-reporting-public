from __future__ import annotations

from dataclasses import dataclass

from .common import Money, format_money, is_zero_money


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


def render_appendix9_part2(rows: list[Appendix9Part2Row]) -> list[str]:
    reportable = [row for row in rows if _has_row_data(row)]
    if not reportable:
        return []

    lines = ["Приложение 9", "Част II"]
    for row in reportable:
        lines.append(f"- Държава: {row.country}")
        lines.append(f"  Код вид доход: {row.code}")
        lines.append(
            "  Брутен размер на дохода (включително платеният данък): "
            f"{format_money(row.gross_income)}"
        )
        lines.append("  Нормативно определени разходи: 0")
        lines.append("  Задължителни осигурителни вноски: 0")
        lines.append(f"  Годишна данъчна основа: {format_money(row.tax_base)}")
        lines.append(f"  Платен данък в чужбина: {format_money(row.foreign_tax)}")
        lines.append(f"  Допустим размер на данъчния кредит: {format_money(row.allowable_credit)}")
        lines.append(f"  Размер на признатия данъчен кредит: {format_money(row.recognized_credit)}")
        lines.append(
            "  № и дата на документа за дохода и съответния данък: "
            f"{row.document_ref or '-'}"
        )
        lines.append("")
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]

