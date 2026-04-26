from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Lock

import requests

from .cache import default_cache_dir, load_quarter_cache, quarter_is_cached, save_quarter_cache
from .models import (
    CacheBuildResult,
    FetchError,
    FxRate,
    ParseError,
    QuarterCacheData,
    QuarterKey,
    RateNotFoundError,
)
from .utils import (
    detect_base_currency,
    is_bgn_period,
    normalize_symbol,
    parse_date,
    parse_decimal,
    quarter_for_date,
    quarter_keys_for_period,
    quarter_keys_for_years,
)

logger = logging.getLogger(__name__)

BNB_FX_ENDPOINT = (
    "https://bnb.bg/Statistics/StExternalSector/StExchangeRates/"
    "StERForeignCurrencies/index.htm"
)
EUR_FIXED_RATE_BGN = Decimal("1.95583")
EUR_SYMBOL = "EUR"
BGN_SYMBOL = "BGN"
LOOKBACK_QUARTERS = 12
_FALLBACK_LOGGED: set[tuple[str, date, date]] = set()
_FALLBACK_LOGGED_LOCK = Lock()
_MEMORY_QUARTER_CACHE_MAX_SIZE = 128
_MEMORY_RATE_CACHE_MAX_SIZE = 8192
_MEMORY_QUARTER_CACHE: dict[tuple[str, QuarterKey], QuarterCacheData] = {}
_MEMORY_QUARTER_CACHE_LOCK = Lock()
_MEMORY_RATE_CACHE: dict[tuple[str, date, str], FxRate] = {}
_MEMORY_RATE_CACHE_LOCK = Lock()
_CACHE_DIR_KEY_CACHE: dict[str, str] = {}
_CACHE_DIR_KEY_CACHE_LOCK = Lock()


def _cache_dir_key(cache_dir: str | Path | None) -> str:
    raw_key = "__DEFAULT__" if cache_dir is None else str(cache_dir)
    with _CACHE_DIR_KEY_CACHE_LOCK:
        cached = _CACHE_DIR_KEY_CACHE.get(raw_key)
    if cached is not None:
        return cached

    path = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
    resolved = str(path.resolve())
    with _CACHE_DIR_KEY_CACHE_LOCK:
        _CACHE_DIR_KEY_CACHE[raw_key] = resolved
    return resolved


def _put_quarter_cache_in_memory(key: tuple[str, QuarterKey], data: QuarterCacheData) -> None:
    with _MEMORY_QUARTER_CACHE_LOCK:
        if len(_MEMORY_QUARTER_CACHE) >= _MEMORY_QUARTER_CACHE_MAX_SIZE:
            _MEMORY_QUARTER_CACHE.clear()
        _MEMORY_QUARTER_CACHE[key] = data


def _put_rate_cache_in_memory(key: tuple[str, date, str], rate: FxRate) -> None:
    with _MEMORY_RATE_CACHE_LOCK:
        if len(_MEMORY_RATE_CACHE) >= _MEMORY_RATE_CACHE_MAX_SIZE:
            _MEMORY_RATE_CACHE.clear()
        _MEMORY_RATE_CACHE[key] = rate


def _merge_quarter_data(existing: QuarterCacheData, fetched: QuarterCacheData) -> QuarterCacheData:
    merged_by_key: dict[tuple[str, date], FxRate] = {
        (rate.symbol, rate.date): rate for rate in existing.rates
    }
    for rate in fetched.rates:
        merged_by_key[(rate.symbol, rate.date)] = rate

    return QuarterCacheData(
        quarter=fetched.quarter,
        base_currency=fetched.base_currency,
        source=fetched.source,
        fetched_at=fetched.fetched_at,
        rates=sorted(merged_by_key.values(), key=lambda rate: (rate.symbol, rate.date)),
    )


def _decode_response_payload(response: requests.Response) -> str:
    raw = response.content
    encodings: list[str] = ["utf-8-sig"]

    if response.encoding:
        encodings.append(response.encoding)
    apparent = getattr(response, "apparent_encoding", None)
    if apparent:
        encodings.append(apparent)

    encodings.extend(["cp1251", "windows-1251", "utf-8"])

    seen: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw.decode("utf-8", errors="replace")


