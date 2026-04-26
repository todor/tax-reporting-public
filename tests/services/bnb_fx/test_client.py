from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import requests

import services.bnb_fx.client as bnb_client
from services.bnb_fx.client import (
    BnbFxClient,
    EUR_FIXED_RATE_BGN,
    build_cache,
    build_cache_for_symbols_and_years,
    convert_amount,
    get_conversion_rate,
    get_exchange_rate,
)
from services.bnb_fx.models import FxRate, QuarterCacheData, QuarterKey, RateNotFoundError


class _MockResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _make_data(quarter: QuarterKey, *, symbol: str = "USD", on_date: date | None = None) -> QuarterCacheData:
    row_date = on_date or quarter.start_date
    return QuarterCacheData(
        quarter=quarter,
        base_currency="BGN" if row_date <= date(2025, 12, 31) else "EUR",
        rates=[
            FxRate(
                symbol=symbol,
                date=row_date,
                rate=Decimal("1.50"),
                nominal=Decimal("1"),
                base_currency="BGN" if row_date <= date(2025, 12, 31) else "EUR",
            )
        ],
    )


def test_cache_miss_fetches_and_writes_then_lookup_succeeds(monkeypatch, tmp_path) -> None:
    calls: list[QuarterKey] = []

    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        calls.append(quarter)
        return _make_data(quarter, symbol="USD", on_date=date(2024, 2, 15))

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    rate = get_exchange_rate("usd", "2024-02-15", cache_dir=tmp_path)
    assert rate.symbol == "USD"
    assert rate.date == date(2024, 2, 15)
    assert calls == [QuarterKey(2024, 1)]


def test_second_lookup_hits_cache_without_network(monkeypatch, tmp_path) -> None:
    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        return _make_data(quarter, symbol="USD", on_date=date(2024, 2, 15))

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)
    _ = get_exchange_rate("USD", "2024-02-15", cache_dir=tmp_path)

    def fail_fetch(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("network fetch should not happen on cache hit")

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fail_fetch)
    rate = get_exchange_rate("USD", "2024-02-15", cache_dir=tmp_path)
    assert rate.base_currency == "EUR"
    assert rate.rate == Decimal("1.50") / EUR_FIXED_RATE_BGN


def test_build_cache_skips_existing_quarter(monkeypatch, tmp_path) -> None:
    count = {"fetch": 0}

    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        count["fetch"] += 1
        return _make_data(quarter)

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    first = build_cache(["USD"], "2024-01-01", "2024-03-31", cache_dir=tmp_path)
    second = build_cache(["USD"], "2024-01-01", "2024-03-31", cache_dir=tmp_path)

    assert first.fetched_count == 1
    assert second.skipped_count == 1
    assert count["fetch"] == 1


def test_build_cache_for_symbols_and_years_fetches_expected_quarters(monkeypatch, tmp_path) -> None:
    seen: list[QuarterKey] = []

    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        seen.append(quarter)
        return _make_data(quarter)

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    result = build_cache_for_symbols_and_years(["USD", "EUR"], [2024, 2025], cache_dir=tmp_path)

    assert result.fetched_count == 8
    assert len(seen) == 8
    assert seen[0] == QuarterKey(2024, 1)
    assert seen[-1] == QuarterKey(2025, 4)


def test_auto_fetch_uses_full_containing_quarter(monkeypatch, tmp_path) -> None:
    expected_quarter = QuarterKey(2024, 4)
    seen: list[QuarterKey] = []

    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        seen.append(quarter)
        return _make_data(quarter, symbol="USD", on_date=date(2024, 10, 15))

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    _ = get_exchange_rate("USD", "2024-10-15", cache_dir=tmp_path)
    assert seen == [expected_quarter]


def test_missing_rate_raises_rate_not_found(monkeypatch, tmp_path) -> None:
    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        return _make_data(quarter, symbol="EUR", on_date=date(2024, 10, 15))

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)
    monkeypatch.setattr("services.bnb_fx.client.LOOKBACK_QUARTERS", 2)

    with pytest.raises(RateNotFoundError):
        _ = get_exchange_rate("USD", "2024-10-15", cache_dir=tmp_path)


