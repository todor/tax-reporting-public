from __future__ import annotations

from dataclasses import dataclass, field

from .common import Money, is_zero_money, render_money_line


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
            lines.append(render_money_line("  Обща цена на придобиване в съответната валута", row.acquisition_native))
            lines.append(render_money_line("  В EUR", row.acquisition_eur))
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
            lines.append(render_money_line("  Брутен размер на дохода", row.gross_income))
            lines.append("  Документално доказана цена на придобиване: ")
            lines.append(render_money_line("  Платен данък в чужбина", row.foreign_tax))
            lines.append(render_money_line("  Допустим размер на данъчния кредит", row.allowable_credit))
            lines.append(render_money_line("  Размер на признатия данъчен кредит", row.recognized_credit))
            lines.append(render_money_line("  Дължим данък, подлежащ на внасяне", row.tax_due))
            lines.append("")

    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