def _is_na(value: str) -> bool:
    return value.strip().lower() in {"", "n/a", "na", "-"}


def parse_bnb_xml(
    xml_text: str,
    *,
    quarter: QuarterKey,
    symbols: Iterable[str] | None = None,
) -> QuarterCacheData:
    """Parse a BNB XML export payload into normalized quarter cache data."""
    stripped = xml_text.lstrip("\ufeff").strip()
    if not stripped:
        raise ParseError("empty XML payload")

    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        raise ParseError("malformed XML payload") from exc

    rows = root.findall(".//ROW")
    if not rows:
        raise ParseError("XML payload has no ROW elements")

    wanted_symbols = {normalize_symbol(symbol) for symbol in symbols} if symbols else None

    row_maps = [{child.tag: (child.text or "").strip() for child in row} for row in rows]
    header_fragments: list[str] = []
    for row_map in row_maps:
        if row_map.get("GOLD", "").strip() == "0":
            header_fragments.extend(value for value in row_map.values() if value)

    if not header_fragments:
        for row_map in row_maps:
            title = row_map.get("TITLE", "").strip()
            if title:
                header_fragments.append(title)

    base_currency = detect_base_currency(header_fragments)
    quote_field = "REVERSERATE" if base_currency == "EUR" else "RATE"

    parsed_rates: list[FxRate] = []
    for row_map in row_maps:
        gold_marker = row_map.get("GOLD", "").strip()
        if gold_marker == "0":
            continue
        if gold_marker not in {"", "1"}:
            continue

        date_text = row_map.get("CURR_DATE", "")
        symbol_text = row_map.get("CODE", "")
        if not date_text or not symbol_text:
            continue

        try:
            row_date = parse_date(date_text, field_name="row date")
        except ValueError:
            # Skip descriptive/header rows that are not real data entries.
            continue

        if row_date < quarter.start_date or row_date > quarter.end_date:
            continue

        try:
            symbol = normalize_symbol(symbol_text)
        except ValueError:
            continue
        if wanted_symbols is not None and symbol not in wanted_symbols:
            continue

        rate_text = row_map.get(quote_field, "").strip()
        if base_currency == "EUR" and not rate_text:
            # Fallback for older XML exports where REVERSERATE is missing.
            rate_text = row_map.get("RATE", "").strip()

        if _is_na(rate_text):
            raise ParseError(f"symbol {symbol} does not have rates on BNB for {row_date.isoformat()}")

        nominal_text = row_map.get("RATIO", "")
        nominal_value = Decimal("1")
        if nominal_text and nominal_text.lower() not in {"n/a", "na", "-"}:
            nominal_value = parse_decimal(nominal_text, field_name="nominal")

        quoted_rate = parse_decimal(rate_text, field_name="rate")
        parsed_rates.append(
            FxRate(
                symbol=symbol,
                date=row_date,
                rate=quoted_rate,
                nominal=nominal_value,
                base_currency=base_currency,
                raw_row=row_map,
            )
        )

    if not parsed_rates:
        raise ParseError("no valid FX rows parsed from XML payload")

    for item in parsed_rates:
        if is_bgn_period(item.date) and base_currency != "BGN":
            raise ParseError(
                f"base currency mismatch for {item.date.isoformat()}: expected BGN, got {base_currency}"
            )
        if (not is_bgn_period(item.date)) and base_currency != "EUR":
            raise ParseError(
                f"base currency mismatch for {item.date.isoformat()}: expected EUR, got {base_currency}"
            )
    logger.info(
        "Parsed BNB quarter %s (%s): %s rows, %s symbols",
        quarter.label,
        base_currency,
        len(parsed_rates),
        len({item.symbol for item in parsed_rates}),
    )

    return QuarterCacheData(
        quarter=quarter,
        base_currency=base_currency,
        rates=parsed_rates,
    )


