from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import integrations.fund.shared.runtime as runtime
from integrations.fund.shared.runtime import default_fund_eur_unit_rate_provider


def test_default_fund_rate_provider_uses_crypto_fx_for_crypto(monkeypatch) -> None:
    calls: dict[str, int] = {"crypto": 0, "bnb": 0}

    def fake_get_crypto_eur_rate(  # noqa: ANN001
        symbol: str,
        timestamp,
        exchange: str,
        cache_dir=None,
        assume_single_symbol: bool = False,
    ):
        calls["crypto"] += 1
        assert symbol == "USDC"
        assert exchange == "binance"
        assert assume_single_symbol is True
        return SimpleNamespace(price_eur=Decimal("0.91"))

    def fake_get_exchange_rate(symbol: str, on_date, cache_dir=None):  # noqa: ANN001
        calls["bnb"] += 1
        return SimpleNamespace(rate=Decimal("0.9"))

    monkeypatch.setattr(runtime, "get_crypto_eur_rate", fake_get_crypto_eur_rate)
    monkeypatch.setattr(runtime, "get_exchange_rate", fake_get_exchange_rate)

    provider = default_fund_eur_unit_rate_provider(cache_dir=None)
    rate = provider("USDC", "crypto", datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert rate == Decimal("0.91")
    assert calls["crypto"] == 1
    assert calls["bnb"] == 0


def test_default_fund_rate_provider_uses_bnb_for_fiat(monkeypatch) -> None:
    calls: dict[str, int] = {"crypto": 0, "bnb": 0}

    def fake_get_crypto_eur_rate(  # noqa: ANN001
        symbol: str,
        timestamp,
        exchange: str,
        cache_dir=None,
        assume_single_symbol: bool = False,
    ):
        calls["crypto"] += 1
        return SimpleNamespace(price_eur=Decimal("0.91"))

    def fake_get_exchange_rate(symbol: str, on_date, cache_dir=None):  # noqa: ANN001
        calls["bnb"] += 1
        assert symbol == "USD"
        return SimpleNamespace(rate=Decimal("0.92"))

    monkeypatch.setattr(runtime, "get_crypto_eur_rate", fake_get_crypto_eur_rate)
    monkeypatch.setattr(runtime, "get_exchange_rate", fake_get_exchange_rate)

    provider = default_fund_eur_unit_rate_provider(cache_dir=None)
    rate = provider("USD", "fiat", datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert rate == Decimal("0.92")
    assert calls["bnb"] == 1
    assert calls["crypto"] == 0
