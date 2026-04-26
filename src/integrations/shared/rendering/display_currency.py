from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable

from services.bnb_fx import convert_amount

from .common import DisplayCurrency, MoneyRenderContext

DISPLAY_CURRENCIES: tuple[DisplayCurrency, ...] = ("EUR", "BGN")


class DisplayCurrencyError(ValueError):
    """Raised when display-currency configuration or conversion fails."""


EurToBgnRateProvider = Callable[[date], Decimal]


@dataclass(frozen=True, slots=True)
class DisplayCurrencyConfig:
    display_currency: DisplayCurrency
    calculations_currency: str
    fx_source: str | None = None
    fx_date_iso: str | None = None
    fx_pair: str | None = None


def normalize_display_currency(value: str) -> DisplayCurrency:
    normalized = value.strip().upper()
    if normalized not in DISPLAY_CURRENCIES:
        allowed = ", ".join(DISPLAY_CURRENCIES)
        raise DisplayCurrencyError(f"invalid --display-currency {value!r}; expected one of: {allowed}")
    return normalized  # type: ignore[return-value]


def _default_eur_to_bgn_rate_provider(
    *,
    cache_dir: str | Path | None,
) -> EurToBgnRateProvider:
    def provider(on_date: date) -> Decimal:
        bgn_for_one_eur = convert_amount(
            Decimal("1"),
            "EUR",
            "BGN",
            on_date,
            cache_dir=cache_dir,
        )
        if bgn_for_one_eur <= Decimal("0"):
            raise DisplayCurrencyError(
                f"invalid BNB EUR/BGN quote for {on_date.isoformat()}: {bgn_for_one_eur}"
            )
        return bgn_for_one_eur

    return provider


def build_money_render_context(
    *,
    tax_year: int,
    display_currency: str,
    cache_dir: str | Path | None = None,
    eur_to_bgn_rate_provider: EurToBgnRateProvider | None = None,
) -> MoneyRenderContext:
    normalized = normalize_display_currency(display_currency)
    if normalized == "EUR":
        return MoneyRenderContext(display_currency="EUR")

    fx_date = date(tax_year, 12, 31)
    provider = eur_to_bgn_rate_provider or _default_eur_to_bgn_rate_provider(cache_dir=cache_dir)
    try:
        bgn_per_eur = provider(fx_date)
    except Exception as exc:  # noqa: BLE001
        raise DisplayCurrencyError(
            "failed to build BGN display conversion from BNB "
            f"for FX date {fx_date.isoformat()}: {exc}"
        ) from exc
    if bgn_per_eur <= Decimal("0"):
        raise DisplayCurrencyError(
            f"invalid EUR->BGN display rate for {fx_date.isoformat()}: {bgn_per_eur}"
        )

    return MoneyRenderContext(
        display_currency="BGN",
        calculations_currency="EUR",
        convert_eur_to_display=lambda amount: amount * bgn_per_eur,
        fx_source="BNB",
        fx_date_iso=fx_date.isoformat(),
        fx_pair="EUR/BGN",
    )


def display_currency_technical_lines(context: MoneyRenderContext) -> list[str]:
    if context.display_currency != "BGN":
        return []
    return [
        "Display currency: BGN",
        "Calculations currency: EUR",
        f"Display FX source: {context.fx_source or 'BNB'}",
        f"Display FX date: {context.fx_date_iso or '-'}",
        f"Display FX pair: {context.fx_pair or 'EUR/BGN'}",
    ]


__all__ = [name for name in globals() if not name.startswith("__")]