def test_year_boundary_base_currency_behavior(monkeypatch, tmp_path) -> None:
    def _params_dict(params):  # noqa: ANN001, ANN202
        return {key: value for key, value in params}

    def fake_get(self, url, params, timeout, headers):  # noqa: ANN001, ANN201
        params_dict = _params_dict(params)
        start = date(
            int(params_dict["periodStartYear"]),
            int(params_dict["periodStartMonths"]),
            int(params_dict["periodStartDays"]),
        )
        end = date(
            int(params_dict["periodEndYear"]),
            int(params_dict["periodEndMonths"]),
            int(params_dict["periodEndDays"]),
        )

        if start <= date(2025, 12, 31):
            text = f"""\
<?xml version="1.0"?>
<ROWSET>
 <ROW><GOLD>0</GOLD><TITLE>Foreign Exchange Rates of the Bulgarian lev</TITLE><RATE>Levs (BGN)</RATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>{start.strftime("%d.%m.%Y")}</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><RATE>1.7000</RATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>{end.strftime("%d.%m.%Y")}</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><RATE>1.7100</RATE></ROW>
</ROWSET>
"""
        else:
            text = f"""\
<?xml version="1.0"?>
<ROWSET>
 <ROW><GOLD>0</GOLD><TITLE>Foreign Exchange Rates in euro</TITLE><REVERSERATE>Euro (EUR)</REVERSERATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>{start.strftime("%d.%m.%Y")}</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><REVERSERATE>0.8600</REVERSERATE><RATE>1.1628</RATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>{end.strftime("%d.%m.%Y")}</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><REVERSERATE>0.8610</REVERSERATE><RATE>1.1614</RATE></ROW>
</ROWSET>
"""
        return _MockResponse(text)

    monkeypatch.setattr(requests.Session, "get", fake_get)

    rate_2025 = get_exchange_rate("USD", "2025-12-31", cache_dir=tmp_path)
    rate_2026 = get_exchange_rate("USD", "2026-01-01", cache_dir=tmp_path)

    assert rate_2025.base_currency == "EUR"
    assert rate_2026.base_currency == "EUR"


def test_build_cache_splits_into_quarter_chunks_only(monkeypatch, tmp_path) -> None:
    windows: list[tuple[date, date]] = []

    def fake_get(self, url, params, timeout, headers):  # noqa: ANN001, ANN201
        params_dict = {key: value for key, value in params}
        start = date(
            int(params_dict["periodStartYear"]),
            int(params_dict["periodStartMonths"]),
            int(params_dict["periodStartDays"]),
        )
        end = date(
            int(params_dict["periodEndYear"]),
            int(params_dict["periodEndMonths"]),
            int(params_dict["periodEndDays"]),
        )
        windows.append((start, end))

        text = f"""\
<?xml version="1.0"?>
<ROWSET>
 <ROW><GOLD>0</GOLD><TITLE>Foreign Exchange Rates of the Bulgarian lev</TITLE><RATE>Levs (BGN)</RATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>{start.strftime("%d.%m.%Y")}</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><RATE>1.7000</RATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>{end.strftime("%d.%m.%Y")}</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><RATE>1.7100</RATE></ROW>
</ROWSET>
"""
        return _MockResponse(text)

    monkeypatch.setattr(requests.Session, "get", fake_get)

    result = build_cache(["USD"], "2024-01-01", "2025-12-31", cache_dir=tmp_path)

    assert result.fetched_count == 8
    assert len(windows) == 8
    for start, end in windows:
        assert (end - start).days + 1 <= 92


def test_eur_uses_fixed_rate_without_network(tmp_path) -> None:
    rate = get_exchange_rate("EUR", "2025-12-31", cache_dir=tmp_path)
    assert rate.base_currency == "EUR"
    assert rate.rate == Decimal("1")


def test_bgn_uses_fixed_rate_without_network_during_bgn_base_period(monkeypatch, tmp_path) -> None:
    def fail_fetch(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("network fetch should not happen for pre-2026 BGN lookup")

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fail_fetch)

    rate = get_exchange_rate("BGN", "2025-12-31", cache_dir=tmp_path)
    assert rate.base_currency == "EUR"
    assert rate.rate == Decimal("1") / EUR_FIXED_RATE_BGN


def test_bgn_uses_fixed_rate_without_network_post_2026(monkeypatch, tmp_path) -> None:
    def fail_fetch(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("network fetch should not happen for BGN lookup")

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fail_fetch)

    rate = get_exchange_rate("BGN", "2026-01-01", cache_dir=tmp_path)
    assert rate.base_currency == "EUR"
    assert rate.rate == Decimal("1") / EUR_FIXED_RATE_BGN


