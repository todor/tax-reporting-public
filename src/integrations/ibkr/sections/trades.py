from __future__ import annotations

from dataclasses import dataclass
import logging
from decimal import Decimal
from typing import Literal

from ..appendices.declaration_text import _sum_bucket
from ..constants import (
    ADDED_TRADES_COLUMNS,
    APPENDIX_13,
    APPENDIX_5,
    APPENDIX_IGNORED,
    APPENDIX_REVIEW,
    DECIMAL_EIGHT,
    EXCHANGE_CLASS_INVALID,
    EXCHANGE_CLASS_EU_REGULATED,
    EXCHANGE_CLASS_UNMAPPED,
    REVIEW_STATUS_NON_TAXABLE,
    REVIEW_STATUS_TAXABLE,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTED_SYMBOL,
    ZERO,
    FxRateProvider,
)
from ..models import (
    AnalysisSummary,
    CsvStructureError,
    IbkrAnalyzerError,
    InstrumentListing,
    ReviewEntry,
    _ActiveHeader,
)
from ..shared import (
    _code_has_closing_token,
    _fmt,
    _index_for,
    _normalize_review_status,
    _optional_index,
    _parse_closedlot_date,
    _parse_decimal,
    _parse_decimal_or_zero,
    _parse_trade_datetime,
    _to_eur,
    _try_parse_decimal,
)
from .instruments import (
    _classify_exchange_with_normalized,
    _record_exchange_observation,
    _is_forex_asset,
    _is_supported_asset,
    _resolve_instrument_for_trade_symbol,
    _resolve_tax_target,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TradesSectionResult:
    row_extras: dict[int, list[str]]
    row_base_len: dict[int, int]


@dataclass(slots=True)
class _TradeFieldIndexes:
    asset: int
    currency: int
    symbol: int
    date_time: int
    exchange: int | None
    code: int
    proceeds: int
    basis: int | None
    discriminator: int
    commission: int | None
    review_status: int | None


@dataclass(slots=True)
class _TradeRowContext:
    row_idx: int
    row_number: int
    active_header: _ActiveHeader
    field_idx: _TradeFieldIndexes
    data: list[str]
    asset_category: str
    symbol_raw: str
    symbol: str
    currency: str
    code: str
    is_closing_trade: bool
    proceeds: Decimal
    commission: Decimal
    trade_basis: Decimal | None
    trade_date: object
    realized_pl: Decimal | None
    execution_exchange_raw: str
    execution_exchange_norm: str
    execution_exchange_class: str
    proceeds_eur: Decimal
    trade_fx_rate: Decimal
    commission_eur: Decimal
    trade_basis_eur_from_trade: Decimal | None
    realized_pl_eur: Decimal | None


def _trade_indexes(active_header: _ActiveHeader) -> _TradeFieldIndexes:
    section_name = f"Trades header at row {active_header.row_number}"
    return _TradeFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        date_time=_index_for(active_header.headers, "Date/Time", section_name=section_name),
        exchange=_optional_index(active_header.headers, "Exchange", "Exch", "Execution Exchange"),
        code=_index_for(active_header.headers, "Code", section_name=section_name),
        proceeds=_index_for(active_header.headers, "Proceeds", section_name=section_name),
        basis=_optional_index(active_header.headers, "Basis", "Cost Basis", "CostBasis"),
        discriminator=_index_for(active_header.headers, "DataDiscriminator", section_name=section_name),
        commission=_optional_index(active_header.headers, "Comm/Fee", "Commission"),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _set_trade_extras(
    row_extras: dict[int, list[str]],
    *,
    row_idx: int,
    values: dict[str, str],
) -> None:
    extras = [""] * len(ADDED_TRADES_COLUMNS)
    for key, value in values.items():
        extras[ADDED_TRADES_COLUMNS.index(key)] = value
    row_extras[row_idx] = extras


def _parse_trade_context(
    *,
    row_idx: int,
    row_number: int,
    row: list[str],
    active_trades_header: _ActiveHeader,
    field_idx: _TradeFieldIndexes,
    row_base_len: dict[int, int],
    fx_provider: FxRateProvider,
    eu_regulated_exchange_overrides: set[str],
    closed_world_mode: bool,
) -> _TradeRowContext:
    padded = row + [""] * (row_base_len[row_idx] - len(row))
    data = padded[2 : 2 + len(active_trades_header.headers)]
    asset_category = data[field_idx.asset].strip()
    symbol_raw = data[field_idx.symbol].strip()
    symbol = symbol_raw.upper()
    currency = data[field_idx.currency].strip().upper()
    code = data[field_idx.code].strip()
    is_closing_trade = _code_has_closing_token(code)
    proceeds = _parse_decimal(data[field_idx.proceeds], row_number=row_number, field_name="Proceeds")
    commission = (
        _parse_decimal_or_zero(data[field_idx.commission], row_number=row_number, field_name="Comm/Fee")
        if field_idx.commission is not None
        else ZERO
    )
    trade_basis: Decimal | None = None
    if field_idx.basis is not None:
        trade_basis_raw = data[field_idx.basis].strip()
        if trade_basis_raw != "":
            trade_basis = _parse_decimal(trade_basis_raw, row_number=row_number, field_name="Basis")
    trade_dt = _parse_trade_datetime(data[field_idx.date_time], row_number=row_number)
    trade_date = trade_dt.date()
    realized_idx = _optional_index(
        active_trades_header.headers,
        "Realized P/L",
        "Realized P&L",
        "Realized Profit and Loss",
        "RealizedProfitLoss",
    )
    realized_pl: Decimal | None = None
    if realized_idx is not None:
        realized_raw = data[realized_idx].strip()
        if realized_raw != "":
            realized_pl = _parse_decimal(realized_raw, row_number=row_number, field_name="Realized P/L")

    execution_exchange_raw = data[field_idx.exchange].strip() if field_idx.exchange is not None else ""
    execution_exchange_class, execution_exchange_norm = _classify_exchange_with_normalized(
        execution_exchange_raw,
        eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
        closed_world_mode=closed_world_mode,
    )

    proceeds_eur, trade_fx_rate = _to_eur(
        proceeds,
        currency,
        trade_date,
        fx_provider,
        row_number=row_number,
    )
    commission_eur, _ = _to_eur(
        commission,
        currency,
        trade_date,
        fx_provider,
        row_number=row_number,
    )
    trade_basis_eur_from_trade: Decimal | None = None
    if trade_basis is not None:
        trade_basis_eur_from_trade, _ = _to_eur(
            trade_basis,
            currency,
            trade_date,
            fx_provider,
            row_number=row_number,
        )
    realized_pl_eur: Decimal | None = None
    if realized_pl is not None:
        realized_pl_eur, _ = _to_eur(
            realized_pl,
            currency,
            trade_date,
            fx_provider,
            row_number=row_number,
        )

    return _TradeRowContext(
        row_idx=row_idx,
        row_number=row_number,
        active_header=active_trades_header,
        field_idx=field_idx,
        data=data,
        asset_category=asset_category,
        symbol_raw=symbol_raw,
        symbol=symbol,
        currency=currency,
        code=code,
        is_closing_trade=is_closing_trade,
        proceeds=proceeds,
        commission=commission,
        trade_basis=trade_basis,
        trade_date=trade_date,
        realized_pl=realized_pl,
        execution_exchange_raw=execution_exchange_raw,
        execution_exchange_norm=execution_exchange_norm,
        execution_exchange_class=execution_exchange_class,
        proceeds_eur=proceeds_eur,
        trade_fx_rate=trade_fx_rate,
        commission_eur=commission_eur,
        trade_basis_eur_from_trade=trade_basis_eur_from_trade,
        realized_pl_eur=realized_pl_eur,
    )


def _find_attached_closedlot_indices(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    row_base_len: dict[int, int],
    row_idx: int,
) -> list[int]:
    closedlot_indices: list[int] = []
    scan_idx = row_idx + 1
    while scan_idx < len(rows):
        scan_row = rows[scan_idx]
        if len(scan_row) < 2 or scan_row[0] != "Trades" or scan_row[1] != "Data":
            break
        scan_header = active_headers.get(scan_idx)
        if scan_header is None:
            raise CsvStructureError(f"row {scan_idx + 1}: Trades Data row encountered before Trades Header")
        row_base_len[scan_idx] = 2 + len(scan_header.headers)
        scan_idxes = _trade_indexes(scan_header)
        padded_scan = scan_row + [""] * (row_base_len[scan_idx] - len(scan_row))
        scan_data = padded_scan[2 : 2 + len(scan_header.headers)]
        scan_discriminator = scan_data[scan_idxes.discriminator].strip()
        if scan_discriminator.lower() != "closedlot":
            break
        closedlot_indices.append(scan_idx)
        scan_idx += 1
    return closedlot_indices


def _set_forex_trade_extras(
    *,
    ctx: _TradeRowContext,
    tax_exempt_mode: str,
    row_extras: dict[int, list[str]],
    tax_treatment_reason: str,
    review_required: bool,
    review_notes: str,
) -> None:
    values: dict[str, str] = {
        "Fx Rate": _fmt(ctx.trade_fx_rate, quant=DECIMAL_EIGHT),
        "Comm/Fee (EUR)": _fmt(ctx.commission_eur, quant=DECIMAL_EIGHT),
        "Proceeds (EUR)": _fmt(ctx.proceeds_eur, quant=DECIMAL_EIGHT),
        "Tax Exempt Mode": tax_exempt_mode,
        "Appendix Target": APPENDIX_IGNORED,
        "Tax Treatment Reason": tax_treatment_reason,
        "Review Required": "YES" if review_required else "NO",
        "Review Notes": review_notes,
    }
    if ctx.trade_basis_eur_from_trade is not None:
        values["Basis (EUR)"] = _fmt(ctx.trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
    if ctx.realized_pl_eur is not None:
        values["Realized P/L (EUR)"] = _fmt(ctx.realized_pl_eur, quant=DECIMAL_EIGHT)
    _set_trade_extras(row_extras, row_idx=ctx.row_idx, values=values)


def _apply_forex_review_status(
    summary: AnalysisSummary,
    *,
    row_number: int,
    symbol: str,
    execution_exchange_norm: str,
    review_status_normalized: str,
) -> tuple[str, bool, str]:
    reason = "Forex ignored (not included in Appendix 5/13)"
    review_required = False
    review_notes_parts: list[str] = []

    if review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
        summary.review_status_overrides_rows += 1
        summary.forex_non_taxable_ignored_rows += 1
        reason = "Forex ignored: Review Status override NON-TAXABLE"
        review_notes_parts.append("Review Status override applied")
        return reason, review_required, "; ".join(review_notes_parts)

    summary.forex_review_required_rows += 1
    summary.review_required_rows += 1
    review_required = True

    if review_status_normalized == REVIEW_STATUS_TAXABLE:
        summary.review_status_overrides_rows += 1
        reason = "Forex ignored: Review Status override TAXABLE (taxable forex not supported)"
        review_notes_parts.append("Review Status override applied")
    elif review_status_normalized == "":
        reason = "Forex ignored: missing Review Status (taxable forex not supported)"
    else:
        summary.unknown_review_status_rows += 1
        summary.unknown_review_status_values.add(review_status_normalized)
        reason = (
            f"Forex ignored: unknown Review Status={review_status_normalized} "
            "(taxable forex not supported)"
        )
        review_notes_parts.append("Unknown Review Status value")

    warning = (
        f"row {row_number}: {reason} "
        f"(symbol={symbol}, execution_exchange={execution_exchange_norm or '<EMPTY>'})"
    )
    summary.warnings.append(warning)
    logger.debug("%s", warning)
    return reason, review_required, "; ".join(review_notes_parts)


def _set_non_closing_trade_extras(
    *,
    ctx: _TradeRowContext,
    tax_exempt_mode: str,
    row_extras: dict[int, list[str]],
) -> None:
    values: dict[str, str] = {
        "Fx Rate": _fmt(ctx.trade_fx_rate, quant=DECIMAL_EIGHT),
        "Comm/Fee (EUR)": _fmt(ctx.commission_eur, quant=DECIMAL_EIGHT),
        "Proceeds (EUR)": _fmt(ctx.proceeds_eur, quant=DECIMAL_EIGHT),
        "Realized P/L (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
        "Realized P/L Wins (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
        "Realized P/L Losses (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
        "Tax Exempt Mode": tax_exempt_mode,
        "Tax Treatment Reason": "Non-closing Trade row (informational only)",
        "Review Required": "NO",
    }
    if ctx.trade_basis_eur_from_trade is not None:
        values["Basis (EUR)"] = _fmt(ctx.trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
    _set_trade_extras(row_extras, row_idx=ctx.row_idx, values=values)


def _sum_closedlot_basis_eur(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    row_base_len: dict[int, int],
    closedlot_indices: list[int],
    row_extras: dict[int, list[str]],
    consumed_closedlots: set[int],
    fx_provider: FxRateProvider,
    fallback_currency: str,
) -> Decimal:
    closedlot_basis_eur_sum = ZERO
    for closed_idx in closedlot_indices:
        closed_row_number = closed_idx + 1
        closed_row = rows[closed_idx]
        closed_header = active_headers.get(closed_idx)
        if closed_header is None:
            raise CsvStructureError(f"row {closed_row_number}: Trades Data row encountered before Trades Header")
        closed_idxes = _trade_indexes(closed_header)
        row_base_len[closed_idx] = 2 + len(closed_header.headers)
        padded_closed = closed_row + [""] * (row_base_len[closed_idx] - len(closed_row))
        closed_data = padded_closed[2 : 2 + len(closed_header.headers)]
        if closed_idxes.basis is None:
            raise CsvStructureError(
                f"Trades header at row {closed_header.row_number}: missing required column; "
                "expected one of ('Basis', 'Cost Basis', 'CostBasis')"
            )
        closed_basis_raw = closed_data[closed_idxes.basis]
        closed_basis = _parse_decimal(closed_basis_raw, row_number=closed_row_number, field_name="Basis")
        closed_dt = _parse_closedlot_date(closed_data[closed_idxes.date_time], row_number=closed_row_number)
        closed_currency = closed_data[closed_idxes.currency].strip().upper() or fallback_currency
        closed_basis_eur, closed_fx_rate = _to_eur(
            closed_basis,
            closed_currency,
            closed_dt,
            fx_provider,
            row_number=closed_row_number,
        )
        closedlot_basis_eur_sum += closed_basis_eur
        consumed_closedlots.add(closed_idx)
        _set_trade_extras(
            row_extras,
            row_idx=closed_idx,
            values={
                "Fx Rate": _fmt(closed_fx_rate, quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(closed_basis_eur, quant=DECIMAL_EIGHT),
            },
        )
    return closedlot_basis_eur_sum


def _apply_review_override(
    summary: AnalysisSummary,
    *,
    review_status_normalized: str,
    appendix_target: str,
    reason: str,
    review_required: bool,
) -> tuple[str, str, bool, str]:
    review_notes_parts: list[str] = []
    if review_status_normalized == REVIEW_STATUS_TAXABLE:
        appendix_target = APPENDIX_5
        reason = "Review Status override: TAXABLE"
        review_required = False
        summary.review_status_overrides_rows += 1
        review_notes_parts.append("Review Status override applied")
    elif review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
        appendix_target = APPENDIX_13
        reason = "Review Status override: NON-TAXABLE"
        review_required = False
        summary.review_status_overrides_rows += 1
        review_notes_parts.append("Review Status override applied")
    elif review_status_normalized != "":
        reason = f"{reason}; unknown Review Status={review_status_normalized}"
        review_required = True
        summary.unknown_review_status_rows += 1
        summary.unknown_review_status_values.add(review_status_normalized)
        review_notes_parts.append("Unknown Review Status value")
    return appendix_target, reason, review_required, "; ".join(review_notes_parts)


def _process_closing_trade_row(
    *,
    ctx: _TradeRowContext,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    summary: AnalysisSummary,
    fx_provider: FxRateProvider,
    tax_year: int,
    tax_exempt_mode: str,
    closed_world_mode: bool,
    row_extras: dict[int, list[str]],
    row_base_len: dict[int, int],
    consumed_closedlots: set[int],
    closedlot_indices: list[int],
) -> None:
    closedlot_basis_eur_sum = _sum_closedlot_basis_eur(
        rows=rows,
        active_headers=active_headers,
        row_base_len=row_base_len,
        closedlot_indices=closedlot_indices,
        row_extras=row_extras,
        consumed_closedlots=consumed_closedlots,
        fx_provider=fx_provider,
        fallback_currency=ctx.currency,
    )
    trade_basis_eur = -closedlot_basis_eur_sum

    cash_leg_eur = ctx.proceeds_eur + ctx.commission_eur
    if cash_leg_eur >= ZERO:
        sale_price_component_eur = abs(cash_leg_eur)
        purchase_component_eur = abs(trade_basis_eur)
    else:
        sale_price_component_eur = abs(trade_basis_eur)
        purchase_component_eur = abs(cash_leg_eur)
    pnl_eur = ctx.proceeds_eur + trade_basis_eur + ctx.commission_eur

    pnl_win = pnl_eur if pnl_eur > 0 else ZERO
    pnl_loss = -pnl_eur if pnl_eur < 0 else ZERO

    instrument, normalized_symbol, forced_review_reason = _resolve_instrument_for_trade_symbol(
        asset_category=ctx.asset_category,
        trade_symbol=ctx.symbol_raw,
        listings=listings,
    )
    symbol_for_messages = normalized_symbol or ctx.symbol
    missing_symbol_mapping = instrument is None
    listing_exchange = instrument.listing_exchange_normalized if instrument is not None else ""
    listing_exchange_class = instrument.listing_exchange_class if instrument is not None else None
    symbol_is_eu_listed: bool | None = None if instrument is None else instrument.is_eu_listed

    appendix_target, reason, review_required = _resolve_tax_target(
        tax_exempt_mode=tax_exempt_mode,
        listing_exchange_class=listing_exchange_class,
        execution_exchange_class=ctx.execution_exchange_class,
        missing_symbol_mapping=missing_symbol_mapping,
        closed_world_mode=closed_world_mode,
        forced_review_reason=forced_review_reason,
    )

    review_status_raw = (
        ctx.data[ctx.field_idx.review_status].strip()
        if ctx.field_idx.review_status is not None
        else ""
    )
    review_status_normalized = _normalize_review_status(review_status_raw)
    appendix_target, reason, review_required, review_notes = _apply_review_override(
        summary,
        review_status_normalized=review_status_normalized,
        appendix_target=appendix_target,
        reason=reason,
        review_required=review_required,
    )

    if review_required:
        summary.review_required_rows += 1
        if review_notes == "":
            review_notes = "Review required by tax mode rules"
        # In execution_exchange mode, rows routed to the REVIEW bucket are
        # rendered with full numeric detail in the dedicated review section.
        # Keep processing notes for non-review warnings to avoid duplication.
        skip_duplicate_review_warning = (
            tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE
            and appendix_target == APPENDIX_REVIEW
        )
        if not skip_duplicate_review_warning:
            summary.warnings.append(
                f"row {ctx.row_number}: {reason} (symbol={symbol_for_messages}, execution_exchange={ctx.execution_exchange_norm or '<EMPTY>'})"
            )
        logger.debug(
            "row %s marked REVIEW_REQUIRED: %s (symbol=%s, execution_exchange=%s)",
            ctx.row_number,
            reason,
            symbol_for_messages,
            ctx.execution_exchange_norm or "<EMPTY>",
        )

    in_tax_year = ctx.trade_date.year == tax_year
    if in_tax_year:
        # Mode-scoped audit source:
        # - listed_symbol: listing exchange only
        # - execution_exchange: always listing; execution only when listing
        #   is EU_REGULATED or UNMAPPED (the branch where execution participates
        #   in final routing)
        if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL:
            if listing_exchange_class is not None:
                _record_exchange_observation(
                    summary,
                    classification=listing_exchange_class,
                    normalized_exchange=listing_exchange,
                    raw_exchange=listing_exchange,
                )
        else:
            if listing_exchange_class is not None:
                _record_exchange_observation(
                    summary,
                    classification=listing_exchange_class,
                    normalized_exchange=listing_exchange,
                    raw_exchange=listing_exchange,
                )
            if listing_exchange_class in {EXCHANGE_CLASS_EU_REGULATED, EXCHANGE_CLASS_UNMAPPED}:
                _record_exchange_observation(
                    summary,
                    classification=ctx.execution_exchange_class,
                    normalized_exchange=ctx.execution_exchange_norm,
                    raw_exchange=ctx.execution_exchange_raw,
                )
            elif listing_exchange_class == EXCHANGE_CLASS_INVALID:
                # Audit discovery exception:
                # when listing exchange is invalid/missing, still surface a readable
                # execution venue in audit buckets (for transparency/debugging),
                # even though tax routing for the row stays review-required.
                _record_exchange_observation(
                    summary,
                    classification=ctx.execution_exchange_class,
                    normalized_exchange=ctx.execution_exchange_norm,
                    raw_exchange=ctx.execution_exchange_raw,
                )
        summary.processed_trades_in_tax_year += 1
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE and appendix_target == APPENDIX_REVIEW:
            summary.review_rows += 1
            summary.review_exchanges.add(ctx.execution_exchange_norm or "<EMPTY>")
            _sum_bucket(summary.review, sale_price_component_eur, purchase_component_eur, pnl_eur)
            summary.review_entries.append(
                ReviewEntry(
                    row_number=ctx.row_number,
                    symbol=symbol_for_messages,
                    trade_date=ctx.trade_date.isoformat(),
                    listing_exchange=listing_exchange or "<MISSING>",
                    execution_exchange=ctx.execution_exchange_norm or "<EMPTY>",
                    reason=reason,
                    proceeds_eur=ctx.proceeds_eur,
                    basis_eur=trade_basis_eur,
                    pnl_eur=pnl_eur,
                )
            )
        elif appendix_target == APPENDIX_13:
            _sum_bucket(summary.appendix_13, sale_price_component_eur, purchase_component_eur, pnl_eur)
        else:
            _sum_bucket(summary.appendix_5, sale_price_component_eur, purchase_component_eur, pnl_eur)
    else:
        summary.trades_outside_tax_year += 1

    _set_trade_extras(
        row_extras,
        row_idx=ctx.row_idx,
        values={
            "Fx Rate": _fmt(ctx.trade_fx_rate, quant=DECIMAL_EIGHT),
            "Comm/Fee (EUR)": _fmt(ctx.commission_eur, quant=DECIMAL_EIGHT),
            "Proceeds (EUR)": _fmt(ctx.proceeds_eur, quant=DECIMAL_EIGHT),
            "Basis (EUR)": _fmt(trade_basis_eur, quant=DECIMAL_EIGHT),
            "Sale Price (EUR)": _fmt(sale_price_component_eur, quant=DECIMAL_EIGHT),
            "Purchase Price (EUR)": _fmt(purchase_component_eur, quant=DECIMAL_EIGHT),
            "Realized P/L (EUR)": _fmt(pnl_eur, quant=DECIMAL_EIGHT),
            "Realized P/L Wins (EUR)": _fmt(pnl_win, quant=DECIMAL_EIGHT),
            "Realized P/L Losses (EUR)": _fmt(pnl_loss, quant=DECIMAL_EIGHT),
            "Normalized Symbol": normalized_symbol,
            "Listing Exchange": listing_exchange,
            "Symbol Listed On EU Regulated Market": (
                "YES" if symbol_is_eu_listed else "NO" if symbol_is_eu_listed is not None else ""
            ),
            "Execution Exchange Classification": ctx.execution_exchange_class,
            "Tax Exempt Mode": tax_exempt_mode,
            "Appendix Target": appendix_target,
            "Tax Treatment Reason": reason,
            "Review Required": "YES" if review_required else "NO",
            "Review Notes": review_notes,
        },
    )


def process_trades_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    summary: AnalysisSummary,
    fx_provider: FxRateProvider,
    tax_year: int,
    tax_exempt_mode: Literal["listed_symbol", "execution_exchange"],
    eu_regulated_exchange_overrides: set[str],
    closed_world_mode: bool,
) -> TradesSectionResult:
    row_extras: dict[int, list[str]] = {}
    row_base_len: dict[int, int] = {}
    consumed_closedlots: set[int] = set()
    current_trades_header: _ActiveHeader | None = None
    seen_trades_header = False
    found_trade_section_data = False

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1

        if len(row) < 2 or row[0] != "Trades":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_trades_header = _ActiveHeader(section="Trades", row_number=row_number, headers=row[2:])
            seen_trades_header = True
            row_base_len[row_idx] = 2 + len(current_trades_header.headers)
            continue

        if current_trades_header is None:
            raise CsvStructureError(f"row {row_number}: Trades row encountered before Trades Header")

        row_base_len[row_idx] = 2 + len(current_trades_header.headers)
        if row_type != "Data":
            continue

        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            raise CsvStructureError(f"row {row_number}: Trades Data row encountered before Trades Header")
        current_trades_header = active_trades_header
        row_base_len[row_idx] = 2 + len(active_trades_header.headers)
        field_idx = _trade_indexes(active_trades_header)

        found_trade_section_data = True
        summary.trades_data_rows_total += 1
        padded = row + [""] * (row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]
        lowered = data[field_idx.discriminator].strip().lower()
        if lowered == "trade":
            summary.trade_discriminator_rows += 1
        elif lowered == "closedlot":
            summary.closedlot_discriminator_rows += 1
        elif lowered == "order":
            summary.order_discriminator_rows += 1

        if row_idx in consumed_closedlots:
            continue

        if lowered == "closedlot":
            raise IbkrAnalyzerError(
                f"row {row_number}: orphan ClosedLot row detected (must immediately follow a Trade row)"
            )
        if lowered != "trade":
            summary.ignored_non_closing_trade_rows += 1
            continue

        ctx = _parse_trade_context(
            row_idx=row_idx,
            row_number=row_number,
            row=row,
            active_trades_header=active_trades_header,
            field_idx=field_idx,
            row_base_len=row_base_len,
            fx_provider=fx_provider,
            eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
            closed_world_mode=closed_world_mode,
        )
        closedlot_indices = _find_attached_closedlot_indices(
            rows=rows,
            active_headers=active_headers,
            row_base_len=row_base_len,
            row_idx=row_idx,
        )

        if _is_forex_asset(ctx.asset_category):
            summary.forex_ignored_rows += 1
            summary.forex_ignored_abs_proceeds_eur += abs(ctx.proceeds_eur)
            review_status_raw = (
                ctx.data[ctx.field_idx.review_status].strip()
                if ctx.field_idx.review_status is not None
                else ""
            )
            review_status_normalized = _normalize_review_status(review_status_raw)
            reason, review_required, review_notes = _apply_forex_review_status(
                summary,
                row_number=ctx.row_number,
                symbol=ctx.symbol,
                execution_exchange_norm=ctx.execution_exchange_norm,
                review_status_normalized=review_status_normalized,
            )
            for closed_idx in closedlot_indices:
                consumed_closedlots.add(closed_idx)
            _set_forex_trade_extras(
                ctx=ctx,
                tax_exempt_mode=tax_exempt_mode,
                row_extras=row_extras,
                tax_treatment_reason=reason,
                review_required=review_required,
                review_notes=review_notes,
            )
            continue

        if not _is_supported_asset(ctx.asset_category):
            raise IbkrAnalyzerError(
                f"Unsupported Asset Category encountered: {ctx.asset_category}. Review required before using analyzer."
            )

        if not ctx.is_closing_trade:
            summary.ignored_non_closing_trade_rows += 1
            _set_non_closing_trade_extras(
                ctx=ctx,
                tax_exempt_mode=tax_exempt_mode,
                row_extras=row_extras,
            )
            continue

        summary.closing_trade_candidates += 1

        if not closedlot_indices:
            raise IbkrAnalyzerError(f"row {row_number}: no ClosedLot rows attached to closing Trade")
        _process_closing_trade_row(
            ctx=ctx,
            rows=rows,
            active_headers=active_headers,
            listings=listings,
            summary=summary,
            fx_provider=fx_provider,
            tax_year=tax_year,
            tax_exempt_mode=tax_exempt_mode,
            closed_world_mode=closed_world_mode,
            row_extras=row_extras,
            row_base_len=row_base_len,
            consumed_closedlots=consumed_closedlots,
            closedlot_indices=closedlot_indices,
        )

    if not seen_trades_header:
        raise CsvStructureError("missing section header: Trades")
    if not found_trade_section_data:
        raise CsvStructureError("Trades section has no Data rows")

    return TradesSectionResult(
        row_extras=row_extras,
        row_base_len=row_base_len,
    )


def _aggregate_col_indices() -> dict[str, int]:
    return {
        "comm": ADDED_TRADES_COLUMNS.index("Comm/Fee (EUR)"),
        "proceeds": ADDED_TRADES_COLUMNS.index("Proceeds (EUR)"),
        "basis": ADDED_TRADES_COLUMNS.index("Basis (EUR)"),
        "sale_price": ADDED_TRADES_COLUMNS.index("Sale Price (EUR)"),
        "purchase_price": ADDED_TRADES_COLUMNS.index("Purchase Price (EUR)"),
        "realized": ADDED_TRADES_COLUMNS.index("Realized P/L (EUR)"),
    }


def _ensure_agg_bucket(
    bucket: dict[tuple[str, str] | tuple[str, str, str], dict[str, Decimal]],
    key: tuple[str, str] | tuple[str, str, str],
) -> dict[str, Decimal]:
    item = bucket.get(key)
    if item is None:
        item = {
            "proceeds": ZERO,
            "basis": ZERO,
            "comm_fee": ZERO,
            "sale_price": ZERO,
            "purchase_price": ZERO,
            "realized_pl": ZERO,
            "wins": ZERO,
            "losses": ZERO,
        }
        bucket[key] = item
    return item


def _aggregate_trade_rows(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    trades_row_extras: dict[int, list[str]],
    aggregate_col_idx: dict[str, int],
) -> tuple[dict[tuple[str, str, str], dict[str, Decimal]], dict[tuple[str, str], dict[str, Decimal]]]:
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]] = {}
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            continue
        field_idx = _trade_indexes(active_trades_header)
        base_len = 2 + len(active_trades_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]
        if data[field_idx.discriminator].strip().lower() != "trade":
            continue

        asset_category = data[field_idx.asset].strip()
        if _is_forex_asset(asset_category) or not _is_supported_asset(asset_category):
            continue

        extras = trades_row_extras.get(row_idx)
        if extras is None:
            continue
        proceeds_eur = _try_parse_decimal(extras[aggregate_col_idx["proceeds"]]) or ZERO
        basis_eur = _try_parse_decimal(extras[aggregate_col_idx["basis"]]) or ZERO
        comm_fee_eur = _try_parse_decimal(extras[aggregate_col_idx["comm"]]) or ZERO
        sale_price_eur = _try_parse_decimal(extras[aggregate_col_idx["sale_price"]]) or ZERO
        purchase_price_eur = _try_parse_decimal(extras[aggregate_col_idx["purchase_price"]]) or ZERO
        realized_eur = _try_parse_decimal(extras[aggregate_col_idx["realized"]]) or ZERO
        wins_eur = realized_eur if realized_eur > 0 else ZERO
        losses_eur = -realized_eur if realized_eur < 0 else ZERO

        symbol_raw = data[field_idx.symbol].strip()
        symbol_upper = symbol_raw.upper()
        currency = data[field_idx.currency].strip().upper()
        instrument, normalized_symbol, _forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if normalized_symbol:
            grouping_symbol = normalized_symbol
        elif instrument is not None:
            grouping_symbol = instrument.symbol
        else:
            grouping_symbol = symbol_upper

        symbol_bucket = _ensure_agg_bucket(symbol_agg_eur, (asset_category, currency, grouping_symbol))
        symbol_bucket["proceeds"] += proceeds_eur
        symbol_bucket["basis"] += basis_eur
        symbol_bucket["comm_fee"] += comm_fee_eur
        symbol_bucket["sale_price"] += sale_price_eur
        symbol_bucket["purchase_price"] += purchase_price_eur
        symbol_bucket["realized_pl"] += realized_eur
        symbol_bucket["wins"] += wins_eur
        symbol_bucket["losses"] += losses_eur

        asset_bucket = _ensure_agg_bucket(asset_agg_eur, (asset_category, currency))
        asset_bucket["proceeds"] += proceeds_eur
        asset_bucket["basis"] += basis_eur
        asset_bucket["comm_fee"] += comm_fee_eur
        asset_bucket["sale_price"] += sale_price_eur
        asset_bucket["purchase_price"] += purchase_price_eur
        asset_bucket["realized_pl"] += realized_eur
        asset_bucket["wins"] += wins_eur
        asset_bucket["losses"] += losses_eur

    return symbol_agg_eur, asset_agg_eur


def _collect_aggregate_rows(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    subtotal_rows_for_output: list[dict[str, object]] = []
    total_rows_for_output: list[dict[str, object]] = []
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Trades" or row[1] not in {"SubTotal", "Total"}:
            continue
        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            continue
        field_idx = _trade_indexes(active_trades_header)
        base_len = 2 + len(active_trades_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]

        asset_category = data[field_idx.asset].strip()
        if _is_forex_asset(asset_category) or not _is_supported_asset(asset_category):
            continue
        currency = data[field_idx.currency].strip().upper()
        symbol_raw = data[field_idx.symbol].strip()
        symbol_upper = symbol_raw.upper()
        subtotal_symbol = symbol_upper
        if row[1] == "SubTotal":
            sub_instrument, sub_normalized_symbol, _sub_forced_reason = _resolve_instrument_for_trade_symbol(
                asset_category=asset_category,
                trade_symbol=symbol_raw,
                listings=listings,
            )
            if sub_normalized_symbol:
                subtotal_symbol = sub_normalized_symbol
            elif sub_instrument is not None:
                subtotal_symbol = sub_instrument.symbol

        container = subtotal_rows_for_output if row[1] == "SubTotal" else total_rows_for_output
        container.append(
            {
                "row_idx": row_idx,
                "asset_category": asset_category,
                "currency": currency,
                "symbol": subtotal_symbol,
            }
        )
    return subtotal_rows_for_output, total_rows_for_output


def _row_distance_to_expected_for_output(
    *,
    entry: dict[str, object],
    expected: dict[str, Decimal],
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]],
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]],
) -> Decimal:
    currency = str(entry["currency"])
    symbol = str(entry.get("symbol", ""))
    asset = str(entry["asset_category"])
    if symbol:
        aggregate = symbol_agg_eur.get((asset, currency, symbol))
    else:
        aggregate = asset_agg_eur.get((asset, currency))
    if aggregate is None:
        return Decimal("999999999")
    return (
        abs(expected["proceeds"] - aggregate["proceeds"])
        + abs(expected["basis"] - aggregate["basis"])
        + abs(expected["comm_fee"] - aggregate["comm_fee"])
        + abs(expected["realized_pl"] - aggregate["realized_pl"])
    )


