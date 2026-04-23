from __future__ import annotations

import re

from ..constants import (
    APPENDIX_13,
    APPENDIX_5,
    APPENDIX_REVIEW,
    EXCHANGE_ALIASES,
    EXCHANGE_CLASSIFICATION_MODE_CLOSED_WORLD,
    EXCHANGE_CLASSIFICATION_MODE_OPEN_WORLD,
    EXCHANGE_CLASS_INVALID,
    EXCHANGE_CLASS_NON_EU,
    EXCHANGE_CLASS_EU_NON_REGULATED,
    EXCHANGE_CLASS_EU_REGULATED,
    EXCHANGE_CLASS_UNMAPPED,
    EU_NON_REGULATED_MARKETS,
    EU_REGULATED_MARKETS,
    INVALID_EXCHANGE_VALUES,
    KNOWN_NON_EU_MARKETS,
    FOREX_ASSET_CATEGORY,
    SUPPORTED_ASSET_CATEGORIES,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTED_SYMBOL,
)
from ..models import AnalysisSummary, CsvStructureError, InstrumentListing, _ActiveHeader
from ..shared import _build_active_headers, _index_for, _optional_index


def _normalize_exchange(raw: str) -> str:
    normalized = raw.strip().upper()
    if normalized in INVALID_EXCHANGE_VALUES:
        return ""
    if normalized.startswith("EUIBSI"):
        return "EUIBSI"
    return EXCHANGE_ALIASES.get(normalized, normalized)


def _classify_exchange_with_normalized(
    raw: str,
    *,
    eu_regulated_exchange_overrides: set[str] | None = None,
    closed_world_mode: bool = False,
) -> tuple[str, str]:
    normalized = _normalize_exchange(raw)
    if normalized == "":
        return EXCHANGE_CLASS_INVALID, ""
    if normalized in (eu_regulated_exchange_overrides or set()):
        return EXCHANGE_CLASS_EU_REGULATED, normalized
    if normalized in EU_REGULATED_MARKETS:
        return EXCHANGE_CLASS_EU_REGULATED, normalized
    if normalized in EU_NON_REGULATED_MARKETS:
        return EXCHANGE_CLASS_EU_NON_REGULATED, normalized
    if normalized in KNOWN_NON_EU_MARKETS:
        return EXCHANGE_CLASS_NON_EU, normalized
    if closed_world_mode:
        # In closed-world mode, readable normalized codes must not remain unmapped.
        # Unknown readable venues are treated as non-regulated by policy.
        return EXCHANGE_CLASS_EU_NON_REGULATED, normalized
    return EXCHANGE_CLASS_UNMAPPED, normalized


def _classify_exchange(
    raw: str,
    *,
    eu_regulated_exchange_overrides: set[str] | None = None,
    closed_world_mode: bool = False,
) -> str:
    klass, _ = _classify_exchange_with_normalized(
        raw,
        eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
        closed_world_mode=closed_world_mode,
    )
    return klass


def _is_closed_world_mode(
    *,
    eu_regulated_exchange_overrides: set[str] | None = None,
    force_closed_world: bool = False,
) -> bool:
    return force_closed_world or bool(eu_regulated_exchange_overrides)


def _exchange_classification_mode_label(
    *,
    eu_regulated_exchange_overrides: set[str] | None = None,
    force_closed_world: bool = False,
) -> str:
    if _is_closed_world_mode(
        eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
        force_closed_world=force_closed_world,
    ):
        return EXCHANGE_CLASSIFICATION_MODE_CLOSED_WORLD
    return EXCHANGE_CLASSIFICATION_MODE_OPEN_WORLD


