from __future__ import annotations

import logging
import time
from datetime import date, datetime, time as time_value, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

from services.bnb_fx import get_exchange_rate as get_fiat_rate

from .cache import load_symbol_year_cache, save_symbol_year_cache
from .exchanges import BINANCE_BASE_URL, BINANCE_FUTURES_BASE_URL, resolve_target_symbol
from .models import (
    AssetNotFoundOnBinanceError,
    CryptoFxError,
    CryptoFxRate,
    PricingUnavailableError,
    ResolvedAsset,
    SymbolYearCache,
)

logger = logging.getLogger(__name__)

BINANCE_INTERVAL = "1h"
BINANCE_KLINES_LIMIT = 1000
FIAT_SYMBOLS = {"EUR", "USD", "USDT", "USDC"}
YEAR_LOOKBACK = 8

SPOT_MARKET = "spot"
FUTURES_MARKET = "futures"

PRICING_SOURCE_FIAT = "fiat_shortcut"
PRICING_SOURCE_BINANCE_SPOT = "binance_spot_close"
PRICING_SOURCE_BINANCE_FUTURES_MARK = "binance_futures_mark_price"


def parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        dt = datetime.fromisoformat(text)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_to_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
    retries: int = 2,
) -> tuple[int, Any]:
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
            time.sleep(0.3 * (attempt + 1))

    raise CryptoFxError(f"failed to query endpoint {url}") from last_error


def _usd_to_eur_rate(on_date: date) -> tuple[Decimal, date]:
    fx = get_fiat_rate("USD", on_date)
    return fx.rate, fx.date


def _fetch_binance_spot_year(symbol: str, year: int, *, session: requests.Session) -> dict[str, str]:
    """Fetch Binance spot hourly close prices from /api/v3/klines."""
    pair = f"{symbol.upper()}USDT"

    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    hourly: dict[str, str] = {}
    cursor = start_ms

    while cursor < end_ms:
        status, payload = _request_json(
            session,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            params={
                "symbol": pair,
                "interval": BINANCE_INTERVAL,
                "startTime": str(cursor),
                "endTime": str(end_ms),
                "limit": str(BINANCE_KLINES_LIMIT),
            },
        )

        if status >= 400:
            if isinstance(payload, dict) and payload.get("code") == -1121:
                raise AssetNotFoundOnBinanceError(f"asset symbol {symbol} not found on Binance spot")
            raise CryptoFxError(f"binance spot klines request failed for {pair}: {payload}")

        if not isinstance(payload, list) or not payload:
            break

        for candle in payload:
            open_time_ms = int(candle[0])
            close_price = str(candle[4])
            ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            hourly[ts.isoformat()] = close_price

        last_open_ms = int(payload[-1][0])
        next_cursor = last_open_ms + 3_600_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    if not hourly:
        raise AssetNotFoundOnBinanceError(f"asset symbol {symbol} has no Binance spot hourly data")

    return hourly


def _fetch_binance_futures_mark_year(
    symbol: str,
    year: int,
    *,
    session: requests.Session,
) -> dict[str, str]:
    """Fetch Binance futures mark-price hourly candles.

    Endpoint:
    - GET /fapi/v1/premiumIndexKlines
      https://fapi.binance.com/fapi/v1/premiumIndexKlines
    """
    pair = f"{symbol.upper()}USDT"

    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    hourly: dict[str, str] = {}
    cursor = start_ms

    while cursor < end_ms:
        status, payload = _request_json(
            session,
            f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/premiumIndexKlines",
            params={
                "symbol": pair,
                "interval": BINANCE_INTERVAL,
                "startTime": str(cursor),
                "endTime": str(end_ms),
                "limit": str(BINANCE_KLINES_LIMIT),
            },
        )

        if status >= 400:
            if isinstance(payload, dict) and payload.get("code") == -1121:
                raise AssetNotFoundOnBinanceError(f"asset symbol {symbol} not found on Binance futures")
            raise CryptoFxError(f"binance futures mark klines request failed for {pair}: {payload}")

        if not isinstance(payload, list) or not payload:
            break

        for candle in payload:
            open_time_ms = int(candle[0])
            # Binance mark-price klines use the same kline layout; index 4 is close mark price.
            mark_close = str(candle[4])
            ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            hourly[ts.isoformat()] = mark_close

        last_open_ms = int(payload[-1][0])
        next_cursor = last_open_ms + 3_600_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    if not hourly:
        raise AssetNotFoundOnBinanceError(f"asset symbol {symbol} has no Binance futures mark-price data")

    return hourly