def _select_subtotal_rows_for_output(
    *,
    subtotal_rows_for_output: list[dict[str, object]],
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]],
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]],
) -> list[dict[str, object]]:
    selected_subtotals_for_output: list[dict[str, object]] = []
    grouped_subtotals_for_output: dict[tuple[str, str], list[dict[str, object]]] = {}
    for entry in subtotal_rows_for_output:
        key = (str(entry["asset_category"]), str(entry["symbol"]))
        grouped_subtotals_for_output.setdefault(key, []).append(entry)

    for (asset_category, symbol), entries in grouped_subtotals_for_output.items():
        non_eur = [item for item in entries if str(item["currency"]).upper() != "EUR"]
        eur = [item for item in entries if str(item["currency"]).upper() == "EUR"]
        selected_subtotals_for_output.extend(non_eur)
        expected_eur = symbol_agg_eur.get((asset_category, "EUR", symbol))
        if expected_eur is not None and eur:
            best_eur = min(
                eur,
                key=lambda item: _row_distance_to_expected_for_output(
                    entry=item,
                    expected=expected_eur,
                    symbol_agg_eur=symbol_agg_eur,
                    asset_agg_eur=asset_agg_eur,
                ),
            )
            selected_subtotals_for_output.append(best_eur)
    return selected_subtotals_for_output


