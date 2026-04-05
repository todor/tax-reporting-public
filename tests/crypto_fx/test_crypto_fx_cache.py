from __future__ import annotations

from datetime import datetime, timezone

from crypto_fx.cache import load_symbol_year_cache, save_symbol_year_cache, symbol_year_cache_path
from crypto_fx.models import SymbolYearCache


def test_save_and_load_symbol_year_cache(tmp_path) -> None:
    data = SymbolYearCache(
        market="spot",
        exchange="binance",
        symbol="BTC",
        year=2025,
        hourly_close_usd={"2025-01-01T00:00:00+00:00": "100000.00"},
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    path = save_symbol_year_cache(data, cache_dir=tmp_path)
    assert path.exists()

    loaded = load_symbol_year_cache(
        market="spot",
        exchange="binance",
        symbol="BTC",
        year=2025,
        cache_dir=tmp_path,
    )
    assert loaded is not None
    assert loaded.market == "spot"
    assert loaded.symbol == "BTC"
    assert loaded.hourly_close_usd["2025-01-01T00:00:00+00:00"] == "100000.00"


def test_cache_path_shape(tmp_path) -> None:
    path = symbol_year_cache_path(
        market="spot",
        exchange="binance",
        symbol="eth",
        year=2024,
        cache_dir=tmp_path,
    )
    assert path.name == "2024.json"
    assert "spot" in str(path)
    assert "binance" in str(path)
    assert "ETH" in str(path)


def test_cache_separation_between_spot_and_futures(tmp_path) -> None:
    spot_path = symbol_year_cache_path(
        market="spot",
        exchange="binance",
        symbol="ETH",
        year=2025,
        cache_dir=tmp_path,
    )
    futures_path = symbol_year_cache_path(
        market="futures",
        exchange="binance",
        symbol="ETH",
        year=2025,
        cache_dir=tmp_path,
    )
    assert spot_path != futures_path
    assert "/spot/" in spot_path.as_posix()
    assert "/futures/" in futures_path.as_posix()
