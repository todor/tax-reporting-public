from __future__ import annotations

import csv
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path

import requests

from .cache import load_quarter_cache, quarter_is_cached, save_quarter_cache
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
    normalize_header,
    normalize_symbol,
    parse_date,
    parse_decimal,
    quarter_for_date,
    quarter_keys_for_period,
    quarter_keys_for_years,
    sniff_delimiter,
)

logger = logging.getLogger(__name__)

BNB_FX_ENDPOINT = (
    "https://bnb.bg/Statistics/StExternalSector/StExchangeRates/"
    "StERForeignCurrencies/index.htm"
)
EUR_FIXED_RATE_BGN = Decimal("1.95583")
EUR_SYMBOL = "EUR"
LOOKBACK_QUARTERS = 12


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


def _header_matches(value: str, *tokens: str) -> bool:
    return any(token in value for token in tokens)


def _is_na(value: str) -> bool:
    return value.strip().lower() in {"", "n/a", "na", "-"}


def _looks_like_symbol(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{3}", value.strip()))


def _find_header_index(rows: list[list[str]]) -> int:
    for idx, row in enumerate(rows):
        normalized = [normalize_header(col) for col in row]
        has_date = any(_header_matches(col, "date", "дата", "period", "период") for col in normalized)
        has_rate = any(
            _header_matches(col, "rate", "курс", "euro", "евро", "bgn", "лев", "лв")
            for col in normalized
        )
        has_symbol = any(_header_matches(col, "code", "код", "currency", "валута") for col in normalized)
        if has_date and has_rate and has_symbol:
            return idx

    raise ParseError("could not locate CSV header row")


def _find_column(headers: list[str], *tokens: str) -> int | None:
    for idx, header in enumerate(headers):
        if _header_matches(header, *tokens):
            return idx
    return None


def _choose_rate_column(headers: list[str], base_currency: str) -> int:
    candidates: list[tuple[int, int]] = []

    for idx, header in enumerate(headers):
        if not _header_matches(header, "rate", "курс", "euro", "евро", "bgn", "лев", "лв"):
            continue

        score = 0
        if base_currency == "BGN" and _header_matches(header, "bgn", "лев", "лв"):
            score += 3
        if base_currency == "EUR" and _header_matches(header, "eur", "euro", "евро"):
            score += 3
        if _header_matches(header, "for one unit", "за единица", "per unit"):
            score += 2
        if _header_matches(
            header,
            "for one euro",
            "for 1 euro",
            "за едно евро",
            "за 1 евро",
            "за 1 bgn",
            "for 1 bgn",
            "per 1 bgn",
            "per 1 eur",
            "reverse",
        ):
            score -= 2
        if base_currency == "BGN" and _header_matches(header, "за 1 bgn", "for 1 bgn", "per 1 bgn", "reverse"):
            score -= 3
        if base_currency == "EUR" and _header_matches(
            header,
            "за 1 евро",
            "за 1 eur",
            "for 1 euro",
            "for 1 eur",
            "foreign currency for one euro",
            "foreign currency for 1 euro",
            "валута за 1 евро",
        ):
            score -= 4
        if _header_matches(header, "rate", "курс"):
            score += 1

        candidates.append((score, idx))

    if not candidates:
        raise ParseError("could not detect rate column")

    _, column_idx = max(candidates, key=lambda item: (item[0], item[1]))
    return column_idx


def parse_bnb_csv(
    csv_text: str,
    *,
    quarter: QuarterKey,
    symbols: Iterable[str] | None = None,
) -> QuarterCacheData:
    """Parse a BNB CSV payload into normalized quarter cache data."""
    stripped = csv_text.lstrip("\ufeff").strip()
    if not stripped:
        raise ParseError("empty CSV payload")

    def read_rows(delimiter: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(stripped), delimiter=delimiter)
        return [row for row in reader if any(col.strip() for col in row)]

    delimiter = sniff_delimiter("\n".join(stripped.splitlines()[:5]))
    rows = read_rows(delimiter)
    if not rows:
        raise ParseError("CSV payload has no rows")

    header_index = _find_header_index(rows)
    if len(rows[header_index]) == 1:
        for fallback_delimiter in (",", ";", "\t"):
            retry_rows = read_rows(fallback_delimiter)
            try:
                retry_header_index = _find_header_index(retry_rows)
            except ParseError:
                continue
            if len(retry_rows[retry_header_index]) > 1:
                rows = retry_rows
                header_index = retry_header_index
                break

    header_row = rows[header_index]
    normalized_headers = [normalize_header(col) for col in header_row]
    preamble = [" ".join(row).strip() for row in rows[: header_index + 1]]
    base_currency = detect_base_currency(preamble + header_row)

    date_col = _find_column(normalized_headers, "date", "дата", "period", "период")
    symbol_col = _find_column(normalized_headers, "code", "код")
    if symbol_col is None:
        symbol_col = _find_column(normalized_headers, "currency", "валута")
    nominal_col = _find_column(normalized_headers, "nominal", "unit", "units", "единиц", "ratio")
    if nominal_col is None:
        for idx, header in enumerate(normalized_headers):
            if header == "за" or header == "for":
                nominal_col = idx
                break
    rate_col = _choose_rate_column(normalized_headers, base_currency)

    if date_col is None or symbol_col is None:
        raise ParseError("missing required date/code columns")

    wanted_symbols = {normalize_symbol(symbol) for symbol in symbols} if symbols else None

    parsed_rates: list[FxRate] = []
    is_period_export = (
        date_col == 0 and bool(normalized_headers) and _header_matches(normalized_headers[0], "period", "период")
    )

    for row in rows[header_index + 1 :]:
        padded = row + [""] * (len(header_row) - len(row))
        raw_map = {header_row[idx].strip(): padded[idx].strip() for idx in range(len(header_row))}

        date_text = padded[date_col].strip()
        if not date_text:
            continue

        row_date = parse_date(date_text, field_name="row date")
        if row_date < quarter.start_date or row_date > quarter.end_date:
            continue

        if is_period_export:
            cells = [cell.strip() for cell in row]
            rest = cells[1:]
            while rest and rest[-1] == "":
                rest.pop()

            idx = 0
            while idx < len(rest):
                if not _looks_like_symbol(rest[idx]):
                    idx += 1
                    continue

                symbol = normalize_symbol(rest[idx])
                if base_currency == "BGN":
                    if idx + 2 >= len(rest):
                        break
                    nominal_text = rest[idx + 1]
                    rate_text = rest[idx + 2]
                    step = 4 if idx + 3 < len(rest) else 3
                else:
                    if idx + 2 >= len(rest):
                        break
                    nominal_text = "1"
                    rate_text = rest[idx + 2]
                    step = 3

                if wanted_symbols is not None and symbol not in wanted_symbols:
                    idx += step
                    continue

                if _is_na(rate_text):
                    raise ParseError(
                        f"symbol {symbol} does not have rates on BNB for {row_date.isoformat()}"
                    )

                nominal_value = Decimal("1")
                if not _is_na(nominal_text):
                    nominal_value = parse_decimal(nominal_text, field_name="nominal")

                quoted_rate = parse_decimal(rate_text, field_name="rate")
                parsed_rates.append(
                    FxRate(
                        symbol=symbol,
                        date=row_date,
                        rate=quoted_rate,
                        nominal=nominal_value,
                        base_currency=base_currency,
                        raw_row=raw_map,
                    )
                )
                idx += step

            continue

        symbol_text = padded[symbol_col].strip()
        if not symbol_text:
            continue

        symbol = normalize_symbol(symbol_text)
        if wanted_symbols is not None and symbol not in wanted_symbols:
            continue

        rate_text = padded[rate_col].strip()
        if _is_na(rate_text):
            raise ParseError(f"symbol {symbol} does not have rates on BNB for {row_date.isoformat()}")

        nominal_value = Decimal("1")
        if nominal_col is not None and padded[nominal_col].strip():
            nominal_text = padded[nominal_col].strip()
            if not _is_na(nominal_text):
                nominal_value = parse_decimal(nominal_text, field_name="nominal")

        quoted_rate = parse_decimal(rate_text, field_name="rate")
        parsed_rates.append(
            FxRate(
                symbol=symbol,
                date=row_date,
                rate=quoted_rate,
                nominal=nominal_value,
                base_currency=base_currency,
                raw_row=raw_map,
            )
        )

    for item in parsed_rates:
        if is_bgn_period(item.date) and base_currency != "BGN":
            raise ParseError(
                f"base currency mismatch for {item.date.isoformat()}: expected BGN, got {base_currency}"
            )
        if (not is_bgn_period(item.date)) and base_currency != "EUR":
            raise ParseError(
                f"base currency mismatch for {item.date.isoformat()}: expected EUR, got {base_currency}"
            )

    if not parsed_rates:
        raise ParseError("no valid FX rows parsed from CSV payload")

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

    header_fragments: list[str] = []
    parsed_rates: list[FxRate] = []

    for row in rows:
        row_map = {child.tag: (child.text or "").strip() for child in row}
        header_fragments.extend(value for value in row_map.values() if value)

        date_text = row_map.get("CURR_DATE", "")
        symbol_text = row_map.get("CODE", "")
        rate_text = row_map.get("RATE", "")

        if not date_text or not symbol_text:
            continue
        if rate_text.lower() in {"n/a", "na", "-"}:
            continue

        row_date = parse_date(date_text, field_name="row date")
        if row_date < quarter.start_date or row_date > quarter.end_date:
            continue

        symbol = normalize_symbol(symbol_text)
        if wanted_symbols is not None and symbol not in wanted_symbols:
            continue

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
                base_currency="",
                raw_row=row_map,
            )
        )

    if not parsed_rates:
        raise ParseError("no valid FX rows parsed from XML payload")

    base_currency = detect_base_currency(header_fragments)
    for item in parsed_rates:
        if is_bgn_period(item.date) and base_currency != "BGN":
            raise ParseError(
                f"base currency mismatch for {item.date.isoformat()}: expected BGN, got {base_currency}"
            )
        if (not is_bgn_period(item.date)) and base_currency != "EUR":
            raise ParseError(
                f"base currency mismatch for {item.date.isoformat()}: expected EUR, got {base_currency}"
            )
        item.base_currency = base_currency

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


