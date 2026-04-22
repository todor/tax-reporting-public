from __future__ import annotations

import requests

from services.crypto_fx.exchanges import normalize_kraken_symbol, resolve_target_symbol


class _DummySession(requests.Session):
    pass


def test_binance_pair_detection(monkeypatch) -> None:
    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        assert "exchangeInfo" in url
        assert params == {"symbol": "ALCHBTC"}
        return 200, {
            "symbols": [{"symbol": "ALCHBTC", "baseAsset": "ALCH", "quoteAsset": "BTC"}]
        }

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    resolved = resolve_target_symbol("ALCHBTC", "binance", session=_DummySession())
    assert resolved.is_pair is True
    assert resolved.base_asset == "ALCH"
    assert resolved.quote_asset == "BTC"
    assert resolved.target_symbol == "BTC"


def test_binance_futures_pair_detection(monkeypatch) -> None:
    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        assert url.endswith("/fapi/v1/exchangeInfo")
        assert params is None
        return 200, {
            "symbols": [
                {"symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC"},
                {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"},
            ]
        }

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    resolved = resolve_target_symbol("ETHBTC", "binance", is_future=True, session=_DummySession())
    assert resolved.is_pair is True
    assert resolved.base_asset == "ETH"
    assert resolved.quote_asset == "BTC"
    assert resolved.target_symbol == "BTC"


def test_kraken_pair_detection_and_mapping(monkeypatch) -> None:
    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        assert "AssetPairs" in url
        assert params == {"pair": "XXBTZUSD"}
        return 200, {
            "error": [],
            "result": {
                "XXBTZUSD": {
                    "base": "XXBT",
                    "quote": "ZUSD",
                }
            },
        }

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    resolved = resolve_target_symbol("XXBTZUSD", "kraken", session=_DummySession())
    assert resolved.is_pair is True
    assert resolved.base_asset == "BTC"
    assert resolved.quote_asset == "USD"
    assert resolved.target_symbol == "USD"


def test_kraken_futures_pair_detection_and_mapping(monkeypatch) -> None:
    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        assert url.endswith("/derivatives/api/v3/instruments")
        assert params is None
        return 200, {
            "result": "success",
            "instruments": [
                {
                    "symbol": "PF_XXBTZUSD",
                    "base": "XXBT",
                    "quoteCurrency": "ZUSD",
                }
            ],
        }

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    resolved = resolve_target_symbol("PF_XXBTZUSD", "kraken", is_future=True, session=_DummySession())
    assert resolved.is_pair is True
    assert resolved.base_asset == "BTC"
    assert resolved.quote_asset == "USD"
    assert resolved.target_symbol == "USD"


def test_single_symbol_not_pair_uses_symbol_itself(monkeypatch) -> None:
    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        return 400, {"code": -1121, "msg": "Invalid symbol."}

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    resolved = resolve_target_symbol("SOL", "binance", session=_DummySession())
    assert resolved.is_pair is False
    assert resolved.target_symbol == "SOL"


def test_futures_single_symbol_not_pair_uses_symbol_itself(monkeypatch) -> None:
    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        return 200, {"symbols": []}

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    resolved = resolve_target_symbol("SOL", "binance", is_future=True, session=_DummySession())
    assert resolved.is_pair is False
    assert resolved.target_symbol == "SOL"


def test_known_quote_suffix_skips_metadata_calls(monkeypatch) -> None:
    def fail_request_json(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("metadata call should not happen for known quote suffix")

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fail_request_json)

    resolved = resolve_target_symbol("ALCHUSDT", "binance", is_future=True, session=_DummySession())
    assert resolved.is_pair is True
    assert resolved.base_asset == "ALCH"
    assert resolved.quote_asset == "USDT"
    assert resolved.target_symbol == "USDT"


def test_resolution_results_are_cached_for_repeated_calls(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_request_json(session, url, params=None, timeout_seconds=20.0, retries=2):  # noqa: ANN001
        calls["count"] += 1
        assert "exchangeInfo" in url
        assert params == {"symbol": "ALCHBTC"}
        return 200, {
            "symbols": [{"symbol": "ALCHBTC", "baseAsset": "ALCH", "quoteAsset": "BTC"}]
        }

    monkeypatch.setattr("services.crypto_fx.exchanges._request_json", fake_request_json)

    first = resolve_target_symbol("ALCHBTC", "binance", session=_DummySession())
    second = resolve_target_symbol("ALCHBTC", "binance", session=_DummySession())

    assert first.is_pair is True
    assert second.is_pair is True
    assert calls["count"] == 1


def test_kraken_mapping_table() -> None:
    assert normalize_kraken_symbol("XBT") == "BTC"
    assert normalize_kraken_symbol("XXBT") == "BTC"
    assert normalize_kraken_symbol("ZUSD") == "USD"
    assert normalize_kraken_symbol("ZEUR") == "EUR"
    assert normalize_kraken_symbol("ABC") == "ABC"
