from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


class CryptoFxError(Exception):
    """Base error for crypto->EUR conversion failures."""


class UnsupportedExchangeError(CryptoFxError):
    """Raised when an unsupported exchange name is provided."""


class AssetNotFoundOnBinanceError(CryptoFxError):
    """Raised when a required Binance symbol is missing."""


class PairResolutionError(CryptoFxError):
    """Raised when pair resolution against an exchange fails."""


class CacheError(CryptoFxError):
    """Raised when cache read/write operations fail."""


class PricingUnavailableError(CryptoFxError):
    """Raised when symbol pricing is unavailable across configured price sources."""


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    requested_input: str
    exchange: str
    is_pair: bool
    base_asset: str | None
    quote_asset: str | None
    target_symbol: str
    raw_metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class CryptoFxRate:
    requested_input: str
    exchange: str
    is_future: bool
    resolved_symbol: str
    is_pair: bool
    timestamp_requested: datetime
    timestamp_effective: datetime
    price_usd: Decimal | None
    price_eur: Decimal
    source: str
    pricing_source: str
    used_futures_fallback: bool
    conversion_path: str
    raw_metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class SymbolYearCache:
    market: str
    exchange: str
    symbol: str
    year: int
    hourly_close_usd: dict[str, str]
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def latest_on_or_before(self, target: datetime) -> tuple[datetime, Decimal] | None:
        best_ts: datetime | None = None
        best_value: Decimal | None = None

        for key, value in self.hourly_close_usd.items():
            ts = datetime.fromisoformat(key)
            if ts > target:
                continue
            if best_ts is None or ts > best_ts:
                best_ts = ts
                best_value = Decimal(value)

        if best_ts is None or best_value is None:
            return None

        return best_ts, best_value

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "year": self.year,
            "hourly_close_usd": self.hourly_close_usd,
            "fetched_at": self.fetched_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SymbolYearCache:
        return cls(
            market=str(payload.get("market", "spot")),
            exchange=str(payload["exchange"]),
            symbol=str(payload["symbol"]),
            year=int(payload["year"]),
            hourly_close_usd={str(k): str(v) for k, v in payload.get("hourly_close_usd", {}).items()},
            fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
        )
