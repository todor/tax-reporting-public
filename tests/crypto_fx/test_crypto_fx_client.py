from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from crypto_fx.client import floor_to_hour, get_crypto_eur_rate
from crypto_fx.models import (
    AssetNotFoundOnBinanceError,
    CryptoFxRate,
    PricingUnavailableError,
    ResolvedAsset,
    SymbolYearCache,
)


def _resolved(
    *,
    target_symbol: str,
    is_pair: bool,
    exchange: str = "binance",
    base: str | None = None,
    quote: str | None = None,
) -> ResolvedAsset:
    return ResolvedAsset(
        requested_input=target_symbol,
        exchange=exchange,
        is_pair=is_pair,
        base_asset=base,
        quote_asset=quote,
        target_symbol=target_symbol,
        raw_metadata={"x": "y"},
    )


def test_hour_flooring() -> None:
    dt = datetime(2025, 10, 11, 10, 30, 15, tzinfo=timezone.utc)
    assert floor_to_hour(dt) == datetime(2025, 10, 11, 10, 0, 0, tzinfo=timezone.utc)


def test_main_api_accepts_is_future(monkeypatch) -> None:
    captured: dict[str, bool] = {"is_future": False}

    def fake_resolve(symbol_or_pair, exchange, *, is_future=False, session=None):  # noqa: ANN001
        captured["is_future"] = is_future
        return _resolved(target_symbol="EUR", is_pair=False, exchange=exchange)

    monkeypatch.setattr("crypto_fx.client.resolve_target_symbol", fake_resolve)

    result = get_crypto_eur_rate("ETH", "2025-01-01T00:00:00Z", "binance", is_future=True)
    assert captured["is_future"] is True
    assert result.is_future is True
    assert result.pricing_source == "fiat_shortcut"


def test_pair_input_resolves_quote_asset_and_uses_fiat_shortcut(monkeypatch) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="USDT", is_pair=True, base="ALCH", quote="USDT"
        ),
    )
    monkeypatch.setattr("crypto_fx.client._usd_to_eur_rate", lambda on_date: (Decimal("0.91"), on_date))

    rate = get_crypto_eur_rate("ALCHUSDT", "2025-10-11T10:30:15Z", "binance")
    assert rate.is_pair is True
    assert rate.is_future is False
    assert rate.resolved_symbol == "USD"
    assert rate.price_usd == Decimal("1")
    assert rate.price_eur == Decimal("0.91")


def test_single_symbol_resolves_to_itself_and_prices_via_binance(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="BTC", is_pair=False
        ),
    )
    monkeypatch.setattr("crypto_fx.client._usd_to_eur_rate", lambda on_date: (Decimal("0.85"), on_date))

    def fake_load_or_fetch(*, market, exchange, symbol, year, cache_dir, session):  # noqa: ANN001
        assert market == "spot"
        assert exchange == "binance"
        assert symbol == "BTC"
        return SymbolYearCache(
            market="spot",
            exchange="binance",
            symbol="BTC",
            year=2025,
            hourly_close_usd={
                "2025-10-11T10:00:00+00:00": "60000",
                "2025-10-11T09:00:00+00:00": "59000",
            },
        )

    monkeypatch.setattr("crypto_fx.client._load_or_fetch_symbol_year", fake_load_or_fetch)

    result = get_crypto_eur_rate("BTC", "2025-10-11T10:30:15Z", "binance", cache_dir=tmp_path)
    assert result.is_pair is False
    assert result.resolved_symbol == "BTC"
    assert result.timestamp_effective == datetime(2025, 10, 11, 10, 0, tzinfo=timezone.utc)
    assert result.price_usd == Decimal("60000")
    assert result.price_eur == Decimal("51000.00")
    assert result.pricing_source == "binance_spot_close"
    assert result.used_futures_fallback is False


def test_fiat_shortcuts(monkeypatch) -> None:
    monkeypatch.setattr("crypto_fx.client._usd_to_eur_rate", lambda on_date: (Decimal("0.80"), on_date))

    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="EUR", is_pair=False
        ),
    )
    eur_rate = get_crypto_eur_rate("EUR", "2025-01-01T12:00:00Z", "binance", is_future=True)
    assert eur_rate.price_eur == Decimal("1")
    assert eur_rate.is_future is True

    for symbol in ("USD", "USDT", "USDC"):
        monkeypatch.setattr(
            "crypto_fx.client.resolve_target_symbol",
            lambda symbol_or_pair, exchange, is_future=False, session=None, symbol=symbol: _resolved(
                target_symbol=symbol, is_pair=False
            ),
        )
        rate = get_crypto_eur_rate(symbol, "2025-01-01T12:00:00Z", "binance")
        assert rate.resolved_symbol == "USD"
        assert rate.price_eur == Decimal("0.80")


def test_cache_miss_fetch_write_then_second_lookup_hits_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="ETH", is_pair=False
        ),
    )
    monkeypatch.setattr("crypto_fx.client._usd_to_eur_rate", lambda on_date: (Decimal("0.9"), on_date))

    fetch_calls = {"count": 0}

    def fake_fetch(symbol, year, *, market, session):  # noqa: ANN001
        assert market == "spot"
        fetch_calls["count"] += 1
        return {"2025-01-01T10:00:00+00:00": "3000"}

    monkeypatch.setattr("crypto_fx.client._fetch_binance_year", fake_fetch)

    _ = get_crypto_eur_rate("ETH", "2025-01-01T10:30:00Z", "binance", cache_dir=tmp_path)
    assert fetch_calls["count"] == 1

    _ = get_crypto_eur_rate("ETH", "2025-01-01T10:45:00Z", "binance", cache_dir=tmp_path)
    assert fetch_calls["count"] == 1