class BnbFxClient:
    """Small HTTP client for quarter-sized BNB FX downloads (XML export format)."""

    def __init__(
        self,
        *,
        endpoint: str = BNB_FX_ENDPOINT,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.session = session or requests.Session()

    @staticmethod
    def build_query_params(
        start_date: date,
        end_date: date,
        symbols: Iterable[str] | None = None,
    ) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = [
            ("search", "true"),
            ("group1", "second"),
            ("periodStartDays", f"{start_date.day:02d}"),
            ("periodStartMonths", f"{start_date.month:02d}"),
            ("periodStartYear", str(start_date.year)),
            ("periodEndDays", f"{end_date.day:02d}"),
            ("periodEndMonths", f"{end_date.month:02d}"),
            ("periodEndYear", str(end_date.year)),
            ("downloadOper", "true"),
            ("showChart", "false"),
            ("showChartButton", "true"),
            ("type", "XML"),
            ("lang", "EN"),
        ]
        normalized = sorted({normalize_symbol(symbol) for symbol in symbols}) if symbols else []
        for symbol in normalized:
            params.append(("valutes", symbol))
        return params

    def fetch_xml(self, *, start_date: date, end_date: date, symbols: Iterable[str] | None = None) -> str:
        params = self.build_query_params(start_date, end_date, symbols)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    self.endpoint,
                    params=params,
                    timeout=self.timeout_seconds,
                    headers={"Accept": "application/xml, text/xml, text/plain, */*"},
                )

                if response.status_code >= 500 and attempt < self.max_retries:
                    raise FetchError(f"BNB temporary HTTP {response.status_code}")

                response.raise_for_status()
                if hasattr(response, "content"):
                    payload = _decode_response_payload(response).strip()
                else:
                    payload = str(response.text).lstrip("\ufeff").lstrip("ï»¿").strip()
                if not payload:
                    raise FetchError("BNB response is empty")

                logger.info(
                    "Fetched BNB payload for %s to %s",
                    start_date.isoformat(),
                    end_date.isoformat(),
                )
                return payload
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.retry_backoff_seconds * (attempt + 1))

        raise FetchError(
            f"failed to fetch BNB payload for {start_date.isoformat()} to {end_date.isoformat()}"
        ) from last_error

    def fetch_quarter(
        self,
        quarter: QuarterKey,
        *,
        symbols: Iterable[str] | None = None,
    ) -> QuarterCacheData:
        payload = self.fetch_xml(start_date=quarter.start_date, end_date=quarter.end_date, symbols=symbols)
        return parse_bnb_xml(payload, quarter=quarter, symbols=symbols)

def _fetch_and_cache_quarter(
    quarter: QuarterKey,
    *,
    cache_dir: str | Path | None,
    symbols: Iterable[str] | None,
    client: BnbFxClient,
) -> QuarterCacheData:
    logger.info("Fetching quarter %s", quarter.label)
    data = client.fetch_quarter(quarter, symbols=symbols)
    save_quarter_cache(data, cache_dir=cache_dir)
    return data


def _previous_quarter(quarter: QuarterKey) -> QuarterKey:
    if quarter.quarter == 1:
        return QuarterKey(quarter.year - 1, 4)
    return QuarterKey(quarter.year, quarter.quarter - 1)


def _load_or_fetch_quarter_for_symbol(
    *,
    quarter: QuarterKey,
    symbol: str,
    cache_dir: str | Path | None,
    client: BnbFxClient,
) -> QuarterCacheData:
    cache_key = (_cache_dir_key(cache_dir), quarter)
    with _MEMORY_QUARTER_CACHE_LOCK:
        memory_cached = _MEMORY_QUARTER_CACHE.get(cache_key)
    if memory_cached is not None and memory_cached.has_symbol(symbol):
        logger.debug("In-memory cache hit for %s (%s)", quarter.label, symbol)
        return memory_cached

    cached = load_quarter_cache(quarter, cache_dir=cache_dir)
    if cached is not None:
        _put_quarter_cache_in_memory(cache_key, cached)
        if cached.has_symbol(symbol):
            logger.debug("Cache hit for %s (%s)", quarter.label, symbol)
            return cached

    logger.info("Cache miss for %s (%s); fetching quarter", quarter.label, symbol)
    fetched = _fetch_and_cache_quarter(
        quarter,
        cache_dir=cache_dir,
        symbols=[symbol],
        client=client,
    )
    merged = _merge_quarter_data(cached, fetched) if cached is not None else fetched
    if merged is not fetched:
        save_quarter_cache(merged, cache_dir=cache_dir)
    _put_quarter_cache_in_memory(cache_key, merged)
    return merged


