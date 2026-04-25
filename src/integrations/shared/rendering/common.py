from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
DECIMAL_TWO = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str


def format_money(money: Money, *, quant: Decimal = DECIMAL_TWO) -> str:
    return format(money.amount.quantize(quant, rounding=ROUND_HALF_UP), "f")


def is_zero_money(money: Money) -> bool:
    return money.amount == ZERO


__all__ = [name for name in globals() if not name.startswith("__")]

