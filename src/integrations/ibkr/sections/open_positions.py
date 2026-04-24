from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..constants import (
    ADDED_OPEN_POSITIONS_COLUMNS,
    DECIMAL_EIGHT,
    QTY_RECONCILIATION_EPSILON,
    REVIEW_REASON_OPEN_POSITION_TRADE_QTY_MISMATCH,
    REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT,
    REVIEW_REASON_OPEN_POSITION_UNSUPPORTED_ASSET,
    REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT,
    ZERO,
)
from ..models import (
    AnalysisSummary,
    Appendix8Part1Row,
    CsvStructureError,
    IbkrAnalyzerError,
    InstrumentListing,
    _ActiveHeader,
)
from ..shared import (
    _activate_header,
    _fmt,
    _index_for,
    _optional_index,
    _parse_decimal_loose_or_zero,
    _parse_reconciliation_quantity,
    _set_existing_section_value,
    _to_eur,
)
from .income import _resolve_country_from_isin
from .instruments import (
    _is_supported_asset,
    _resolve_instrument_for_trade_symbol,
)


@dataclass(slots=True)
class OpenPositionsSectionResult:
    row_extras: dict[int, dict[str, str]]
    row_base_len: dict[int, int]
    row_added_columns: dict[int, list[str]]
    part1_by_country_currency: dict[tuple[str, str], Appendix8Part1Row]


@dataclass(slots=True)
class _OpenPositionsFieldIndexes:
    asset: int
    symbol: int
    quantity: int
    discriminator: int
    currency: int | None
    cost_basis: int | None
    country: int | None
    cost_basis_eur: int | None


@dataclass(slots=True)
class _TradeOrderFieldIndexes:
    asset: int
    symbol: int
    quantity: int
    discriminator: int


def _open_positions_indexes(active_header: _ActiveHeader) -> _OpenPositionsFieldIndexes:
    section_name = f"Open Positions header at row {active_header.row_number}"
    return _OpenPositionsFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        quantity=_index_for(active_header.headers, "Summary Quantity", "Quantity", section_name=section_name),
        discriminator=_index_for(
            active_header.headers,
            "DataDiscriminator",
            "Data Discriminator",
            section_name=section_name,
        ),
        currency=_optional_index(active_header.headers, "Currency", "Position Currency"),
        cost_basis=_optional_index(
            active_header.headers,
            "Cost Basis",
            "Cost Basis Money",
            "CostBasis",
            "Cost Basis Amount",
        ),
        country=_optional_index(active_header.headers, "Country"),
        cost_basis_eur=_optional_index(active_header.headers, "Cost Basis (EUR)"),
    )


def _trade_order_indexes(active_header: _ActiveHeader) -> _TradeOrderFieldIndexes:
    section_name = f"Trades header at row {active_header.row_number}"
    return _TradeOrderFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        quantity=_index_for(active_header.headers, "Quantity", "Qty", section_name=section_name),
        discriminator=_index_for(
            active_header.headers,
            "DataDiscriminator",
            "Data Discriminator",
            section_name=section_name,
        ),
    )