def test_missing_date_falls_back_to_previous_available_day_same_quarter(monkeypatch, tmp_path) -> None:
    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        return QuarterCacheData(
            quarter=quarter,
            base_currency="BGN",
            rates=[
                FxRate(
                    symbol="USD",
                    date=date(2024, 10, 11),
                    rate=Decimal("1.77"),
                    nominal=Decimal("1"),
                    base_currency="BGN",
                ),
                FxRate(
                    symbol="USD",
                    date=date(2024, 10, 10),
                    rate=Decimal("1.76"),
                    nominal=Decimal("1"),
                    base_currency="BGN",
                ),
            ],
        )

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    # 2024-10-12 is a Saturday, fallback should pick 2024-10-11.
    rate = get_exchange_rate("USD", "2024-10-12", cache_dir=tmp_path)
    assert rate.date == date(2024, 10, 11)
    assert rate.rate == Decimal("1.77") / EUR_FIXED_RATE_BGN


def test_missing_date_falls_back_to_previous_quarter(monkeypatch, tmp_path) -> None:
    calls: list[QuarterKey] = []

    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        calls.append(quarter)
        if quarter == QuarterKey(2024, 4):
            return QuarterCacheData(quarter=quarter, base_currency="BGN", rates=[])
        return QuarterCacheData(
            quarter=quarter,
            base_currency="BGN",
            rates=[
                FxRate(
                    symbol="USD",
                    date=date(2024, 9, 30),
                    rate=Decimal("1.75"),
                    nominal=Decimal("1"),
                    base_currency="BGN",
                )
            ],
        )

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)
    monkeypatch.setattr("services.bnb_fx.client.LOOKBACK_QUARTERS", 2)

    rate = get_exchange_rate("USD", "2024-10-01", cache_dir=tmp_path)
    assert rate.date == date(2024, 9, 30)
    assert rate.rate == Decimal("1.75") / EUR_FIXED_RATE_BGN


def test_fallback_log_emitted_once_for_repeated_lookup(monkeypatch, tmp_path, caplog) -> None:
    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        return QuarterCacheData(
            quarter=quarter,
            base_currency="BGN",
            rates=[
                FxRate(
                    symbol="USD",
                    date=date(2031, 10, 10),
                    rate=Decimal("1.80"),
                    nominal=Decimal("1"),
                    base_currency="BGN",
                )
            ],
        )

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    with caplog.at_level("DEBUG"):
        _ = get_exchange_rate("USD", "2031-10-12", cache_dir=tmp_path)
        _ = get_exchange_rate("USD", "2031-10-12", cache_dir=tmp_path)

    fallback_logs = [record.message for record in caplog.records if "No exact rate for USD on 2031-10-12" in record.message]
    assert len(fallback_logs) == 1


def test_post_2026_returns_eur_for_one_symbol(monkeypatch, tmp_path) -> None:
    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        return QuarterCacheData(
            quarter=quarter,
            base_currency="EUR",
            rates=[
                FxRate(
                    symbol="USD",
                    date=date(2026, 1, 2),
                    rate=Decimal("0.8511"),  # EUR per 1 USD
                    nominal=Decimal("1"),
                    base_currency="EUR",
                )
            ],
        )

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)
    rate = get_exchange_rate("USD", "2026-01-02", cache_dir=tmp_path)

    assert rate.base_currency == "EUR"
    assert rate.rate == Decimal("0.8511")


def test_cache_dir_key_memoizes_default_cache_dir(monkeypatch, tmp_path) -> None:
    calls = {"count": 0}

    def fake_default_cache_dir():  # noqa: ANN201
        calls["count"] += 1
        return tmp_path / "bnb"

    monkeypatch.setattr(bnb_client, "default_cache_dir", fake_default_cache_dir)
    bnb_client._CACHE_DIR_KEY_CACHE.clear()

    first = bnb_client._cache_dir_key(None)
    second = bnb_client._cache_dir_key(None)

    assert first == second
    assert calls["count"] == 1


def test_rate_lookup_is_cached_in_memory_per_symbol_and_date(monkeypatch, tmp_path) -> None:
    load_calls = {"count": 0}
    quarter = QuarterKey(2024, 1)
    cached = _make_data(quarter, symbol="USD", on_date=date(2024, 2, 15))

    def fake_load_quarter_cache(q: QuarterKey, cache_dir=None):  # noqa: ANN001
        load_calls["count"] += 1
        assert q == quarter
        return cached

    monkeypatch.setattr("services.bnb_fx.client.load_quarter_cache", fake_load_quarter_cache)

    first = get_exchange_rate("USD", "2024-02-15", cache_dir=tmp_path)
    second = get_exchange_rate("USD", "2024-02-15", cache_dir=tmp_path)

    assert first.rate == second.rate
    assert load_calls["count"] == 1


