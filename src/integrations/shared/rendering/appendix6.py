from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .common import Money, is_zero_money, render_money_line


@dataclass(frozen=True, slots=True)
class Appendix6Part1CompanyRow:
    payer_name: str
    payer_eik: str
    code: str
    amount: Money


@dataclass(frozen=True, slots=True)
class Appendix6Part1CodeTotal:
    code: str
    amount: Money


@dataclass(frozen=True, slots=True)
class Appendix6Part2TaxableTotal:
    code: str
    amount: Money


@dataclass(slots=True)
class Appendix6RenderData:
    part1_company_rows: list[Appendix6Part1CompanyRow] = field(default_factory=list)
    part1_code_totals: list[Appendix6Part1CodeTotal] = field(default_factory=list)
    part2_taxable_totals: list[Appendix6Part2TaxableTotal] = field(default_factory=list)
    part3_withheld_tax: Money = field(default_factory=lambda: Money(amount=Decimal("0"), currency="EUR"))


def _part1_has_data(data: Appendix6RenderData) -> bool:
    return bool(data.part1_company_rows) or any(
        not is_zero_money(item.amount) for item in data.part1_code_totals
    )


def _part2_has_data(data: Appendix6RenderData) -> bool:
    return any(not is_zero_money(item.amount) for item in data.part2_taxable_totals)


def _part3_has_data(data: Appendix6RenderData) -> bool:
    return not is_zero_money(data.part3_withheld_tax)


def render_appendix6(data: Appendix6RenderData) -> list[str]:
    has_part1 = _part1_has_data(data)
    has_part2 = _part2_has_data(data)
    has_part3 = _part3_has_data(data)
    if not has_part1 and not has_part2 and not has_part3:
        return []

    lines = ["Приложение 6"]

    if has_part1:
        lines.append("Част I")
        for idx, row in enumerate(data.part1_company_rows, start=1):
            lines.append(f"- Ред 1.{idx}")
            lines.append(f"  ЕИК: {row.payer_eik or '-'}")
            lines.append(f"  Наименование: {row.payer_name}")
            lines.append(f"  Код: {row.code}")
            lines.append(render_money_line("  Размер на дохода", row.amount))
        for code_total in data.part1_code_totals:
            if is_zero_money(code_total.amount):
                continue
            lines.append(
                render_money_line(f"- Обща сума на доходите с код {code_total.code}", code_total.amount)
            )

    if has_part2:
        if lines:
            lines.append("")
        lines.append("Част II")
        for taxable in data.part2_taxable_totals:
            if is_zero_money(taxable.amount):
                continue
            lines.append(render_money_line(f"- Облагаем доход по чл. 35, код {taxable.code}", taxable.amount))

    if has_part3:
        if lines:
            lines.append("")
        lines.append("Част III")
        lines.append(render_money_line("- Удържан и/или внесен окончателен данък за доходи", data.part3_withheld_tax))

    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
