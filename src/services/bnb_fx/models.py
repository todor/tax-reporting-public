from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any


class BnbFxError(Exception):
    """Base error for BNB FX domain failures."""


class RateNotFoundError(BnbFxError):
    """Raised when a symbol/date rate is missing after quarter loading."""


class FetchError(BnbFxError):
    """Raised when downloading CSV data from BNB fails."""


class ParseError(BnbFxError):
    """Raised when CSV payload parsing fails."""


class CacheError(BnbFxError):
    """Raised when cache read/write operations fail."""


@dataclass(frozen=True, slots=True)
class QuarterKey:
    """Calendar quarter key used for quarter-sized BNB requests and cache files."""

    year: int
    quarter: int

    def __post_init__(self) -> None:
        if self.quarter not in {1, 2, 3, 4}:
            raise ValueError("quarter must be in 1..4")

    @property
    def start_date(self) -> date:
        month = (self.quarter - 1) * 3 + 1
        return date(self.year, month, 1)

    @property
    def end_date(self) -> date:
        month = self.quarter * 3
        if month == 12:
            return date(self.year, 12, 31)
        next_month = date(self.year, month + 1, 1)
        return next_month.fromordinal(next_month.toordinal() - 1)

    @property
    def label(self) -> str:
        return f"{self.year}_Q{self.quarter}"

    @property
    def cache_file_name(self) -> str:
        return f"bnb_{self.year}_Q{self.quarter}.json"

    @classmethod
    def from_date(cls, value: date) -> QuarterKey:
        quarter = ((value.month - 1) // 3) + 1
        return cls(year=value.year, quarter=quarter)


@dataclass(slots=True)
class FxRate:
    """A single published BNB FX quote for a symbol and date.

    `rate` preserves the quoted CSV value for `nominal` units of the foreign currency.
    Use `rate_per_unit` for normalized "base currency per 1 foreign unit" values.
    """

    symbol: str
    date: date
    rate: Decimal
    base_currency: str
    source: str = "BNB"
    nominal: Decimal = Decimal("1")
    raw_row: dict[str, str] | None = None

    @property
    def rate_per_unit(self) -> Decimal:
        return self.rate / self.nominal

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date.isoformat(),
            "rate": str(self.rate),
            "base_currency": self.base_currency,
            "source": self.source,
            "nominal": str(self.nominal),
            "raw_row": self.raw_row,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FxRate:
        return cls(
            symbol=str(payload["symbol"]),
            date=date.fromisoformat(str(payload["date"])),
            rate=Decimal(str(payload["rate"])),
            base_currency=str(payload["base_currency"]),
            source=str(payload.get("source", "BNB")),
            nominal=Decimal(str(payload.get("nominal", "1"))),
            raw_row=payload.get("raw_row"),
        )


@dataclass(slots=True)
class QuarterCacheData:
    """Normalized parsed quarter payload persisted on disk."""

    quarter: QuarterKey
    base_currency: str
    rates: list[FxRate]
    source: str = "BNB"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def find_rate(self, symbol: str, on_date: date) -> FxRate | None:
        wanted = symbol.upper()
        for rate in self.rates:
            if rate.symbol == wanted and rate.date == on_date:
                return rate
        return None

    def has_symbol(self, symbol: str) -> bool:
        wanted = symbol.upper()
        return any(rate.symbol == wanted for rate in self.rates)

    def find_latest_on_or_before(self, symbol: str, on_date: date) -> FxRate | None:
        wanted = symbol.upper()
        candidates = [rate for rate in self.rates if rate.symbol == wanted and rate.date <= on_date]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.date)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quarter": {
                "year": self.quarter.year,
                "quarter": self.quarter.quarter,
            },
            "base_currency": self.base_currency,
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
            "rates": [rate.to_dict() for rate in self.rates],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QuarterCacheData:
        quarter_payload = payload["quarter"]
        return cls(
            quarter=QuarterKey(
                year=int(quarter_payload["year"]),
                quarter=int(quarter_payload["quarter"]),
            ),
            base_currency=str(payload["base_currency"]),
            source=str(payload.get("source", "BNB")),
            fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
            rates=[FxRate.from_dict(item) for item in payload.get("rates", [])],
        )


@dataclass(slots=True)
class CacheBuildResult:
    """Summary information for quarter cache preloading runs."""

    fetched_quarters: list[QuarterKey] = field(default_factory=list)
    skipped_quarters: list[QuarterKey] = field(default_factory=list)
    failed_quarters: dict[str, str] = field(default_factory=dict)
    rows_written: int = 0

    @property
    def fetched_count(self) -> int:
        return len(self.fetched_quarters)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_quarters)

    @property
    def failed_count(self) -> int:
        return len(self.failed_quarters)