def test_quarter_cache_merge_keeps_previously_fetched_symbols(monkeypatch, tmp_path) -> None:
    fetch_calls = {"count": 0}

    def fake_fetch_quarter(self: BnbFxClient, quarter: QuarterKey, symbols=None):  # noqa: ANN001
        fetch_calls["count"] += 1
        assert symbols is not None and len(symbols) == 1
        symbol = str(symbols[0]).upper()
        rate = Decimal("1.50") if symbol == "USD" else Decimal("2.00")
        return QuarterCacheData(
            quarter=quarter,
            base_currency="BGN",
            rates=[
                FxRate(
                    symbol=symbol,
                    date=date(2024, 2, 15),
                    rate=rate,
                    nominal=Decimal("1"),
                    base_currency="BGN",
                )
            ],
        )

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fake_fetch_quarter)

    usd = get_exchange_rate("USD", "2024-02-15", cache_dir=tmp_path)
    gbp = get_exchange_rate("GBP", "2024-02-15", cache_dir=tmp_path)

    assert usd.rate == Decimal("1.50") / EUR_FIXED_RATE_BGN
    assert gbp.rate == Decimal("2.00") / EUR_FIXED_RATE_BGN
    assert fetch_calls["count"] == 2

    # Verify USD remains available after GBP fetch without re-fetching quarter data.
    def fail_fetch(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("unexpected fetch")

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fail_fetch)
    usd_follow_up = get_exchange_rate("USD", "2024-02-16", cache_dir=tmp_path)
    assert usd_follow_up.rate == Decimal("1.50") / EUR_FIXED_RATE_BGN


def test_get_conversion_rate_identity(tmp_path) -> None:
    assert get_conversion_rate("EUR", "EUR", "2025-12-31", cache_dir=tmp_path) == Decimal("1")


def test_get_conversion_rate_eur_bgn_and_bgn_eur_are_fixed(monkeypatch, tmp_path) -> None:
    def fail_fetch(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("network fetch should not happen for EUR/BGN conversion")

    monkeypatch.setattr(BnbFxClient, "fetch_quarter", fail_fetch)

    assert get_conversion_rate("EUR", "BGN", "2025-12-31", cache_dir=tmp_path) == EUR_FIXED_RATE_BGN
    assert get_conversion_rate("BGN", "EUR", "2025-12-31", cache_dir=tmp_path) == Decimal("1") / EUR_FIXED_RATE_BGN
    assert convert_amount(Decimal("10"), "EUR", "BGN", "2025-12-31", cache_dir=tmp_path) == Decimal("10") * EUR_FIXED_RATE_BGN


def test_get_conversion_rate_cross_symbol_uses_eur_normalized_quotes(monkeypatch) -> None:
    def fake_get_exchange_rate(symbol: str, on_date, cache_dir=None):  # noqa: ANN001, ANN202
        if symbol == "USD":
            return FxRate(
                symbol="USD",
                date=date(2026, 1, 1),
                rate=Decimal("0.8500"),
                nominal=Decimal("1"),
                base_currency="EUR",
            )
        if symbol == "CHF":
            return FxRate(
                symbol="CHF",
                date=date(2026, 1, 1),
                rate=Decimal("1.0700"),
                nominal=Decimal("1"),
                base_currency="EUR",
            )
        raise AssertionError(f"unexpected symbol {symbol}")

    monkeypatch.setattr("services.bnb_fx.client.get_exchange_rate", fake_get_exchange_rate)
    assert get_conversion_rate("USD", "CHF", "2026-01-01") == Decimal("0.8500") / Decimal("1.0700")


def test_convert_amount_multiplies_by_conversion_rate(monkeypatch) -> None:
    monkeypatch.setattr("services.bnb_fx.client.get_conversion_rate", lambda *args, **kwargs: Decimal("1.25"))
    assert convert_amount(Decimal("10"), "USD", "CHF", "2026-01-01") == Decimal("12.50")


def test_build_query_params_uses_xml_and_repeated_valutes() -> None:
    params = BnbFxClient.build_query_params(
        date(2026, 1, 1),
        date(2026, 3, 31),
        symbols=["usd", "CHF", "usd"],
    )

    assert ("type", "XML") in params
    assert [value for key, value in params if key == "valutes"] == ["CHF", "USD"]
