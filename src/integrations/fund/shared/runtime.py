from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from services.bnb_fx import BnbFxError, get_exchange_rate
from services.crypto_fx import CryptoFxError, get_crypto_eur_rate

FundEurUnitRateProvider = Callable[[str, str, datetime], Decimal]


class FundFxConversionError(Exception):
    """Raised when fund FX conversion provider cannot resolve EUR rate."""


def default_fund_eur_unit_rate_provider(
    *,
    cache_dir: str | Path | None,
    crypto_exchange: str = "binance",
) -> FundEurUnitRateProvider:
    def provider(currency: str, currency_type: str, timestamp: datetime) -> Decimal:
        normalized_currency = currency.strip().upper()
        if normalized_currency == "EUR":
            return Decimal("1")

        normalized_type = currency_type.strip().lower()
        if normalized_type == "fiat":
            try:
                fx = get_exchange_rate(normalized_currency, timestamp.date(), cache_dir=cache_dir)
                return fx.rate
            except BnbFxError as exc:
                raise FundFxConversionError(
                    "fiat FX conversion failed "
                    f"(currency={normalized_currency}, timestamp={timestamp.isoformat()})"
                ) from exc

        if normalized_type == "crypto":
            try:
                fx = get_crypto_eur_rate(
                    normalized_currency,
                    timestamp,
                    crypto_exchange,
                    cache_dir=cache_dir,
                    assume_single_symbol=True,
                )
                return fx.price_eur
            except CryptoFxError as exc:
                raise FundFxConversionError(
                    "crypto FX conversion failed "
                    f"(currency={normalized_currency}, timestamp={timestamp.isoformat()})"
                ) from exc

        raise FundFxConversionError(
            f"unsupported currency_type={currency_type!r} for currency={normalized_currency}"
        )

    return provider


__all__ = [name for name in globals() if not name.startswith("__")]
