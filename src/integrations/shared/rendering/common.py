from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Literal

ZERO = Decimal("0")
DECIMAL_TWO = Decimal("0.01")
DisplayCurrency = Literal["EUR", "BGN"]


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class MoneyRenderContext:
    display_currency: DisplayCurrency = "EUR"
    calculations_currency: str = "EUR"
    convert_eur_to_display: Callable[[Decimal], Decimal] | None = None
    fx_source: str | None = None
    fx_date_iso: str | None = None
    fx_pair: str | None = None


def _format_amount(amount: Decimal, *, quant: Decimal = DECIMAL_TWO) -> str:
    return format(amount.quantize(quant, rounding=ROUND_HALF_UP), "f")


def _display_money(
    money: Money,
    *,
    context: MoneyRenderContext | None,
) -> Money:
    if context is None or context.display_currency == "EUR":
        return money
    if context.display_currency == "BGN" and money.currency == "EUR":
        if context.convert_eur_to_display is None:
            raise ValueError("BGN display requires EUR->BGN conversion function")
        return Money(context.convert_eur_to_display(money.amount), "BGN")
    return money


def format_money(
    money: Money,
    *,
    quant: Decimal = DECIMAL_TWO,
    context: MoneyRenderContext | None = None,
) -> str:
    display_money = _display_money(money, context=context)
    return f"{_format_amount(display_money.amount, quant=quant)} {display_money.currency}"


def format_optional_money(
    money: Money | None,
    *,
    quant: Decimal = DECIMAL_TWO,
    context: MoneyRenderContext | None = None,
) -> str:
    if money is None:
        return ""
    return format_money(money, quant=quant, context=context)


def render_money_line(
    label: str,
    money: Money,
    *,
    prefix: str = "",
    quant: Decimal = DECIMAL_TWO,
    context: MoneyRenderContext | None = None,
) -> str:
    return f"{prefix}{label}: {format_money(money, quant=quant, context=context)}"


def render_optional_money_line(
    label: str,
    money: Money | None,
    *,
    prefix: str = "",
    quant: Decimal = DECIMAL_TWO,
    context: MoneyRenderContext | None = None,
) -> str:
    if money is None:
        return f"{prefix}{label}: "
    return render_money_line(label, money, prefix=prefix, quant=quant, context=context)


def is_zero_money(money: Money) -> bool:
    return money.amount == ZERO


__all__ = [name for name in globals() if not name.startswith("__")]