def parse_bnb_payload(
    payload: str,
    *,
    quarter: QuarterKey,
    symbols: Iterable[str] | None = None,
) -> QuarterCacheData:
    stripped = payload.lstrip("\ufeff").lstrip("ï»¿").strip()
    if not stripped:
        raise ParseError("empty BNB payload")

    probe = stripped[:400].lower()
    if "<?xml" in probe or "<rowset" in probe:
        logger.debug("Detected XML payload format")
        return parse_bnb_xml(stripped, quarter=quarter, symbols=symbols)

    logger.debug("Detected CSV payload format")
    return parse_bnb_csv(stripped, quarter=quarter, symbols=symbols)


class BnbCsvClient:
    """Small HTTP client for quarter-sized BNB FX downloads (CSV/XML export formats)."""

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
    ) -> dict[str, str]:
        params: dict[str, str] = {
            "search": "true",
            "group1": "second",
            "periodStartDays": f"{start_date.day:02d}",
            "periodStartMonths": f"{start_date.month:02d}",
            "periodStartYear": str(start_date.year),
            "periodEndDays": f"{end_date.day:02d}",
            "periodEndMonths": f"{end_date.month:02d}",
            "periodEndYear": str(end_date.year),
            "downloadOper": "true",
            "showChart": "false",
            "showChartButton": "true",
            "type": "CSV",
            "lang": "EN",
        }
        normalized = sorted({normalize_symbol(symbol) for symbol in symbols}) if symbols else []
        if normalized:
            params["valutes"] = ",".join(normalized)
        return params

    def fetch_csv(self, *, start_date: date, end_date: date, symbols: Iterable[str] | None = None) -> str:
        params = self.build_query_params(start_date, end_date, symbols)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    self.endpoint,
                    params=params,
                    timeout=self.timeout_seconds,
                    headers={"Accept": "text/csv, text/plain, */*"},
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
        payload = self.fetch_csv(start_date=quarter.start_date, end_date=quarter.end_date, symbols=symbols)
        return parse_bnb_payload(payload, quarter=quarter, symbols=symbols)


