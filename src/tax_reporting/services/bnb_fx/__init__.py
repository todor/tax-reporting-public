"""BNB historical FX rates with aggressive quarter-based disk caching.

Source:
- Bulgarian National Bank (BNB) exchange-rate CSV endpoint.

Strategy:
- Cache one normalized JSON file per calendar quarter.
- `get_exchange_rate()` auto-fetches and caches the whole containing quarter on cache miss.
- Base currency is inferred from CSV headers and surfaced in each returned `FxRate`.
  - Through 2025-12-31 inclusive: BGN base.
  - From 2026-01-01 inclusive: EUR base.

Example:
    >>> from tax_reporting.services.bnb_fx import build_cache, get_exchange_rate
    >>> _ = build_cache(["USD", "EUR"], "2024-01-01", "2024-12-31")
    >>> usd = get_exchange_rate("USD", "2024-10-15")
    >>> usd.base_currency
    'BGN'
"""

from .cache import default_cache_dir
from .client import (
    BnbCsvClient,
    build_cache,
    build_cache_for_symbols_and_years,
    get_exchange_rate,
    parse_bnb_csv,
    parse_bnb_payload,
)
from .models import (
    BnbFxError,
    CacheBuildResult,
    CacheError,
    FetchError,
    FxRate,
    ParseError,
    QuarterCacheData,
    QuarterKey,
    RateNotFoundError,
)

__all__ = [
    "BnbCsvClient",
    "BnbFxError",
    "CacheBuildResult",
    "CacheError",
    "FetchError",
    "FxRate",
    "ParseError",
    "QuarterCacheData",
    "QuarterKey",
    "RateNotFoundError",
    "build_cache",
    "build_cache_for_symbols_and_years",
    "default_cache_dir",
    "get_exchange_rate",
    "parse_bnb_csv",
    "parse_bnb_payload",
]