def _fetch_binance_year(
    symbol: str,
    year: int,
    *,
    market: str,
    session: requests.Session,
) -> dict[str, str]:
    if market == SPOT_MARKET:
        return _fetch_binance_spot_year(symbol, year, session=session)
    if market == FUTURES_MARKET:
        return _fetch_binance_futures_mark_year(symbol, year, session=session)
    raise CryptoFxError(f"unsupported market {market!r}")


def _load_or_fetch_symbol_year(
    *,
    market: str,
    exchange: str,
    symbol: str,
    year: int,
    cache_dir: str | Path | None,
    session: requests.Session,
) -> SymbolYearCache:
    cached = load_symbol_year_cache(
        market=market,
        exchange=exchange,
        symbol=symbol,
        year=year,
        cache_dir=cache_dir,
    )
    if cached is not None:
        logger.debug("Crypto FX cache hit: market=%s symbol=%s year=%s", market, symbol, year)
        return cached

    logger.info("Crypto FX cache miss: market=%s symbol=%s year=%s; fetching", market, symbol, year)
    hourly = _fetch_binance_year(symbol, year, market=market, session=session)
    data = SymbolYearCache(
        market=market,
        exchange=exchange,
        symbol=symbol,
        year=year,
        hourly_close_usd=hourly,
    )
    save_symbol_year_cache(data, cache_dir=cache_dir)
    return data


def _find_price_on_or_before(
    *,
    market: str,
    symbol: str,
    target_hour: datetime,
    cache_dir: str | Path | None,
    session: requests.Session,
) -> tuple[datetime, Decimal]:
    current_year = target_hour.year

    for offset in range(YEAR_LOOKBACK):
        year = current_year - offset
        year_cache = _load_or_fetch_symbol_year(
            market=market,
            exchange="binance",
            symbol=symbol,
            year=year,
            cache_dir=cache_dir,
            session=session,
        )

        cutoff = target_hour if offset == 0 else datetime(year, 12, 31, 23, tzinfo=timezone.utc)
        found = year_cache.latest_on_or_before(cutoff)
        if found is not None:
            return found

    raise CryptoFxError(
        f"no hourly Binance {market} data on or before {target_hour.isoformat()} for symbol {symbol}"
    )


def _get_non_fiat_price_on_or_before(
    *,
    symbol: str,
    target_hour: datetime,
    is_future: bool,
    cache_dir: str | Path | None,
    session: requests.Session,
) -> tuple[datetime, Decimal, str, bool]:
    if not is_future:
        effective_ts, price_usd = _find_price_on_or_before(
            market=SPOT_MARKET,
            symbol=symbol,
            target_hour=target_hour,
            cache_dir=cache_dir,
            session=session,
        )
        return effective_ts, price_usd, PRICING_SOURCE_BINANCE_SPOT, False

    try:
        effective_ts, price_usd = _find_price_on_or_before(
            market=SPOT_MARKET,
            symbol=symbol,
            target_hour=target_hour,
            cache_dir=cache_dir,
            session=session,
        )
        return effective_ts, price_usd, PRICING_SOURCE_BINANCE_SPOT, False
    except (AssetNotFoundOnBinanceError, CryptoFxError) as spot_exc:
        logger.info(
            "Spot pricing unavailable for %s at %s; trying Binance futures mark-price fallback",
            symbol,
            target_hour.isoformat(),
        )

    try:
        effective_ts, price_usd = _find_price_on_or_before(
            market=FUTURES_MARKET,
            symbol=symbol,
            target_hour=target_hour,
            cache_dir=cache_dir,
            session=session,
        )
        return effective_ts, price_usd, PRICING_SOURCE_BINANCE_FUTURES_MARK, True
    except (AssetNotFoundOnBinanceError, CryptoFxError) as fut_exc:
        raise PricingUnavailableError(
            f"asset symbol {symbol} is unavailable on Binance spot and futures mark-price for {target_hour.date()}"
        ) from fut_exc


