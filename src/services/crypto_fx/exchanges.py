from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any

import requests

from .models import PairResolutionError, ResolvedAsset, UnsupportedExchangeError

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com"
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
KRAKEN_BASE_URL = "https://api.kraken.com"
KRAKEN_FUTURES_BASE_URL = "https://futures.kraken.com"
BINANCE_FALLBACK_QUOTES = ("USDT", "USDC", "USD", "EUR")
_RESOLUTION_CACHE_MAX_SIZE = 4096
_RESOLUTION_CACHE: dict[tuple[str, bool, str, int], ResolvedAsset] = {}
_RESOLUTION_CACHE_LOCK = Lock()

KRAKEN_SYMBOL_MAP = {
    "XBT": "BTC",
    "XXBT": "BTC",
    "XDG": "DOGE",
    "XXDG": "DOGE",
    "XETH": "ETH",
    "XXRP": "XRP",
    "XLTC": "LTC",
    "XETC": "ETC",
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZJPY": "JPY",
    "ZCAD": "CAD",
    "ZAUD": "AUD",
    "ZCHF": "CHF",
}


def clear_symbol_resolution_cache() -> None:
    with _RESOLUTION_CACHE_LOCK:
        _RESOLUTION_CACHE.clear()


def normalize_kraken_symbol(symbol: str) -> str:
    return KRAKEN_SYMBOL_MAP.get(symbol.upper(), symbol.upper())


def _normalize_token(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _pick_first_non_empty(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(mapping.get(key, "")).strip()
        if value:
            return value.upper()
    return ""


def _fallback_pair_from_suffix(symbol: str, *, exchange: str) -> ResolvedAsset | None:
    upper = symbol.upper()
    for quote in BINANCE_FALLBACK_QUOTES:
        if not upper.endswith(quote):
            continue
        base = upper[: -len(quote)]
        # Guard against classifying short ticker symbols as pairs (for example BUSD).
        if len(base) < 2:
            continue
        return ResolvedAsset(
            requested_input=upper,
            exchange=exchange,
            is_pair=True,
            base_asset=base,
            quote_asset=quote,
            target_symbol=quote,
            raw_metadata={"pair_lookup": "fallback_suffix", "quote_asset": quote, "base_asset": base},
        )
    return None


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
    retries: int = 2,
) -> tuple[int, dict[str, Any]]:
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout_seconds)
            status = response.status_code
            payload = response.json()
            return status, payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(0.2 * (attempt + 1))

    raise PairResolutionError(f"failed to query exchange endpoint {url}") from last_error


def _resolve_binance_spot_pair(
    symbol_or_pair: str,
    *,
    session: requests.Session,
) -> ResolvedAsset:
    symbol = symbol_or_pair.upper()
    # Binance spot pair validation endpoint:
    # GET /api/v3/exchangeInfo?symbol=BTCUSDT
    status, payload = _request_json(
        session,
        f"{BINANCE_BASE_URL}/api/v3/exchangeInfo",
        params={"symbol": symbol},
    )

    if status >= 400:
        is_unknown_symbol = payload.get("code") == -1121
        if not is_unknown_symbol:
            raise PairResolutionError(f"binance pair validation failed for {symbol}: {payload}")
        fallback_pair = _fallback_pair_from_suffix(symbol, exchange="binance")
        if fallback_pair is not None:
            logger.warning(
                "Binance metadata did not resolve %s; using suffix fallback base=%s quote=%s",
                symbol,
                fallback_pair.base_asset,
                fallback_pair.quote_asset,
            )
            return fallback_pair
        return ResolvedAsset(
            requested_input=symbol,
            exchange="binance",
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=symbol,
            raw_metadata={"pair_lookup": payload},
        )

    symbols = payload.get("symbols", [])
    if not symbols:
        return ResolvedAsset(
            requested_input=symbol,
            exchange="binance",
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=symbol,
            raw_metadata={"pair_lookup": payload},
        )

    info = symbols[0]
    base = str(info.get("baseAsset", "")).upper()
    quote = str(info.get("quoteAsset", "")).upper()
    if not base or not quote:
        raise PairResolutionError(f"binance pair metadata missing base/quote for {symbol}")

    return ResolvedAsset(
        requested_input=symbol,
        exchange="binance",
        is_pair=True,
        base_asset=base,
        quote_asset=quote,
        target_symbol=quote,
        raw_metadata=info,
    )


