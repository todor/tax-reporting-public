from __future__ import annotations

from dataclasses import dataclass, field

from .common import Money, format_money, is_zero_money


@dataclass(frozen=True, slots=True)
class Appendix8Part1Row:
    asset_type: str
    country: str
    quantity: str
    acquisition_date: str | None
    acquisition_native: Money
    acquisition_eur: Money
    native_currency_label: str


@dataclass(frozen=True, slots=True)
class Appendix8Part3Row:
    payer: str
    country: str
    code: str
    treaty_method: str
    gross_income: Money
    foreign_tax: Money
    allowable_credit: Money
    recognized_credit: Money
    tax_due: Money


@dataclass(slots=True)
class Appendix8RenderData:
    part1_rows: list[Appendix8Part1Row] = field(default_factory=list)
    part3_rows: list[Appendix8Part3Row] = field(default_factory=list)
    part1_notes: list[str] = field(default_factory=list)


def _part1_has_data(data: Appendix8RenderData) -> bool:
    return len(data.part1_rows) > 0


def _part3_has_data(data: Appendix8RenderData) -> bool:
    return any(
        any(
            not is_zero_money(item)
            for item in (
                row.gross_income,
                row.foreign_tax,
                row.allowable_credit,
                row.recognized_credit,
                row.tax_due,
            )
        )
        for row in data.part3_rows
    )


def render_appendix8(data: Appendix8RenderData) -> list[str]:
    has_part1 = _part1_has_data(data)
    has_part3 = _part3_has_data(data)
    if not has_part1 and not has_part3:
        return []

    lines = ["Приложение 8"]

    if has_part1:
        lines.append("Част І, Акции")
        for idx, row in enumerate(data.part1_rows, start=1):
            lines.append(f"- ред 1.{idx}")
            lines.append(f"  Вид: {row.asset_type}")
            lines.append(f"  Държава: {row.country}")
            lines.append(f"  Брой: {row.quantity}")
            if row.acquisition_date is not None:
                lines.append(f"  Дата и година на придобиване: {row.acquisition_date}")
            lines.append(
                f"  Обща цена на придобиване в съответната валута "
                f"({row.native_currency_label or '-'}): "
                f"{format_money(row.acquisition_native)}"
            )
            lines.append(f"  В EUR: {format_money(row.acquisition_eur)}")
            lines.append("")
        for note in data.part1_notes:
            lines.append(note)
        if data.part1_notes:
            lines.append("")

    if has_part3:
        lines.append("Част III,")
        for idx, row in enumerate(data.part3_rows, start=1):
            if all(
                is_zero_money(item)
                for item in (
                    row.gross_income,
                    row.foreign_tax,
                    row.allowable_credit,
                    row.recognized_credit,
                    row.tax_due,
                )
            ):
                continue
            lines.append(f"- Ред 1.{idx}")
            lines.append(f"  Наименование на лицето, изплатило дохода: {row.payer}")
            lines.append(f"  Държава: {row.country}")
            lines.append(f"  Код вид доход: {row.code}")
            lines.append(
                "  Код за прилагане на метод за избягване на двойното данъчно облагане: "
                f"{row.treaty_method}"
            )
            lines.append(f"  Брутен размер на дохода: {format_money(row.gross_income)}")
            lines.append("  Документално доказана цена на придобиване: ")
            lines.append(f"  Платен данък в чужбина: {format_money(row.foreign_tax)}")
            lines.append(f"  Допустим размер на данъчния кредит: {format_money(row.allowable_credit)}")
            lines.append(f"  Размер на признатия данъчен кредит: {format_money(row.recognized_credit)}")
            lines.append(f"  Дължим данък, подлежащ на внасяне: {format_money(row.tax_due)}")
            lines.append("")

    return lines


__all__ = [name for name in globals() if not name.startswith("__")]