def _run_open_position_trade_quantity_reconciliation(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
) -> list[str]:
    warnings: list[str] = []
    open_qty_by_key: dict[tuple[str, str], Decimal] = {}
    trade_qty_by_key: dict[tuple[str, str], Decimal] = {}

    def add_qty(
        bucket: dict[tuple[str, str], Decimal],
        *,
        asset_category: str,
        canonical_symbol: str,
        quantity: Decimal,
    ) -> None:
        key = (asset_category, canonical_symbol)
        bucket[key] = bucket.get(key, ZERO) + quantity

    def canonical_symbol_for_row(
        *,
        asset_category: str,
        symbol_raw: str,
    ) -> tuple[str | None, str | None]:
        instrument, _normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if instrument is None:
            return None, forced_reason or "symbol was not resolved via Financial Instrument Information"
        return instrument.canonical_symbol, None

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Open Positions" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} reason=Open Positions row encountered before header"
            )
            continue
        try:
            field_idx = _open_positions_indexes(active_header)
        except CsvStructureError as exc:
            warnings.append(f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} reason={exc}")
            continue

        base_len = 2 + len(active_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        discriminator = data[field_idx.discriminator].strip().lower()
        if discriminator != "summary":
            continue
        asset_category = data[field_idx.asset].strip()
        if not _is_supported_asset(asset_category):
            continue
        symbol_raw = data[field_idx.symbol].strip()
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if quantity is None:
            warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason=invalid summary quantity"
            )
            continue
        canonical_symbol, resolve_error = canonical_symbol_for_row(
            asset_category=asset_category,
            symbol_raw=symbol_raw,
        )
        if canonical_symbol is None:
            warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason={resolve_error}"
            )
            continue
        add_qty(
            open_qty_by_key,
            asset_category=asset_category,
            canonical_symbol=canonical_symbol,
            quantity=quantity,
        )

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            warnings.append(
                f"{REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT}: row={row_number} reason=Trades row encountered before header"
            )
            continue
        try:
            field_idx = _trade_order_indexes(active_header)
        except CsvStructureError:
            continue

        base_len = 2 + len(active_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        discriminator = data[field_idx.discriminator].strip().lower()
        if discriminator != "order":
            continue
        asset_category = data[field_idx.asset].strip()
        if not _is_supported_asset(asset_category):
            continue
        symbol_raw = data[field_idx.symbol].strip()
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if quantity is None:
            warnings.append(
                f"{REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason=invalid order quantity"
            )
            continue
        canonical_symbol, resolve_error = canonical_symbol_for_row(
            asset_category=asset_category,
            symbol_raw=symbol_raw,
        )
        if canonical_symbol is None:
            warnings.append(
                f"{REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason={resolve_error}"
            )
            continue
        add_qty(
            trade_qty_by_key,
            asset_category=asset_category,
            canonical_symbol=canonical_symbol,
            quantity=quantity,
        )

    for asset_category, canonical_symbol in sorted(set(open_qty_by_key) | set(trade_qty_by_key)):
        expected_open_qty = trade_qty_by_key.get((asset_category, canonical_symbol), ZERO)
        actual_open_qty = open_qty_by_key.get((asset_category, canonical_symbol), ZERO)
        diff = expected_open_qty - actual_open_qty
        if abs(diff) <= QTY_RECONCILIATION_EPSILON:
            continue
        warnings.append(
            f"{REVIEW_REASON_OPEN_POSITION_TRADE_QTY_MISMATCH}: "
            f"asset={asset_category} symbol={canonical_symbol} expected_open_qty={_fmt(expected_open_qty)} "
            f"actual_open_qty={_fmt(actual_open_qty)} diff={_fmt(diff)}"
        )

    return warnings


def run_open_position_reconciliation(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
) -> list[str]:
    return _run_open_position_trade_quantity_reconciliation(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
    )


def process_open_positions_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    summary: AnalysisSummary,
    fx_provider,
    tax_year: int,
) -> OpenPositionsSectionResult:
    row_extras: dict[int, dict[str, str]] = {}
    row_base_len: dict[int, int] = {}
    row_added_columns: dict[int, list[str]] = {}
    part1_by_country_currency: dict[tuple[str, str], Appendix8Part1Row] = {}

    def set_open_positions_extras(row_idx: int, values: dict[str, str]) -> None:
        existing = row_extras.get(row_idx, {})
        for key, value in values.items():
            existing[key] = value
        row_extras[row_idx] = existing

    def appendix8_part1_bucket(
        *,
        country_iso: str,
        country_english: str,
        country_bulgarian: str,
        cost_basis_original_currency: str,
    ) -> Appendix8Part1Row:
        key = (country_iso, cost_basis_original_currency)
        bucket = part1_by_country_currency.get(key)
        if bucket is None:
            bucket = Appendix8Part1Row(
                country_iso=country_iso,
                country_english=country_english,
                country_bulgarian=country_bulgarian,
                cost_basis_original_currency=cost_basis_original_currency,
                acquisition_date=date(tax_year, 12, 31),
            )
            part1_by_country_currency[key] = bucket
        return bucket

    current_open_positions_header: _ActiveHeader | None = None
    appendix8_part1_fx_date = date(tax_year, 12, 31)
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Open Positions":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_open_positions_header = _activate_header("Open Positions", row, row_number=row_number)
            row_base_len[row_idx] = 2 + len(current_open_positions_header.headers)
            row_added_columns[row_idx] = [
                col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in current_open_positions_header.headers
            ]
            continue

        if current_open_positions_header is None:
            raise CsvStructureError(f"row {row_number}: Open Positions row encountered before Open Positions Header")
        row_base_len[row_idx] = 2 + len(current_open_positions_header.headers)
        if row_type != "Data":
            continue

        active_open_positions_header = active_headers.get(row_idx)
        if active_open_positions_header is None:
            raise CsvStructureError(f"row {row_number}: Open Positions Data row encountered before Open Positions Header")
        current_open_positions_header = active_open_positions_header
        row_base_len[row_idx] = 2 + len(active_open_positions_header.headers)
        row_added_columns[row_idx] = [
            col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in active_open_positions_header.headers
        ]

        field_idx = _open_positions_indexes(active_open_positions_header)
        padded = row + [""] * (row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_open_positions_header.headers)]
        discriminator = data[field_idx.discriminator].strip().lower()
        if discriminator != "summary":
            continue

        asset_category = data[field_idx.asset].strip()
        summary.open_positions_summary_rows += 1
        if not _is_supported_asset(asset_category):
            summary.review_required_rows += 1
            summary.warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNSUPPORTED_ASSET}: "
                f"row={row_number} asset={asset_category!r} symbol={data[field_idx.symbol].strip()!r}"
            )
            continue

        symbol_raw = data[field_idx.symbol].strip()
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if quantity is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: invalid Open Positions summary quantity for symbol={symbol_raw!r}"
            )

        instrument, _normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if instrument is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: Open Positions symbol cannot be matched to Financial Instrument "
                f"for symbol={symbol_raw!r}"
                + (f"; reason={forced_reason}" if forced_reason else "")
            )

        country_english = ""
        country_resolved: tuple[str, str, str] | None = None
        if instrument.isin != "":
            country_resolved = _resolve_country_from_isin(instrument.isin)
        if country_resolved is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: Open Positions ISIN is missing/invalid or unmapped country "
                f"for symbol={symbol_raw!r}; cannot build Appendix 8 Part I row"
            )
        _country_iso, country_english, _country_bulgarian = country_resolved

        if field_idx.cost_basis is None:
            raise CsvStructureError(
                f"Open Positions header at row {active_open_positions_header.row_number}: "
                "missing required column Cost Basis"
            )
        parsed_basis = _parse_decimal_loose_or_zero(data[field_idx.cost_basis])
        if parsed_basis is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: invalid Open Positions Cost Basis value for symbol={symbol_raw!r}"
            )
        cost_basis_original = parsed_basis
        if field_idx.currency is None:
            raise CsvStructureError(
                f"Open Positions header at row {active_open_positions_header.row_number}: "
                "missing required column Currency"
            )
        currency = data[field_idx.currency].strip().upper()
        if currency == "":
            raise IbkrAnalyzerError(
                f"row {row_number}: empty Open Positions currency for symbol={symbol_raw!r}; "
                "cannot convert Cost Basis to EUR"
            )
        cost_basis_eur, _ = _to_eur(
            cost_basis_original,
            currency,
            appendix8_part1_fx_date,
            fx_provider,
            row_number=row_number,
        )

        cost_basis_eur_text = _fmt(cost_basis_eur, quant=DECIMAL_EIGHT)
        _set_existing_section_value(
            rows=rows,
            row_idx=row_idx,
            active_header=active_open_positions_header,
            field_idx=field_idx.country,
            value=country_english,
            only_if_empty=True,
        )
        _set_existing_section_value(
            rows=rows,
            row_idx=row_idx,
            active_header=active_open_positions_header,
            field_idx=field_idx.cost_basis_eur,
            value=cost_basis_eur_text,
            only_if_empty=True,
        )
        set_open_positions_extras(
            row_idx,
            {
                "Country": country_english,
                "Cost Basis (EUR)": cost_basis_eur_text,
            },
        )

        country_iso, _, country_bulgarian = country_resolved
        bucket = appendix8_part1_bucket(
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
            cost_basis_original_currency=currency,
        )
        bucket.quantity += quantity
        bucket.cost_basis_original += cost_basis_original
        bucket.cost_basis_eur += cost_basis_eur

    return OpenPositionsSectionResult(
        row_extras=row_extras,
        row_base_len=row_base_len,
        row_added_columns=row_added_columns,
        part1_by_country_currency=part1_by_country_currency,
    )