def _resolve_binance_futures_pair(
    symbol_or_pair: str,
    *,
    session: requests.Session,
) -> ResolvedAsset:
    symbol = symbol_or_pair.upper()

    # Binance USD-M futures metadata endpoint (unfiltered):
    # GET /fapi/v1/exchangeInfo
    status, payload = _request_json(
        session,
        f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/exchangeInfo",
    )
    if status >= 400:
        raise PairResolutionError(f"binance futures pair validation failed for {symbol}: {payload}")

    symbols = payload.get("symbols", [])
    wanted = _normalize_token(symbol)
    info = next(
        (
            item
            for item in symbols
            if isinstance(item, dict) and _normalize_token(str(item.get("symbol", ""))) == wanted
        ),
        None,
    )

    if info is None:
        return ResolvedAsset(
            requested_input=symbol,
            exchange="binance",
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=symbol,
            raw_metadata={"pair_lookup": "futures_exchange_info_not_found"},
        )

    base = str(info.get("baseAsset", "")).upper()
    quote = str(info.get("quoteAsset", "")).upper()
    if not base or not quote:
        raise PairResolutionError(f"binance futures pair metadata missing base/quote for {symbol}")

    return ResolvedAsset(
        requested_input=symbol,
        exchange="binance",
        is_pair=True,
        base_asset=base,
        quote_asset=quote,
        target_symbol=quote,
        raw_metadata=info,
    )


def _resolve_kraken_spot_pair(
    symbol_or_pair: str,
    *,
    session: requests.Session,
) -> ResolvedAsset:
    symbol = symbol_or_pair.upper()
    # Kraken spot pair metadata endpoint:
    # GET /0/public/AssetPairs?pair=XBTUSD
    _, payload = _request_json(
        session,
        f"{KRAKEN_BASE_URL}/0/public/AssetPairs",
        params={"pair": symbol},
    )

    errors = payload.get("error", [])
    if errors:
        message = " ".join(str(err) for err in errors)
        unknown = "Unknown asset pair" in message
        if not unknown:
            raise PairResolutionError(f"kraken pair validation failed for {symbol}: {message}")
        return ResolvedAsset(
            requested_input=symbol,
            exchange="kraken",
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=normalize_kraken_symbol(symbol),
            raw_metadata={"pair_lookup": payload},
        )

    result = payload.get("result", {})
    if not result:
        return ResolvedAsset(
            requested_input=symbol,
            exchange="kraken",
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=normalize_kraken_symbol(symbol),
            raw_metadata={"pair_lookup": payload},
        )

    _, info = next(iter(result.items()))
    base = str(info.get("base", "")).upper()
    quote = str(info.get("quote", "")).upper()

    if not base or not quote:
        raise PairResolutionError(f"kraken pair metadata missing base/quote for {symbol}")

    normalized_quote = normalize_kraken_symbol(quote)
    normalized_base = normalize_kraken_symbol(base)

    return ResolvedAsset(
        requested_input=symbol,
        exchange="kraken",
        is_pair=True,
        base_asset=normalized_base,
        quote_asset=normalized_quote,
        target_symbol=normalized_quote,
        raw_metadata=info,
    )


