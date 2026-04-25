from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
DECIMAL_TWO = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str


def _format_amount(amount: Decimal, *, quant: Decimal = DECIMAL_TWO) -> str:
    return format(amount.quantize(quant, rounding=ROUND_HALF_UP), "f")


def format_money(money: Money, *, quant: Decimal = DECIMAL_TWO) -> str:
    return f"{_format_amount(money.amount, quant=quant)} {money.currency}"


def format_optional_money(money: Money | None, *, quant: Decimal = DECIMAL_TWO) -> str:
    if money is None:
        return ""
    return format_money(money, quant=quant)


def render_money_line(
    label: str,
    money: Money,
    *,
    prefix: str = "",
    quant: Decimal = DECIMAL_TWO,
) -> str:
    return f"{prefix}{label}: {format_money(money, quant=quant)}"


def render_optional_money_line(
    label: str,
    money: Money | None,
    *,
    prefix: str = "",
    quant: Decimal = DECIMAL_TWO,
) -> str:
    if money is None:
        return f"{prefix}{label}: "
    return render_money_line(label, money, prefix=prefix, quant=quant)


def is_zero_money(money: Money) -> bool:
    return money.amount == ZERO


__all__ = [name for name in globals() if not name.startswith("__")]
