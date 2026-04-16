from __future__ import annotations

import re

from ..constants import (
    APPENDIX_9_DEFAULT_COUNTRY_ISO,
    COUNTRY_NAME_BY_ISO,
    COUNTRY_NAME_TO_ISO,
    DIVIDEND_APPENDIX_6,
    DIVIDEND_APPENDIX_8,
    DIVIDEND_APPENDIX_UNKNOWN,
    INTEREST_DECLARED_TYPES,
    INTEREST_NON_DECLARED_TYPES,
    INTEREST_STATUS_NON_TAXABLE,
    INTEREST_STATUS_TAXABLE,
    INTEREST_STATUS_UNKNOWN,
    _normalize_country_lookup_key,
)
from ..models import IbkrAnalyzerError, InstrumentListing
from .instruments import _resolve_instrument_for_trade_symbol


def _extract_isin(description: str) -> tuple[str | None, str | None]:
    matches = re.findall(r"\(([A-Za-z0-9]{12})\)", description)
    if not matches:
        return None, "missing ISIN in description"
    normalized = [item.upper() for item in matches if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", item.upper())]
    if len(normalized) == 1:
        return normalized[0], None
    if len(normalized) > 1:
        return None, "multiple ISIN candidates in description"
    return None, "invalid ISIN format in description"


def _extract_symbol_from_security_description(description: str) -> str | None:
    match = re.match(r"\s*([A-Za-z0-9._-]+)\s*\([A-Za-z0-9]{12}\)", description)
    if match is None:
        return None
    return match.group(1).upper()


def _resolve_country_from_isin(isin: str) -> tuple[str, str, str] | None:
    iso = isin[:2].upper()
    names = COUNTRY_NAME_BY_ISO.get(iso)
    if names is None:
        return None
    english, bulgarian = names
    return iso, english, bulgarian


def _resolve_country_from_text(country_text: str) -> tuple[str, str, str]:
    text = country_text.strip()
    if text == "":
        raise IbkrAnalyzerError("empty country value")
    normalized_text = _normalize_country_lookup_key(text)
    resolved_iso = COUNTRY_NAME_TO_ISO.get(normalized_text)
    if resolved_iso is not None:
        english, bulgarian = COUNTRY_NAME_BY_ISO[resolved_iso]
        return resolved_iso, english, bulgarian
    upper = text.upper()
    manual_iso = f"MANUAL:{upper}"
    return manual_iso, text, text


def _appendix9_default_country() -> tuple[str, str, str]:
    english, bulgarian = COUNTRY_NAME_BY_ISO[APPENDIX_9_DEFAULT_COUNTRY_ISO]
    return APPENDIX_9_DEFAULT_COUNTRY_ISO, english, bulgarian


def _classify_dividend_description(description: str) -> str:
    lowered = description.lower()
    if "cash dividend" in lowered:
        return DIVIDEND_APPENDIX_8
    if "lieu received" in lowered:
        return DIVIDEND_APPENDIX_6
    return DIVIDEND_APPENDIX_UNKNOWN


def _classify_status_from_description(description: str) -> str:
    lowered = description.lower()
    if "cash dividend" in lowered or "credit interest" in lowered or "lieu received" in lowered:
        return INTEREST_STATUS_TAXABLE
    return INTEREST_STATUS_UNKNOWN


def _extract_period_key_from_description(description: str, *, fallback: str) -> str:
    match = re.search(r"\bfor\s+(.+)$", description, flags=re.IGNORECASE)
    if match is None:
        return fallback
    period = re.sub(r"\s+", " ", match.group(1).strip())
    return period.upper() if period else fallback


def _normalize_interest_type(description: str, *, currency: str) -> str:
    text = description.strip()
    if not text:
        return ""

    if text.upper().startswith(currency.strip().upper() + " "):
        text = text[len(currency.strip()) :].strip()
    else:
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[A-Z]{3,5}", parts[0].upper()):
            text = parts[1].strip()

    text = re.sub(r"\s+for\s+.+$", "", text, flags=re.IGNORECASE).strip()
    return text


def _classify_interest_type(normalized_type: str) -> str:
    if normalized_type in INTEREST_DECLARED_TYPES:
        return INTEREST_STATUS_TAXABLE
    if normalized_type in INTEREST_NON_DECLARED_TYPES:
        return INTEREST_STATUS_NON_TAXABLE
    return INTEREST_STATUS_UNKNOWN


def _resolve_dividend_company_name(
    *,
    description: str,
    listings: dict[str, InstrumentListing],
) -> tuple[str | None, str | None]:
    symbol = _extract_symbol_from_security_description(description)
    if symbol is None:
        return None, "missing symbol token in description"
    instrument, normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
        asset_category="Stocks",
        trade_symbol=symbol,
        listings=listings,
    )
    if instrument is not None:
        description_value = instrument.description.strip()
        if description_value:
            return description_value, None
        return instrument.canonical_symbol, None
    if normalized_symbol:
        return normalized_symbol, forced_reason or "symbol was normalized without instrument mapping"
    return symbol, forced_reason or "symbol was not resolved via Financial Instrument Information"


__all__ = [name for name in globals() if not name.startswith("__")]