def _resolve_kraken_futures_pair(
    symbol_or_pair: str,
    *,
    session: requests.Session,
) -> ResolvedAsset:
    symbol = symbol_or_pair.upper()

    # Kraken futures instruments endpoint:
    # GET /derivatives/api/v3/instruments
    status, payload = _request_json(
        session,
        f"{KRAKEN_FUTURES_BASE_URL}/derivatives/api/v3/instruments",
    )
    if status >= 400:
        raise PairResolutionError(f"kraken futures pair validation failed for {symbol}: {payload}")

    instruments = payload.get("instruments", [])
    wanted = _normalize_token(symbol)

    def _matches(item: dict[str, Any]) -> bool:
        for key in ("symbol", "instrument", "altname", "name", "ticker", "contractSymbol"):
            token = _normalize_token(str(item.get(key, "")))
            if token and token == wanted:
                return True
        return False

    info = next((item for item in instruments if isinstance(item, dict) and _matches(item)), None)

    if info is None:
        return ResolvedAsset(
            requested_input=symbol,
            exchange="kraken",
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=symbol,
            raw_metadata={"pair_lookup": "kraken_futures_instrument_not_found"},
        )

    base = _pick_first_non_empty(
        info,
        (
            "baseAsset",
            "base",
            "underlying",
            "underlying_asset",
            "underlyingAsset",
            "baseCurrency",
        ),
    )
    quote = _pick_first_non_empty(
        info,
        (
            "quoteAsset",
            "quote",
            "quoteCurrency",
            "settlementCurrency",
            "settlement_currency",
            "currency",
        ),
    )

    if not quote:
        instrument_symbol = str(
            info.get("symbol", "") or info.get("instrument", "") or info.get("name", "")
        ).upper()
        guessed = _fallback_pair_from_suffix(instrument_symbol, exchange="kraken")
        if guessed is not None and guessed.quote_asset is not None:
            quote = guessed.quote_asset
            if not base and guessed.base_asset is not None:
                base = guessed.base_asset

    if not base or not quote:
        raise PairResolutionError(f"kraken futures pair metadata missing base/quote for {symbol}")

    normalized_quote = normalize_kraken_symbol(quote)
    normalized_base = normalize_kraken_symbol(base)

    return ResolvedAsset(
        requested_input=symbol,
        exchange="kraken",
        is_pair=True,
        base_asset=normalized_base,
        quote_asset=normalized_quote,
        target_symbol=normalized_quote,
        raw_metadata=info,
    )


def _normalize_kraken_resolved(resolved: ResolvedAsset) -> ResolvedAsset:
    if resolved.is_pair:
        normalized_base = normalize_kraken_symbol(resolved.base_asset or "") if resolved.base_asset else None
        normalized_quote = normalize_kraken_symbol(resolved.quote_asset or "") if resolved.quote_asset else None
        normalized_target = normalize_kraken_symbol(resolved.target_symbol)
        return ResolvedAsset(
            requested_input=resolved.requested_input,
            exchange=resolved.exchange,
            is_pair=True,
            base_asset=normalized_base,
            quote_asset=normalized_quote,
            target_symbol=normalized_target,
            raw_metadata=resolved.raw_metadata,
        )

    return ResolvedAsset(
        requested_input=resolved.requested_input,
        exchange=resolved.exchange,
        is_pair=False,
        base_asset=None,
        quote_asset=None,
        target_symbol=normalize_kraken_symbol(resolved.target_symbol),
        raw_metadata=resolved.raw_metadata,
    )


def resolve_target_symbol(
    symbol_or_pair: str,
    exchange: str,
    *,
    is_future: bool = False,
    session: requests.Session | None = None,
) -> ResolvedAsset:
    resolved_exchange = exchange.strip().lower()
    if resolved_exchange not in {"binance", "kraken"}:
        raise UnsupportedExchangeError(f"unsupported exchange: {exchange!r}")

    symbol = symbol_or_pair.strip().upper()
    if not symbol:
        raise PairResolutionError("symbol_or_pair must not be empty")

    # Cache key includes `_request_json` identity so monkeypatched test stubs naturally
    # invalidate old entries and avoid cross-test leakage.
    cache_key = (resolved_exchange, is_future, symbol, id(_request_json))
    with _RESOLUTION_CACHE_LOCK:
        cached = _RESOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if resolved_exchange == "binance":
        quick_pair = _fallback_pair_from_suffix(symbol, exchange=resolved_exchange)
        if quick_pair is not None:
            with _RESOLUTION_CACHE_LOCK:
                _RESOLUTION_CACHE[cache_key] = quick_pair
            return quick_pair

    client = session or requests.Session()

    if resolved_exchange == "binance":
        if is_future:
            resolved = _resolve_binance_futures_pair(symbol, session=client)
        else:
            resolved = _resolve_binance_spot_pair(symbol, session=client)
    else:
        if is_future:
            resolved = _resolve_kraken_futures_pair(symbol, session=client)
        else:
            resolved = _resolve_kraken_spot_pair(symbol, session=client)

    normalized = _normalize_kraken_resolved(resolved) if resolved_exchange == "kraken" else resolved
    with _RESOLUTION_CACHE_LOCK:
        if len(_RESOLUTION_CACHE) >= _RESOLUTION_CACHE_MAX_SIZE:
            _RESOLUTION_CACHE.clear()
        _RESOLUTION_CACHE[cache_key] = normalized
    return normalized
