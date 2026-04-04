from __future__ import annotations

from datetime import date
from decimal import Decimal

from tax_reporting.services.bnb_fx.cache import (
    load_quarter_cache,
    quarter_cache_path,
    quarter_is_cached,
    save_quarter_cache,
)
from tax_reporting.services.bnb_fx.models import FxRate, QuarterCacheData, QuarterKey


def _sample_cache_data() -> QuarterCacheData:
    quarter = QuarterKey(2024, 1)
    return QuarterCacheData(
        quarter=quarter,
        base_currency="BGN",
        rates=[
            FxRate(
                symbol="USD",
                date=date(2024, 2, 15),
                rate=Decimal("1.7999"),
                nominal=Decimal("1"),
                base_currency="BGN",
            )
        ],
    )


def test_save_and_load_quarter_cache_roundtrip(tmp_path) -> None:
    data = _sample_cache_data()
    path = save_quarter_cache(data, cache_dir=tmp_path)

    assert path.exists()
    loaded = load_quarter_cache(data.quarter, cache_dir=tmp_path)
    assert loaded is not None
    assert loaded.quarter == data.quarter
    assert loaded.base_currency == "BGN"

    rate = loaded.find_rate("USD", date(2024, 2, 15))
    assert rate is not None
    assert rate.rate == Decimal("1.7999")


def test_quarter_cached_flag(tmp_path) -> None:
    quarter = QuarterKey(2024, 1)

    assert quarter_is_cached(quarter, cache_dir=tmp_path) is False
    save_quarter_cache(_sample_cache_data(), cache_dir=tmp_path)
    assert quarter_is_cached(quarter, cache_dir=tmp_path) is True


def test_quarter_cache_path_format(tmp_path) -> None:
    quarter = QuarterKey(2026, 3)
    path = quarter_cache_path(quarter, cache_dir=tmp_path)

    assert path.name == "bnb_2026_Q3.json"