def _format_invalid_exchange_value(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned == "":
        return "<EMPTY>"
    return cleaned.upper()


def _record_exchange_observation(
    summary: AnalysisSummary | None,
    *,
    classification: str,
    normalized_exchange: str,
    raw_exchange: str,
) -> None:
    if summary is None:
        return
    if normalized_exchange == "" and classification != EXCHANGE_CLASS_INVALID:
        # Guard against empty placeholders leaking into non-invalid buckets
        # (for example Treasury Bills rows with missing listing venue).
        return
    if classification == EXCHANGE_CLASS_EU_REGULATED:
        summary.encountered_eu_regulated_exchanges.add(normalized_exchange)
        return
    if classification == EXCHANGE_CLASS_EU_NON_REGULATED:
        summary.encountered_eu_non_regulated_exchanges.add(normalized_exchange)
        return
    if classification == EXCHANGE_CLASS_NON_EU:
        summary.encountered_non_eu_exchanges.add(normalized_exchange)
        return
    if classification == EXCHANGE_CLASS_UNMAPPED:
        summary.encountered_unmapped_exchanges.add(normalized_exchange)
        return
    if classification == EXCHANGE_CLASS_INVALID:
        summary.encountered_invalid_exchange_values.add(_format_invalid_exchange_value(raw_exchange))
        return


def _split_symbol_aliases(raw: str) -> list[str]:
    aliases = [part.strip().upper() for part in raw.split(",")]
    return [alias for alias in aliases if alias]


def _is_supported_asset(asset_category: str) -> bool:
    return asset_category.strip() in SUPPORTED_ASSET_CATEGORIES


def _is_forex_asset(asset_category: str) -> bool:
    return asset_category.strip() == FOREX_ASSET_CATEGORY


def _is_treasury_bills_asset(asset_category: str) -> bool:
    return asset_category.strip() == "Treasury Bills"


def _extract_treasury_bill_identifiers(raw_symbol: str) -> list[str]:
    # IBKR Treasury Bills symbols may include free text + embedded CUSIP-like token,
    # e.g. "...<br/>912797NP8 ...". We extract deterministic 9-char uppercase tokens.
    matches = re.findall(r"\b[A-Z0-9]{9}\b", raw_symbol.upper())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in matches:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _extract_isin_from_text(raw: str) -> str:
    candidates = re.findall(r"\b([A-Z]{2}[A-Z0-9]{10})\b", raw.upper())
    if not candidates:
        return ""
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    return ""


def _resolve_instrument_for_trade_symbol(
    *,
    asset_category: str,
    trade_symbol: str,
    listings: dict[str, InstrumentListing],
) -> tuple[InstrumentListing | None, str, str | None]:
    symbol_upper = trade_symbol.strip().upper()
    instrument = listings.get(symbol_upper)
    if instrument is not None:
        return instrument, "", None

    if not _is_treasury_bills_asset(asset_category):
        return None, "", None

    candidates = _extract_treasury_bill_identifiers(symbol_upper)
    if len(candidates) == 1:
        normalized_symbol = candidates[0]
        return listings.get(normalized_symbol), normalized_symbol, None
    if len(candidates) > 1:
        return (
            None,
            "",
            "Treasury Bills symbol contains multiple 9-char identifier candidates; manual review required",
        )
    return (
        None,
        "",
        "Treasury Bills symbol has no 9-char identifier candidate; manual review required",
    )


def _resolve_tax_target(
    *,
    tax_exempt_mode: str,
    listing_exchange_class: str | None,
    execution_exchange_class: str,
    missing_symbol_mapping: bool,
    closed_world_mode: bool,
    forced_review_reason: str | None = None,
) -> tuple[str, str, bool]:
    if forced_review_reason is not None:
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
            return APPENDIX_REVIEW, forced_review_reason, True
        return APPENDIX_5, forced_review_reason, True

    if missing_symbol_mapping:
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
            return APPENDIX_REVIEW, "Missing symbol mapping", True
        return APPENDIX_5, "Missing symbol mapping", True

    if listing_exchange_class is None:
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
            return APPENDIX_REVIEW, "Missing listing exchange classification", True
        return APPENDIX_5, "Missing listing exchange classification", True

    if listing_exchange_class == EXCHANGE_CLASS_INVALID:
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
            return APPENDIX_REVIEW, "Invalid listing exchange", True
        return APPENDIX_5, "Invalid listing exchange", True

    if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL:
        if listing_exchange_class == EXCHANGE_CLASS_UNMAPPED and not closed_world_mode:
            return APPENDIX_5, "Unmapped listing exchange (open-world mode)", True
        if listing_exchange_class != EXCHANGE_CLASS_EU_REGULATED:
            return APPENDIX_5, "Non-EU-listed symbol", False
        return APPENDIX_13, "EU-listed symbol (listed_symbol mode)", False

    # execution_exchange mode (two-stage decision):
    # 1) listing exchange decides immediate Appendix 5 for known non-exempt listings
    # 2) listing EU_REGULATED or UNMAPPED proceeds to execution exchange decision
    if listing_exchange_class in {EXCHANGE_CLASS_NON_EU, EXCHANGE_CLASS_EU_NON_REGULATED}:
        return APPENDIX_5, "Non-EU-listed symbol", False

    if listing_exchange_class not in {EXCHANGE_CLASS_EU_REGULATED, EXCHANGE_CLASS_UNMAPPED}:
        return APPENDIX_REVIEW, "Missing listing exchange classification", True

    if execution_exchange_class == EXCHANGE_CLASS_EU_REGULATED:
        return APPENDIX_13, "EU-listed + EU-regulated execution", False
    if execution_exchange_class == EXCHANGE_CLASS_EU_NON_REGULATED:
        return APPENDIX_5, "EU-listed + non-regulated execution", False
    if execution_exchange_class == EXCHANGE_CLASS_NON_EU:
        return APPENDIX_5, "EU-listed + non-EU execution", False
    if execution_exchange_class == EXCHANGE_CLASS_INVALID:
        return APPENDIX_REVIEW, "EU-listed + invalid execution exchange", True
    if execution_exchange_class == EXCHANGE_CLASS_UNMAPPED:
        return APPENDIX_REVIEW, "EU-listed + unmapped execution", True
    return APPENDIX_REVIEW, "EU-listed + unknown execution classification", True


def parse_instrument_listings(rows: list[list[str]]) -> dict[str, InstrumentListing]:
    active_headers, seen_headers = _build_active_headers(rows)
    return parse_instrument_listings_with_headers(
        rows,
        active_headers=active_headers,
        seen_headers=seen_headers,
        summary=None,
        eu_regulated_exchange_overrides=None,
        closed_world_mode=False,
    )


def parse_instrument_listings_with_headers(
    rows: list[list[str]],
    *,
    active_headers: dict[int, _ActiveHeader],
    seen_headers: set[str],
    summary: AnalysisSummary | None = None,
    eu_regulated_exchange_overrides: set[str] | None = None,
    closed_world_mode: bool = False,
) -> dict[str, InstrumentListing]:
    section_name = "Financial Instrument Information"
    listings: dict[str, InstrumentListing] = {}

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != section_name or row[1] != "Data":
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            raise CsvStructureError(
                f"row {row_number}: {section_name} Data row encountered before {section_name} Header"
            )

        header_name = f"{section_name} header at row {active_header.row_number}"
        asset_idx = _index_for(active_header.headers, "Asset Category", section_name=header_name)
        symbol_idx = _index_for(active_header.headers, "Symbol", section_name=header_name)
        listing_idx = _index_for(active_header.headers, "Listing Exch", section_name=header_name)
        description_idx = _optional_index(active_header.headers, "Description", "Financial Instrument Description", "Name")
        isin_idx = _optional_index(
            active_header.headers,
            "ISIN",
            "Security ID",
            "SecurityID",
            "Security Id",
        )

        data = row[2:] + [""] * (len(active_header.headers) - len(row[2:]))
        asset_category = data[asset_idx].strip()
        if asset_category not in SUPPORTED_ASSET_CATEGORIES:
            continue
        raw_symbol = data[symbol_idx].strip()
        symbols = _split_symbol_aliases(raw_symbol)
        if _is_treasury_bills_asset(asset_category):
            for token in _extract_treasury_bill_identifiers(raw_symbol):
                if token not in symbols:
                    symbols.append(token)
        if not symbols:
            raise CsvStructureError(f"row {row_number}: empty symbol in Financial Instrument Information")

        listing_exchange = data[listing_idx].strip()
        instrument_description = data[description_idx].strip() if description_idx is not None else ""
        instrument_isin = (
            _extract_isin_from_text(data[isin_idx].strip()) if isin_idx is not None else ""
        )
        if instrument_isin == "" and instrument_description != "":
            instrument_isin = _extract_isin_from_text(instrument_description)
        listing_class, listing_exchange_normalized = _classify_exchange_with_normalized(
            listing_exchange,
            eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
            closed_world_mode=closed_world_mode,
        )
        if _is_treasury_bills_asset(asset_category) and listing_class == EXCHANGE_CLASS_INVALID:
            # IBKR Treasury Bills rows can have no listing venue. Treat these as
            # non-EU listed instruments (not invalid listing exchange).
            listing_class = EXCHANGE_CLASS_NON_EU
        is_eu_listed = listing_class == EXCHANGE_CLASS_EU_REGULATED

        canonical_symbol = symbols[0]
        for symbol in symbols:
            new_item = InstrumentListing(
                symbol=symbol,
                canonical_symbol=canonical_symbol,
                listing_exchange=listing_exchange,
                listing_exchange_normalized=listing_exchange_normalized,
                listing_exchange_class=listing_class,
                is_eu_listed=is_eu_listed,
                description=instrument_description,
                isin=instrument_isin,
            )

            existing = listings.get(symbol)
            if existing is None:
                listings[symbol] = new_item
                continue

            if existing.listing_exchange_normalized == new_item.listing_exchange_normalized:
                continue
            if existing.is_eu_listed != new_item.is_eu_listed:
                raise CsvStructureError(
                    f"row {row_number}: conflicting symbol mapping for {symbol}: "
                    f"{existing.listing_exchange_normalized} vs {new_item.listing_exchange_normalized}"
                )

    if section_name not in seen_headers:
        raise CsvStructureError(f"missing section header: {section_name}")
    if not listings:
        raise CsvStructureError("Financial Instrument Information section has no supported symbol mappings")
    return listings


__all__ = [name for name in globals() if not name.startswith("__")]
