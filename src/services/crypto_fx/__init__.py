"""Crypto-to-EUR conversion helper for tax reporting.

Behavior summary:
- Pair input uses QUOTE asset from the provided exchange metadata.
- Single-symbol input uses the symbol itself.
- Kraken symbols are normalized to Binance-style symbols.
- Fiat/stable shortcuts: EUR, USD, USDT, USDC via bnb_fx.
- Non-fiat pricing uses Binance hourly klines (`<SYMBOL>USDT`) and USD->EUR conversion.
- Futures mode (`is_future=True`) uses futures metadata and falls back to Binance
  futures mark-price candles if spot pricing is unavailable.
"""

from .cache import default_cache_dir
from .client import get_crypto_eur_rate
from .exchanges import KRAKEN_SYMBOL_MAP, clear_symbol_resolution_cache, normalize_kraken_symbol, resolve_target_symbol
from .models import (
    AssetNotFoundOnBinanceError,
    CacheError,
    CryptoFxError,
    CryptoFxRate,
    PairResolutionError,
    PricingUnavailableError,
    ResolvedAsset,
    SymbolYearCache,
    UnsupportedExchangeError,
)

__all__ = [
    "AssetNotFoundOnBinanceError",
    "CacheError",
    "CryptoFxError",
    "CryptoFxRate",
    "KRAKEN_SYMBOL_MAP",
    "PairResolutionError",
    "PricingUnavailableError",
    "ResolvedAsset",
    "SymbolYearCache",
    "UnsupportedExchangeError",
    "clear_symbol_resolution_cache",
    "default_cache_dir",
    "get_crypto_eur_rate",
    "normalize_kraken_symbol",
    "resolve_target_symbol",
]