def _to_eur_per_symbol_quote(rate: FxRate) -> FxRate:
    """Convert stored quote to 'EUR for 1 symbol unit'."""
    source_base = rate.base_currency
    if rate.symbol == EUR_SYMBOL:
        converted = Decimal("1")
    elif source_base == "EUR":
        converted = rate.rate / rate.nominal
    elif source_base == "BGN":
        converted = rate.rate / (rate.nominal * EUR_FIXED_RATE_BGN)
    else:
        raise ParseError(f"unsupported source base currency: {source_base}")

    raw_row = dict(rate.raw_row or {})
    raw_row["source_base_currency"] = source_base
    raw_row["quote_semantics"] = "EUR for 1 symbol unit"

    return FxRate(
        symbol=rate.symbol,
        date=rate.date,
        rate=converted,
        base_currency="EUR",
        source=rate.source,
        nominal=Decimal("1"),
        raw_row=raw_row,
    )


def _log_fallback_once(symbol: str, requested_date: date, effective_date: date) -> None:
    key = (symbol, requested_date, effective_date)
    with _FALLBACK_LOGGED_LOCK:
        if key in _FALLBACK_LOGGED:
            return
        _FALLBACK_LOGGED.add(key)

    logger.debug(
        "No exact rate for %s on %s, using previous date %s",
        symbol,
        requested_date.isoformat(),
        effective_date.isoformat(),
    )


def get_exchange_rate(
    symbol: str,
    on_date: date | str,
    cache_dir: str | Path | None = None,
) -> FxRate:
    """Return FX quote for `symbol` on `on_date` as EUR for 1 symbol unit.

    On cache miss the entire containing quarter is downloaded, parsed, and cached.
    If the exact date is missing, the closest previous available date is used.
    """
    normalized_symbol = normalize_symbol(symbol)
    target_date = parse_date(on_date, field_name="on_date")
    cache_dir_key = _cache_dir_key(cache_dir)

    rate_cache_key = (normalized_symbol, target_date, cache_dir_key)
    with _MEMORY_RATE_CACHE_LOCK:
        cached_rate = _MEMORY_RATE_CACHE.get(rate_cache_key)
    if cached_rate is not None:
        return cached_rate

    if normalized_symbol == EUR_SYMBOL:
        rate = FxRate(
            symbol=EUR_SYMBOL,
            date=target_date,
            rate=Decimal("1"),
            nominal=Decimal("1"),
            base_currency="EUR",
            source="BNB",
            raw_row={
                "note": "Identity quote for EUR per 1 EUR",
                "source_base_currency": "BGN",
                "fixed_eur_bgn_rate": str(EUR_FIXED_RATE_BGN),
            },
        )
        _put_rate_cache_in_memory(rate_cache_key, rate)
        return rate

    if normalized_symbol == BGN_SYMBOL:
        rate = FxRate(
            symbol=BGN_SYMBOL,
            date=target_date,
            rate=Decimal("1") / EUR_FIXED_RATE_BGN,
            nominal=Decimal("1"),
            base_currency="EUR",
            source="BNB",
            raw_row={
                "note": "Fixed conversion for BGN using EUR/BGN peg",
                "source_base_currency": "BGN",
                "fixed_eur_bgn_rate": str(EUR_FIXED_RATE_BGN),
            },
        )
        _put_rate_cache_in_memory(rate_cache_key, rate)
        return rate

    quarter = quarter_for_date(target_date)
    client = BnbFxClient()
    current_quarter = quarter

    for lookback_index in range(LOOKBACK_QUARTERS):
        data = _load_or_fetch_quarter_for_symbol(
            quarter=current_quarter,
            symbol=normalized_symbol,
            cache_dir=cache_dir,
            client=client,
        )
        cutoff_date = target_date if lookback_index == 0 else current_quarter.end_date
        found = data.find_latest_on_or_before(normalized_symbol, cutoff_date)
        if found is not None:
            if found.date != target_date:
                _log_fallback_once(normalized_symbol, target_date, found.date)
            converted = _to_eur_per_symbol_quote(found)
            _put_rate_cache_in_memory(rate_cache_key, converted)
            return converted

        current_quarter = _previous_quarter(current_quarter)

    raise RateNotFoundError(
        "No BNB rate found for "
        f"symbol={normalized_symbol} on or before {target_date.isoformat()} "
        f"(looked back {LOOKBACK_QUARTERS} quarters)"
    )