def _build_fiat_rate(
    *,
    requested_input: str,
    exchange: str,
    is_future: bool,
    resolved: ResolvedAsset,
    requested_ts: datetime,
) -> CryptoFxRate:
    symbol = resolved.target_symbol
    if symbol == "EUR":
        return CryptoFxRate(
            requested_input=requested_input,
            exchange=exchange,
            is_future=is_future,
            resolved_symbol=symbol,
            is_pair=resolved.is_pair,
            timestamp_requested=requested_ts,
            timestamp_effective=floor_to_hour(requested_ts),
            price_usd=None,
            price_eur=Decimal("1"),
            source="Fiat shortcut",
            pricing_source=PRICING_SOURCE_FIAT,
            used_futures_fallback=False,
            conversion_path="EUR identity",
            raw_metadata=resolved.raw_metadata,
        )

    usd_eur, effective_date = _usd_to_eur_rate(requested_ts.date())
    effective_ts = datetime.combine(effective_date, time_value(0, 0), tzinfo=timezone.utc)
    return CryptoFxRate(
        requested_input=requested_input,
        exchange=exchange,
        is_future=is_future,
        resolved_symbol=symbol,
        is_pair=resolved.is_pair,
        timestamp_requested=requested_ts,
        timestamp_effective=effective_ts,
        price_usd=Decimal("1"),
        price_eur=usd_eur,
        source="BNB fiat FX",
        pricing_source=PRICING_SOURCE_FIAT,
        used_futures_fallback=False,
        conversion_path=f"{symbol}->USD->EUR via bnb_fx",
        raw_metadata=resolved.raw_metadata,
    )


def get_crypto_eur_rate(
    symbol_or_pair: str,
    timestamp: datetime | str,
    exchange: str,
    is_future: bool = False,
    cache_dir: str | Path | None = None,
    assume_single_symbol: bool = False,
) -> CryptoFxRate:
    """Return EUR conversion for crypto tax valuation at timestamp.

    Resolution flow:
    - pair input -> use quote asset
    - single symbol -> use symbol
    - Kraken symbols are mapped to Binance equivalents
    - fiat shortcuts use bnb_fx
    - non-fiat is priced from Binance `<SYMBOL>USDT` at hourly resolution
    - futures requests try spot first, then futures mark-price fallback
    """
    resolved_exchange = exchange.strip().lower()
    requested_input = symbol_or_pair.strip().upper()
    requested_ts = parse_timestamp(timestamp)
    floored_ts = floor_to_hour(requested_ts)

    session = requests.Session()
    if assume_single_symbol:
        resolved = ResolvedAsset(
            requested_input=requested_input,
            exchange=resolved_exchange,
            is_pair=False,
            base_asset=None,
            quote_asset=None,
            target_symbol=requested_input,
            raw_metadata={"resolution_mode": "assume_single_symbol"},
        )
    else:
        resolved = resolve_target_symbol(
            requested_input,
            resolved_exchange,
            is_future=is_future,
            session=session,
        )
    target_symbol = resolved.target_symbol.upper()

    if target_symbol in FIAT_SYMBOLS:
        fiat_symbol = "USD" if target_symbol in {"USD", "USDT", "USDC"} else "EUR"
        adjusted = ResolvedAsset(
            requested_input=resolved.requested_input,
            exchange=resolved.exchange,
            is_pair=resolved.is_pair,
            base_asset=resolved.base_asset,
            quote_asset=resolved.quote_asset,
            target_symbol=fiat_symbol,
            raw_metadata=resolved.raw_metadata,
        )
        return _build_fiat_rate(
            requested_input=requested_input,
            exchange=resolved_exchange,
            is_future=is_future,
            resolved=adjusted,
            requested_ts=requested_ts,
        )

    effective_ts, price_usd, pricing_source, used_futures_fallback = _get_non_fiat_price_on_or_before(
        symbol=target_symbol,
        target_hour=floored_ts,
        is_future=is_future,
        cache_dir=cache_dir,
        session=session,
    )

    usd_eur, usd_eur_effective_date = _usd_to_eur_rate(effective_ts.date())
    eur_value = price_usd * usd_eur

    raw_metadata = dict(resolved.raw_metadata or {})
    raw_metadata["usd_eur_effective_date"] = usd_eur_effective_date.isoformat()
    raw_metadata["binance_pair"] = f"{target_symbol}USDT"
    raw_metadata["pricing_source"] = pricing_source
    raw_metadata["is_future"] = is_future

    if pricing_source == PRICING_SOURCE_BINANCE_FUTURES_MARK:
        conversion_path = f"{target_symbol}USDT mark-price close * USD->EUR(bnb_fx)"
        source = "Binance Futures Mark+BNB"
    else:
        conversion_path = f"{target_symbol}USDT close * USD->EUR(bnb_fx)"
        source = "Binance+BNB"

    return CryptoFxRate(
        requested_input=requested_input,
        exchange=resolved_exchange,
        is_future=is_future,
        resolved_symbol=target_symbol,
        is_pair=resolved.is_pair,
        timestamp_requested=requested_ts,
        timestamp_effective=effective_ts,
        price_usd=price_usd,
        price_eur=eur_value,
        source=source,
        pricing_source=pricing_source,
        used_futures_fallback=used_futures_fallback,
        conversion_path=conversion_path,
        raw_metadata=raw_metadata,
    )