def test_asset_not_found_on_binance_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="UNKNOWN", is_pair=False
        ),
    )

    def fake_fetch(symbol, year, *, market, session):  # noqa: ANN001
        raise AssetNotFoundOnBinanceError("asset symbol UNKNOWN not found on Binance")

    monkeypatch.setattr("crypto_fx.client._fetch_binance_year", fake_fetch)

    with pytest.raises(AssetNotFoundOnBinanceError):
        _ = get_crypto_eur_rate("UNKNOWN", "2025-01-01T10:30:00Z", "binance", cache_dir=tmp_path)


def test_futures_tries_spot_then_uses_mark_price_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="ETH", is_pair=False
        ),
    )
    monkeypatch.setattr("crypto_fx.client._usd_to_eur_rate", lambda on_date: (Decimal("0.9"), on_date))

    calls: list[str] = []

    def fake_load_or_fetch(*, market, exchange, symbol, year, cache_dir, session):  # noqa: ANN001
        calls.append(market)
        if market == "spot":
            raise AssetNotFoundOnBinanceError("spot not found")
        return SymbolYearCache(
            market="futures",
            exchange="binance",
            symbol="ETH",
            year=2025,
            hourly_close_usd={"2025-10-11T10:00:00+00:00": "3100"},
        )

    monkeypatch.setattr("crypto_fx.client._load_or_fetch_symbol_year", fake_load_or_fetch)

    result = get_crypto_eur_rate(
        "ETH",
        "2025-10-11T10:30:15Z",
        "binance",
        is_future=True,
        cache_dir=tmp_path,
    )
    assert calls[:2] == ["spot", "futures"]
    assert result.pricing_source == "binance_futures_mark_price"
    assert result.used_futures_fallback is True
    assert result.price_usd == Decimal("3100")


def test_futures_prefers_spot_when_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="BTC", is_pair=False
        ),
    )
    monkeypatch.setattr("crypto_fx.client._usd_to_eur_rate", lambda on_date: (Decimal("0.9"), on_date))

    calls: list[str] = []

    def fake_load_or_fetch(*, market, exchange, symbol, year, cache_dir, session):  # noqa: ANN001
        calls.append(market)
        if market == "spot":
            return SymbolYearCache(
                market="spot",
                exchange="binance",
                symbol="BTC",
                year=2025,
                hourly_close_usd={"2025-10-11T10:00:00+00:00": "60000"},
            )
        raise AssertionError("futures should not be called when spot has data")

    monkeypatch.setattr("crypto_fx.client._load_or_fetch_symbol_year", fake_load_or_fetch)

    result = get_crypto_eur_rate(
        "BTC",
        "2025-10-11T10:30:15Z",
        "binance",
        is_future=True,
        cache_dir=tmp_path,
    )
    assert calls[0] == "spot"
    assert result.pricing_source == "binance_spot_close"
    assert result.used_futures_fallback is False


def test_futures_both_spot_and_futures_fail(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="UNKNOWN", is_pair=False
        ),
    )

    def fake_load_or_fetch(*, market, exchange, symbol, year, cache_dir, session):  # noqa: ANN001
        raise AssetNotFoundOnBinanceError(f"{market} missing")

    monkeypatch.setattr("crypto_fx.client._load_or_fetch_symbol_year", fake_load_or_fetch)

    with pytest.raises(PricingUnavailableError):
        _ = get_crypto_eur_rate(
            "UNKNOWN",
            "2025-01-01T10:30:00Z",
            "binance",
            is_future=True,
            cache_dir=tmp_path,
        )


def test_integration_with_bnb_fx_usd_to_eur(monkeypatch) -> None:
    class _DummyBnbRate:
        def __init__(self):
            self.rate = Decimal("0.87")
            self.date = date(2025, 2, 1)

    monkeypatch.setattr("crypto_fx.client.get_fiat_rate", lambda symbol, on_date: _DummyBnbRate())
    monkeypatch.setattr(
        "crypto_fx.client.resolve_target_symbol",
        lambda symbol_or_pair, exchange, is_future=False, session=None: _resolved(
            target_symbol="USDT", is_pair=True
        ),
    )

    result = get_crypto_eur_rate("BTCUSDT", "2025-02-01T00:00:00Z", "binance")
    assert result.price_eur == Decimal("0.87")


def test_cli_behavior(monkeypatch, capsys) -> None:
    from crypto_fx import cli

    monkeypatch.setattr(
        "crypto_fx.cli.get_crypto_eur_rate",
        lambda symbol_or_pair, timestamp, exchange, is_future=False, cache_dir=None: CryptoFxRate(
            requested_input=symbol_or_pair,
            exchange=exchange,
            is_future=is_future,
            resolved_symbol="USD",
            is_pair=True,
            timestamp_requested=datetime(2025, 1, 1, 10, tzinfo=timezone.utc),
            timestamp_effective=datetime(2025, 1, 1, 10, tzinfo=timezone.utc),
            price_usd=Decimal("1"),
            price_eur=Decimal("0.9"),
            source="test",
            pricing_source="binance_spot_close",
            used_futures_fallback=False,
            conversion_path="test",
            raw_metadata=None,
        ),
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "crypto-fx",
            "get-rate",
            "--symbol-or-pair",
            "BTCUSDT",
            "--exchange",
            "binance",
            "--is-future",
            "--timestamps",
            "2025-01-01T10:10:00Z,2025-01-01T11:10:00Z",
        ],
    )

    exit_code = cli.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "requested_input,exchange,is_future,resolved_symbol" in out
    assert out.count("BTCUSDT,binance,True,USD") == 2
