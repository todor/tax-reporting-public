from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from integrations.shared.rendering.common import Money, format_money
from integrations.shared.rendering.display_currency import (
    DisplayCurrencyError,
    build_money_render_context,
    display_currency_technical_lines,
    normalize_display_currency,
)


def test_normalize_display_currency_accepts_supported_values() -> None:
    assert normalize_display_currency("EUR") == "EUR"
    assert normalize_display_currency(" bgn ") == "BGN"


def test_normalize_display_currency_rejects_unsupported_value() -> None:
    with pytest.raises(DisplayCurrencyError, match="invalid --display-currency"):
        normalize_display_currency("USD")


def test_build_money_render_context_eur_keeps_identity() -> None:
    context = build_money_render_context(tax_year=2025, display_currency="EUR")
    assert context.display_currency == "EUR"
    assert format_money(Money(Decimal("123.45"), "EUR"), context=context) == "123.45 EUR"


def test_build_money_render_context_bgn_uses_bnb_fx_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Decimal, str, str, date, str | None]] = []

    def fake_convert_amount(
        amount: Decimal,
        source_symbol: str,
        target_symbol: str,
        on_date: date,
        cache_dir=None,
    ):  # noqa: ANN001
        calls.append(
            (
                amount,
                source_symbol,
                target_symbol,
                on_date,
                str(cache_dir) if cache_dir is not None else None,
            )
        )
        return Decimal("1.95583")

    monkeypatch.setattr(
        "integrations.shared.rendering.display_currency.convert_amount",
        fake_convert_amount,
    )

    context = build_money_render_context(
        tax_year=2025,
        display_currency="BGN",
        cache_dir="/tmp/fx-cache",
    )

    assert format_money(Money(Decimal("10"), "EUR"), context=context) == "19.56 BGN"
    assert format_money(Money(Decimal("-2"), "EUR"), context=context) == "-3.91 BGN"
    assert format_money(Money(Decimal("0"), "EUR"), context=context) == "0.00 BGN"
    # Non-EUR monetary values are never converted by display-currency rendering.
    assert format_money(Money(Decimal("5"), "USD"), context=context) == "5.00 USD"

    assert calls == [(Decimal("1"), "EUR", "BGN", date(2025, 12, 31), "/tmp/fx-cache")]


def test_display_currency_technical_lines_rendered_only_for_bgn() -> None:
    eur_context = build_money_render_context(tax_year=2025, display_currency="EUR")
    assert display_currency_technical_lines(eur_context) == []

    bgn_context = build_money_render_context(
        tax_year=2025,
        display_currency="BGN",
        eur_to_bgn_rate_provider=lambda _date: Decimal("1.95583"),
    )
    assert display_currency_technical_lines(bgn_context) == [
        "Display currency: BGN",
        "Calculations currency: EUR",
        "Display FX source: BNB",
        "Display FX date: 2025-12-31",
        "Display FX pair: EUR/BGN",
    ]
