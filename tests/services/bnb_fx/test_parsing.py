from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from services.bnb_fx.client import parse_bnb_xml
from services.bnb_fx.models import ParseError, QuarterKey
from services.bnb_fx.utils import quarter_for_date

PRE_2026_XML = """\
<?xml version="1.0"?>
<ROWSET>
 <ROW>
  <GOLD>0</GOLD>
  <TITLE>Foreign Exchange Rates of the Bulgarian lev</TITLE>
  <RATE>Levs (BGN)</RATE>
 </ROW>
 <ROW>
  <GOLD>1</GOLD>
  <CURR_DATE>31.12.2025</CURR_DATE>
  <NAME_>US Dollar</NAME_>
  <CODE>USD</CODE>
  <RATIO>1</RATIO>
  <RATE>1.8000</RATE>
 </ROW>
 <ROW>
  <GOLD>1</GOLD>
  <CURR_DATE>31.12.2025</CURR_DATE>
  <NAME_>Japanese yen</NAME_>
  <CODE>JPY</CODE>
  <RATIO>100</RATIO>
  <RATE>1.9000</RATE>
 </ROW>
</ROWSET>
"""

POST_2026_XML = """\
<?xml version="1.0"?>
<ROWSET>
 <ROW>
  <GOLD>0</GOLD>
  <TITLE>Foreign Exchange Rates in euro</TITLE>
  <REVERSERATE>Euro (EUR)</REVERSERATE>
 </ROW>
 <ROW>
  <GOLD>1</GOLD>
  <CURR_DATE>01.01.2026</CURR_DATE>
  <NAME_>US Dollar</NAME_>
  <CODE>USD</CODE>
  <RATIO>1</RATIO>
  <REVERSERATE>0.8600</REVERSERATE>
  <RATE>1.1628</RATE>
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


def test_parse_xml_pre_2026_detects_bgn_base() -> None:
    data = parse_bnb_xml(PRE_2026_XML, quarter=QuarterKey(2025, 4))
    usd = data.find_rate("USD", date(2025, 12, 31))
    assert usd is not None
    assert usd.base_currency == "BGN"
    assert usd.rate == Decimal("1.8000")


def test_parse_xml_post_2026_detects_eur_base_and_uses_reverserate() -> None:
    data = parse_bnb_xml(POST_2026_XML, quarter=QuarterKey(2026, 1))
    usd = data.find_rate("USD", date(2026, 1, 1))
    assert usd is not None
    assert usd.base_currency == "EUR"
    assert usd.rate == Decimal("0.8600")


def test_parse_xml_handles_utf8_bom_prefix() -> None:
    payload = "\ufeff" + PRE_2026_XML
    data = parse_bnb_xml(payload, quarter=QuarterKey(2025, 4))
    usd = data.find_rate("USD", date(2025, 12, 31))
    assert usd is not None
    assert usd.base_currency == "BGN"


def test_parse_xml_real_sample_uses_only_gold_data_rows_and_reverserate() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "exchange_rates_3.xml"
    xml_text = fixture_path.read_text(encoding="utf-8")
    data = parse_bnb_xml(xml_text, quarter=QuarterKey(2026, 1), symbols=["USD", "CHF"])

    usd = data.find_rate("USD", date(2026, 1, 1))
    chf = data.find_rate("CHF", date(2026, 1, 5))
    assert usd is not None
    assert chf is not None
    assert usd.base_currency == "EUR"
    assert usd.rate == Decimal("0.8511")
    assert chf.rate == Decimal("1.0765")


def test_parse_xml_normalizes_symbol_and_preserves_nominal() -> None:
    data = parse_bnb_xml(PRE_2026_XML, quarter=QuarterKey(2025, 4))
    jpy = data.find_rate("jpy", date(2025, 12, 31))

    assert jpy is not None
    assert jpy.symbol == "JPY"
    assert jpy.nominal == Decimal("100")
    assert jpy.rate == Decimal("1.9000")
    assert jpy.rate_per_unit == Decimal("0.019")


def test_parse_xml_with_na_rate_raises_parse_error() -> None:
    bad_xml = """\
<?xml version="1.0"?>
<ROWSET>
 <ROW><GOLD>0</GOLD><TITLE>Foreign Exchange Rates in euro</TITLE><REVERSERATE>Euro (EUR)</REVERSERATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>01.01.2026</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><REVERSERATE>n/a</REVERSERATE></ROW>
</ROWSET>
"""
    with pytest.raises(ParseError, match="does not have rates on BNB"):
        parse_bnb_xml(bad_xml, quarter=QuarterKey(2026, 1), symbols=["USD"])


def test_parse_xml_invalid_decimal_raises_parse_error() -> None:
    bad_xml = """\
<?xml version="1.0"?>
<ROWSET>
 <ROW><GOLD>0</GOLD><TITLE>Foreign Exchange Rates in euro</TITLE><REVERSERATE>Euro (EUR)</REVERSERATE></ROW>
 <ROW><GOLD>1</GOLD><CURR_DATE>01.01.2026</CURR_DATE><CODE>USD</CODE><RATIO>1</RATIO><REVERSERATE>not-a-number</REVERSERATE></ROW>
</ROWSET>
"""
    with pytest.raises(ParseError):
        parse_bnb_xml(bad_xml, quarter=QuarterKey(2026, 1))
