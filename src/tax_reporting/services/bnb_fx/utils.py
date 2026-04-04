from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import ParseError, QuarterKey


_DATE_BOUNDARY = date(2025, 12, 31)


def parse_date(value: date | str, *, field_name: str = "date") -> date:
    if isinstance(value, date):
        return value

    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"invalid {field_name}: {value!r}; expected YYYY-MM-DD or DD.MM.YYYY")


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not value:
        raise ValueError("symbol must not be empty")
    return value


def quarter_for_date(value: date) -> QuarterKey:
    return QuarterKey.from_date(value)


def quarter_keys_for_period(start_date: date, end_date: date) -> list[QuarterKey]:
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    current = quarter_for_date(start_date)
    last = quarter_for_date(end_date)
    quarters: list[QuarterKey] = []

    while (current.year, current.quarter) <= (last.year, last.quarter):
        quarters.append(current)
        if current.quarter == 4:
            current = QuarterKey(current.year + 1, 1)
        else:
            current = QuarterKey(current.year, current.quarter + 1)

    return quarters


def quarter_keys_for_years(years: list[int]) -> list[QuarterKey]:
    keys = [QuarterKey(year, quarter) for year in sorted(set(years)) for quarter in range(1, 5)]
    return keys


def parse_decimal(value: str, *, field_name: str = "rate") -> Decimal:
    cleaned = value.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if cleaned == "":
        raise ParseError(f"missing decimal value for {field_name}")

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ParseError(f"invalid decimal value for {field_name}: {value!r}") from exc


def sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
        return dialect.delimiter
    except csv.Error:
        return ";"


def detect_base_currency(header_fragments: list[str]) -> str:
    text = " ".join(fragment.lower() for fragment in header_fragments)

    has_bgn_markers = any(marker in text for marker in ("bgn", "lev", "лв", "лев"))
    has_eur_markers = any(marker in text for marker in ("eur", "euro", "евро"))

    if has_bgn_markers and not has_eur_markers:
        return "BGN"
    if has_eur_markers and not has_bgn_markers:
        return "EUR"

    # If both appear, prefer the explicit "for one euro"/"за едно евро" style used post-2025.
    if has_eur_markers and any(marker in text for marker in ("for one euro", "за едно евро")):
        return "EUR"

    if has_bgn_markers:
        return "BGN"

    raise ParseError("could not infer base currency (expected BGN/EUR markers in header)")


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_bgn_period(value: date) -> bool:
    return value <= _DATE_BOUNDARY


def quarter_spans_currency_boundary(quarter: QuarterKey) -> bool:
    return quarter.start_date <= _DATE_BOUNDARY < quarter.end_date


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