def _select_total_rows_for_output(
    *,
    total_rows_for_output: list[dict[str, object]],
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]],
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]],
) -> list[dict[str, object]]:
    selected_totals_for_output: list[dict[str, object]] = []
    grouped_totals_for_output: dict[str, list[dict[str, object]]] = {}
    for entry in total_rows_for_output:
        grouped_totals_for_output.setdefault(str(entry["asset_category"]), []).append(entry)

    for asset_category, entries in grouped_totals_for_output.items():
        non_eur = [item for item in entries if str(item["currency"]).upper() != "EUR"]
        eur = [item for item in entries if str(item["currency"]).upper() == "EUR"]
        selected_totals_for_output.extend(non_eur)
        expected_eur = asset_agg_eur.get((asset_category, "EUR"))
        if expected_eur is not None and eur:
            best_eur = min(
                eur,
                key=lambda item: _row_distance_to_expected_for_output(
                    entry=item,
                    expected=expected_eur,
                    symbol_agg_eur=symbol_agg_eur,
                    asset_agg_eur=asset_agg_eur,
                ),
            )
            selected_totals_for_output.append(best_eur)
    return selected_totals_for_output


def _write_selected_subtotal_extras(
    *,
    selected_subtotals_for_output: list[dict[str, object]],
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]],
    trades_row_extras: dict[int, list[str]],
) -> None:
    for entry in selected_subtotals_for_output:
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        symbol = str(entry["symbol"])
        agg = symbol_agg_eur.get((asset_category, currency, symbol))
        if agg is None:
            continue
        _set_trade_extras(
            trades_row_extras,
            row_idx=int(entry["row_idx"]),
            values={
                "Comm/Fee (EUR)": _fmt(agg["comm_fee"], quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(agg["proceeds"], quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(agg["basis"], quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(agg["sale_price"], quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(agg["purchase_price"], quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(agg["realized_pl"], quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(agg["wins"], quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(agg["losses"], quant=DECIMAL_EIGHT),
            },
        )


def _write_selected_total_extras(
    *,
    selected_totals_for_output: list[dict[str, object]],
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]],
    trades_row_extras: dict[int, list[str]],
) -> None:
    for entry in selected_totals_for_output:
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        agg = asset_agg_eur.get((asset_category, currency))
        if agg is None:
            continue
        _set_trade_extras(
            trades_row_extras,
            row_idx=int(entry["row_idx"]),
            values={
                "Comm/Fee (EUR)": _fmt(agg["comm_fee"], quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(agg["proceeds"], quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(agg["basis"], quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(agg["sale_price"], quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(agg["purchase_price"], quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(agg["realized_pl"], quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(agg["wins"], quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(agg["losses"], quant=DECIMAL_EIGHT),
            },
        )


def populate_trade_aggregate_extras(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    trades_row_extras: dict[int, list[str]],
) -> None:
    aggregate_col_idx = _aggregate_col_indices()
    symbol_agg_eur, asset_agg_eur = _aggregate_trade_rows(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        trades_row_extras=trades_row_extras,
        aggregate_col_idx=aggregate_col_idx,
    )
    subtotal_rows_for_output, total_rows_for_output = _collect_aggregate_rows(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
    )
    selected_subtotals_for_output = _select_subtotal_rows_for_output(
        subtotal_rows_for_output=subtotal_rows_for_output,
        symbol_agg_eur=symbol_agg_eur,
        asset_agg_eur=asset_agg_eur,
    )
    selected_totals_for_output = _select_total_rows_for_output(
        total_rows_for_output=total_rows_for_output,
        symbol_agg_eur=symbol_agg_eur,
        asset_agg_eur=asset_agg_eur,
    )
    _write_selected_subtotal_extras(
        selected_subtotals_for_output=selected_subtotals_for_output,
        symbol_agg_eur=symbol_agg_eur,
        trades_row_extras=trades_row_extras,
    )
    _write_selected_total_extras(
        selected_totals_for_output=selected_totals_for_output,
        asset_agg_eur=asset_agg_eur,
        trades_row_extras=trades_row_extras,
    )
