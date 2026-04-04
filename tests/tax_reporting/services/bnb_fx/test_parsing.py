from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tax_reporting.services.bnb_fx.client import parse_bnb_csv, parse_bnb_payload
from tax_reporting.services.bnb_fx.models import ParseError, QuarterKey
from tax_reporting.services.bnb_fx.utils import quarter_for_date

PRE_2026_CSV = """\
BNB historical rates in BGN
Date;Code;Units;Rate in BGN
2025-12-31;usd;1;1.79516
2025-12-31;JPY;100;1,22345
"""

POST_2026_CSV = """\
BNB historical rates in EUR
Date;Code;Units;Euro for one unit of foreign currency;Foreign currency for one euro
2026-01-01;usd;1;0.8666;1.1539
"""

PERIOD_2025_CSV = """\
Курсове на българския лев към отделни чуждестранни валути и цена на златото, валидни за периода от 01.01.2025 до 31.03.2025
Период, Код, за,в BGN, за 1 BGN
02.01.2025, USD, 1,1.895, 0.527704,
"""

PERIOD_2026_CSV = """\
Валутни курсове на чуждестранни валути за  за периода от 01.01.2026 до 31.03.2026
Период, Код, Валута за 1 евро, Евро за единица валута
01.01.2026, USD, 1.1750, 0.8511,
"""

PRE_2026_XML = """\
<?xml version="1.0"?>
<ROWSET>
 <ROW>
  <TITLE>Foreign Exchange Rates of the Bulgarian lev</TITLE>
  <RATE>Levs (BGN)</RATE>
 </ROW>
 <ROW>
  <CURR_DATE>31.12.2025</CURR_DATE>
  <NAME_>US Dollar</NAME_>
  <CODE>USD</CODE>
  <RATIO>1</RATIO>
  <RATE>1.8000</RATE>
 </ROW>
</ROWSET>
"""

POST_2026_XML = """\
<?xml version="1.0"?>
<ROWSET>
 <ROW>
  <TITLE>Foreign Exchange Rates in euro</TITLE>
  <RATE>Euro (EUR)</RATE>
 </ROW>
 <ROW>
  <CURR_DATE>01.01.2026</CURR_DATE>
  <NAME_>US Dollar</NAME_>
  <CODE>USD</CODE>
  <RATIO>1</RATIO>
  <RATE>0.8600</RATE>
 </ROW>
</ROWSET>
"""


def test_quarter_logic_months_and_boundaries() -> None:
    assert quarter_for_date(date(2024, 1, 1)) == QuarterKey(2024, 1)
    assert quarter_for_date(date(2024, 4, 1)) == QuarterKey(2024, 2)
    assert quarter_for_date(date(2024, 7, 1)) == QuarterKey(2024, 3)
    assert quarter_for_date(date(2024, 10, 1)) == QuarterKey(2024, 4)
    assert quarter_for_date(date(2025, 12, 31)) == QuarterKey(2025, 4)
    assert quarter_for_date(date(2026, 1, 1)) == QuarterKey(2026, 1)


def test_parse_pre_2026_header_sets_bgn_base() -> None:
    data = parse_bnb_csv(PRE_2026_CSV, quarter=QuarterKey(2025, 4))

    assert data.base_currency == "BGN"
    usd = data.find_rate("USD", date(2025, 12, 31))
    assert usd is not None
    assert usd.rate == Decimal("1.79516")


def test_parse_post_2026_header_sets_eur_base() -> None:
    data = parse_bnb_csv(POST_2026_CSV, quarter=QuarterKey(2026, 1))

    assert data.base_currency == "EUR"
    usd = data.find_rate("USD", date(2026, 1, 1))
    assert usd is not None
    assert usd.rate == Decimal("0.8666")


def test_parse_xml_pre_2026_detects_bgn_base() -> None:
    data = parse_bnb_payload(PRE_2026_XML, quarter=QuarterKey(2025, 4))
    usd = data.find_rate("USD", date(2025, 12, 31))
    assert usd is not None
    assert usd.base_currency == "BGN"
    assert usd.rate == Decimal("1.8000")


def test_parse_xml_post_2026_detects_eur_base() -> None:
    data = parse_bnb_payload(POST_2026_XML, quarter=QuarterKey(2026, 1))
    usd = data.find_rate("USD", date(2026, 1, 1))
    assert usd is not None
    assert usd.base_currency == "EUR"
    assert usd.rate == Decimal("0.8600")


def test_parse_payload_detects_xml_with_mojibake_bom_prefix() -> None:
    payload = "ï»¿" + PRE_2026_XML
    data = parse_bnb_payload(payload, quarter=QuarterKey(2025, 4))
    usd = data.find_rate("USD", date(2025, 12, 31))
    assert usd is not None
    assert usd.base_currency == "BGN"


def test_parse_period_export_csv_2025_bgn_format() -> None:
    data = parse_bnb_payload(PERIOD_2025_CSV, quarter=QuarterKey(2025, 1))
    usd = data.find_rate("USD", date(2025, 1, 2))
    assert usd is not None
    assert usd.base_currency == "BGN"
    assert usd.nominal == Decimal("1")
    assert usd.rate == Decimal("1.895")


def test_parse_period_export_csv_2026_eur_format() -> None:
    data = parse_bnb_payload(PERIOD_2026_CSV, quarter=QuarterKey(2026, 1))
    usd = data.find_rate("USD", date(2026, 1, 1))
    assert usd is not None
    assert usd.base_currency == "EUR"
    assert usd.rate == Decimal("0.8511")


def test_parse_period_export_csv_skips_na_rate_rows() -> None:
    payload = """\
Курсове на българския лев към отделни чуждестранни валути и цена на златото, валидни за периода от 01.10.2025 до 31.12.2025
Период, Код, за,в BGN, за 1 BGN
01.10.2025, EUR, n/a,n/a, n/a,
01.10.2025, USD, 1,1.7000, 0.5882,
"""
    with pytest.raises(ParseError, match="does not have rates on BNB"):
        parse_bnb_payload(payload, quarter=QuarterKey(2025, 4), symbols=["EUR"])


def test_parsing_normalizes_symbol_and_preserves_nominal() -> None:
    data = parse_bnb_csv(PRE_2026_CSV, quarter=QuarterKey(2025, 4))
    jpy = data.find_rate("jpy", date(2025, 12, 31))

    assert jpy is not None
    assert jpy.symbol == "JPY"
    assert jpy.nominal == Decimal("100")
    assert jpy.rate == Decimal("1.22345")
    assert jpy.rate_per_unit == Decimal("0.0122345")


def test_parsing_invalid_decimal_raises_parse_error() -> None:
    bad_csv = """\
Date;Code;Units;Rate in BGN
2025-12-31;USD;1;not-a-number
"""

    with pytest.raises(ParseError):
        parse_bnb_csv(bad_csv, quarter=QuarterKey(2025, 4))