def get_conversion_rate(
    source_symbol: str,
    target_symbol: str,
    on_date: date | str,
    cache_dir: str | Path | None = None,
) -> Decimal:
    source = normalize_symbol(source_symbol)
    target = normalize_symbol(target_symbol)
    if source == target:
        return Decimal("1")

    source_to_eur = get_exchange_rate(source, on_date, cache_dir=cache_dir).rate
    target_to_eur = get_exchange_rate(target, on_date, cache_dir=cache_dir).rate
    if target_to_eur <= Decimal("0"):
        raise ParseError(f"invalid target EUR quote for {target} on {parse_date(on_date).isoformat()}")
    return source_to_eur / target_to_eur


def convert_amount(
    amount: Decimal,
    source_symbol: str,
    target_symbol: str,
    on_date: date | str,
    cache_dir: str | Path | None = None,
) -> Decimal:
    rate = get_conversion_rate(source_symbol, target_symbol, on_date, cache_dir=cache_dir)
    return amount * rate


def _build_cache_for_quarters(
    *,
    quarters: list[QuarterKey],
    symbols: list[str],
    cache_dir: str | Path | None,
) -> CacheBuildResult:
    client = BnbFxClient()
    result = CacheBuildResult()

    normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
    symbols_to_fetch = [symbol for symbol in normalized_symbols if symbol not in {EUR_SYMBOL, BGN_SYMBOL}]

    if not symbols_to_fetch:
        result.skipped_quarters.extend(quarters)
        logger.info("Only EUR/BGN requested; no BNB fetch needed because EUR/BGN is fixed")
        return result

    for quarter in quarters:
        if quarter_is_cached(quarter, cache_dir=cache_dir):
            logger.info("Quarter %s already cached; skipping", quarter.label)
            result.skipped_quarters.append(quarter)
            continue

        try:
            data = _fetch_and_cache_quarter(
                quarter,
                cache_dir=cache_dir,
                symbols=symbols_to_fetch,
                client=client,
            )
            result.fetched_quarters.append(quarter)
            result.rows_written += len(data.rates)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed quarter %s", quarter.label)
            result.failed_quarters[quarter.label] = str(exc)

    return result


def build_cache(
    symbols: list[str],
    start_date: date | str,
    end_date: date | str,
    cache_dir: str | Path | None = None,
) -> CacheBuildResult:
    """Preload quarter cache data intersecting [start_date, end_date]."""
    start = parse_date(start_date, field_name="start_date")
    end = parse_date(end_date, field_name="end_date")
    quarters = quarter_keys_for_period(start, end)
    return _build_cache_for_quarters(quarters=quarters, symbols=symbols, cache_dir=cache_dir)


def build_cache_for_symbols_and_years(
    symbols: list[str],
    years: list[int],
    cache_dir: str | Path | None = None,
) -> CacheBuildResult:
    """Preload quarter cache data for each full year in `years`."""
    if not years:
        raise ValueError("years must not be empty")

    quarters = quarter_keys_for_years(years)
    return _build_cache_for_quarters(quarters=quarters, symbols=symbols, cache_dir=cache_dir)