def _fetch_and_cache_quarter(
    quarter: QuarterKey,
    *,
    cache_dir: str | Path | None,
    symbols: Iterable[str] | None,
    client: BnbCsvClient,
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
    client: BnbCsvClient,
) -> QuarterCacheData:
    cached = load_quarter_cache(quarter, cache_dir=cache_dir)
    if cached is not None and cached.has_symbol(symbol):
        logger.info("Cache hit for %s (%s)", quarter.label, symbol)
        return cached

    logger.info("Cache miss for %s (%s); fetching quarter", quarter.label, symbol)
    return _fetch_and_cache_quarter(
        quarter,
        cache_dir=cache_dir,
        symbols=[symbol],
        client=client,
    )


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
    if normalized_symbol == EUR_SYMBOL:
        return FxRate(
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

    quarter = quarter_for_date(target_date)
    client = BnbCsvClient()
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
                logger.info(
                    "No exact rate for %s on %s, using previous date %s",
                    normalized_symbol,
                    target_date.isoformat(),
                    found.date.isoformat(),
                )
            return _to_eur_per_symbol_quote(found)

        current_quarter = _previous_quarter(current_quarter)

    raise RateNotFoundError(
        "No BNB rate found for "
        f"symbol={normalized_symbol} on or before {target_date.isoformat()} "
        f"(looked back {LOOKBACK_QUARTERS} quarters)"
    )


def _build_cache_for_quarters(
    *,
    quarters: list[QuarterKey],
    symbols: list[str],
    cache_dir: str | Path | None,
) -> CacheBuildResult:
    client = BnbCsvClient()
    result = CacheBuildResult()

    normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
    symbols_to_fetch = [symbol for symbol in normalized_symbols if symbol != EUR_SYMBOL]

    if not symbols_to_fetch:
        result.skipped_quarters.extend(quarters)
        logger.info("Only EUR requested; no BNB fetch needed because EUR/BGN is fixed")
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
