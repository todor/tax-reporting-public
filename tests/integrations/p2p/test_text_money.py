from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.p2p.shared.appendix6_models import P2PValidationError
from integrations.p2p.shared.text_money import normalize_text_line, parse_decimal_text


def test_normalize_text_line() -> None:
    assert normalize_text_line("  A\u00a0\u00a0B   C ") == "A B C"


def test_parse_decimal_text_parses_comma_and_dot_forms() -> None:
    assert parse_decimal_text("58.28", field_name="x") == Decimal("58.28")
    assert parse_decimal_text("58,28", field_name="x") == Decimal("58.28")
    assert parse_decimal_text("1,234.56", field_name="x") == Decimal("1234.56")


def test_parse_decimal_text_fails_for_invalid_value() -> None:
    with pytest.raises(P2PValidationError, match="invalid decimal"):
        parse_decimal_text("abc", field_name="x")
