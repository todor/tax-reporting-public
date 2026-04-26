"""BNB historical FX rates with aggressive quarter-based disk caching.

Source:
- Bulgarian National Bank (BNB) exchange-rate XML endpoint.

Strategy:
- Cache one normalized JSON file per calendar quarter.
- `get_exchange_rate()` auto-fetches and caches the whole containing quarter on cache miss.
- Base currency in returned quotes is always EUR (`rate` means EUR for 1 symbol unit).

Example:
    >>> from services.bnb_fx import build_cache, get_exchange_rate
    >>> _ = build_cache(["USD", "EUR"], "2024-01-01", "2024-12-31")
    >>> usd = get_exchange_rate("USD", "2024-10-15")
    >>> usd.base_currency, usd.rate
    ('EUR', Decimal(...))
"""

from .cache import default_cache_dir
from .client import (
    BnbFxClient,
    build_cache,
    build_cache_for_symbols_and_years,
    convert_amount,
    get_conversion_rate,
    get_exchange_rate,
    parse_bnb_xml,
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
    "BnbFxClient",
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
    "convert_amount",
    "default_cache_dir",
    "get_conversion_rate",
    "get_exchange_rate",
    "parse_bnb_xml",
]
