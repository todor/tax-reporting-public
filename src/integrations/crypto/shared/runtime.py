from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from services.bnb_fx import BnbFxError, get_exchange_rate
from services.crypto_fx import get_crypto_eur_rate

EurUnitRateProvider = Callable[[str, datetime], Decimal]


def default_eur_unit_rate_provider(
    *,
    cache_dir: str | Path | None,
    crypto_exchange: str = "binance",
) -> EurUnitRateProvider:
    def provider(currency: str, timestamp: datetime) -> Decimal:
        normalized = currency.strip().upper()
        if normalized == "EUR":
            return Decimal("1")

        try:
            fx = get_exchange_rate(normalized, timestamp.date(), cache_dir=cache_dir)
            return fx.rate
        except BnbFxError:
            pass

        fx_crypto = get_crypto_eur_rate(
            normalized,
            timestamp,
            crypto_exchange,
            cache_dir=cache_dir,
        )
        return fx_crypto.price_eur

    return provider


def build_enriched_ir_output_paths(
    *,
    input_path: Path,
    output_dir: Path,
    tax_year: int,
    stem_fallback: str,
) -> tuple[Path, Path, Path]:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", input_path.stem).strip("_").lower()
    stem = normalized or stem_fallback
    return (
        output_dir / f"{stem}_modified.csv",
        output_dir / f"{stem}_declaration.txt",
        output_dir / f"{stem}_state_end_{tax_year}.json",
    )


__all__ = [name for name in globals() if not name.startswith("__")]
