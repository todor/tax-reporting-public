from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .appendix6_models import P2PAppendix6Result

DECIMAL_TWO = Decimal("0.01")


def _fmt_decimal(value: Decimal, *, quant: Decimal = DECIMAL_TWO) -> str:
    return format(value.quantize(quant, rounding=ROUND_HALF_UP), "f")


def _fmt_informative_value(value: Decimal | str) -> str:
    if isinstance(value, Decimal):
        return _fmt_decimal(value)
    return str(value)


def build_appendix6_text(*, result: P2PAppendix6Result) -> str:
    lines: list[str] = []

    lines.append("Приложение 6")
    lines.append("Част I")
    for idx, row in enumerate(result.part1_rows, start=1):
        lines.append(f"- Ред 1.{idx}")
        lines.append(f"  ЕИК: {(row.payer_eik or '-')}")
        lines.append(f"  Наименование: {row.payer_name}")
        lines.append(f"  Код: {row.code}")
        lines.append(f"  Размер на дохода: {_fmt_decimal(row.amount)}")
    lines.append(f"- Обща сума на доходите с код 603: {_fmt_decimal(result.aggregate_code_603)}")
    lines.append(f"- Обща сума на доходите с код 606: {_fmt_decimal(result.aggregate_code_606)}")

    lines.append("")
    lines.append("Част II")
    lines.append(f"- Облагаем доход по чл. 35, код 603: {_fmt_decimal(result.taxable_code_603)}")
    lines.append(f"- Облагаем доход по чл. 35, код 606: {_fmt_decimal(result.taxable_code_606)}")

    lines.append("")
    lines.append("Част III")
    lines.append(
        "- Удържан и/или внесен окончателен данък за доходи: "
        f"{_fmt_decimal(result.withheld_tax)}"
    )

    if result.warnings:
        lines.append("")
        lines.append("!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!")
        for warning in result.warnings:
            lines.append(f"- {warning}")

    if result.informational_messages:
        lines.append("")
        lines.append("Бележки по обработката")
        for message in result.informational_messages:
            lines.append(f"- {message}")

    lines.append("")
    lines.append("Одитни данни")
    for info in result.informative_rows:
        lines.append(f"- {info.label}: {_fmt_informative_value(info.value)}")

    return "\n".join(lines).rstrip() + "\n"


def write_appendix6_text(path: Path, *, result: P2PAppendix6Result) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_appendix6_text(result=result), encoding="utf-8")


__all__ = [name for name in globals() if not name.startswith("__")]
