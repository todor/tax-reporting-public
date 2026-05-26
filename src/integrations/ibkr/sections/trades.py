from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
import re
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
    FUTURES_ASSET_CATEGORY,
    REVIEW_STATUS_NON_TAXABLE,
    REVIEW_STATUS_NON_TAXABLE_FROM_HERE,
    REVIEW_STATUS_TAXABLE,
    REVIEW_STATUS_TAXABLE_FROM_HERE,
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
    IbkrReportDateFormat,
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
    _is_cfd_asset,
    _is_forex_asset,
    _is_option_asset,
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
    quantity: int | None
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
    quantity: Decimal
    proceeds: Decimal
    commission: Decimal
    trade_basis: Decimal | None
    trade_date: date
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
        quantity=_optional_index(active_header.headers, "Quantity", "Qty"),
        code=_index_for(active_header.headers, "Code", section_name=section_name),
        proceeds=_index_for(active_header.headers, "Proceeds", "Notional Value", section_name=section_name),
        basis=_optional_index(active_header.headers, "Basis", "Cost Basis", "CostBasis"),
        discriminator=_index_for(active_header.headers, "DataDiscriminator", section_name=section_name),
        commission=_optional_index(active_header.headers, "Comm/Fee", "Commission"),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _trade_data(
    row: list[str],
    *,
    active_header: _ActiveHeader,
    row_base_len: dict[int, int],
    row_idx: int,
) -> list[str]:
    row_base_len[row_idx] = 2 + len(active_header.headers)
    padded = row + [""] * (row_base_len[row_idx] - len(row))
    return padded[2 : 2 + len(active_header.headers)]


def _trade_value(
    data: list[str],
    active_header: _ActiveHeader,
    *names: str,
) -> str:
    index = _optional_index(active_header.headers, *names)
    if index is None or index >= len(data):
        return ""
    return data[index].strip()


def _record_unsupported_trade_asset_category(
    summary: AnalysisSummary,
    *,
    row_number: int,
    asset_category: str,
) -> None:
    category = asset_category or "<EMPTY>"
    summary.unsupported_trade_asset_category_rows += 1
    if category in summary.unsupported_trade_asset_categories:
        return
    summary.unsupported_trade_asset_categories.add(category)
    warning = (
        f"row {row_number}: unsupported Trades Asset Category {category!r} was skipped; "
        "unsupported Trades schema was not parsed"
    )
    summary.warnings.append(warning)
    logger.debug("%s", warning)


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
    quantity = (
        _parse_decimal(data[field_idx.quantity], row_number=row_number, field_name="Quantity")
        if field_idx.quantity is not None
        else ZERO
    )
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
        quantity=quantity,
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
        scan_data = _trade_data(
            scan_row,
            active_header=scan_header,
            row_base_len=row_base_len,
            row_idx=scan_idx,
        )
        scan_discriminator = _trade_value(scan_data, scan_header, "DataDiscriminator")
        if scan_discriminator.lower() != "closedlot":
            break
        closedlot_indices.append(scan_idx)
        scan_idx += 1
    return closedlot_indices


def _set_forex_trade_extras(
    *,
    ctx: _TradeRowContext,
    tax_exempt_mode: str,
    tax_year: int,
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
        "Tax Year Scope": "IN_TAX_YEAR" if ctx.trade_date.year == tax_year else "OUTSIDE_TAX_YEAR",
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
    inherited_review_status: str | None,
    inherited_status_applied: bool,
    from_here_directive: str | None,
) -> tuple[str, bool, str]:
    reason = "Forex ignored (not included in Appendix 5/13)"
    review_required = False
    review_notes_parts: list[str] = []
    if from_here_directive is not None:
        summary.review_status_overrides_rows += 1
        review_notes_parts.append(f"Review Status inheritance set to {from_here_directive}")
    elif inherited_status_applied:
        review_notes_parts.append(f"Inherited Forex Review Status applied: {inherited_review_status}")

    if review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
        if not inherited_status_applied and from_here_directive is None:
            summary.review_status_overrides_rows += 1
        summary.forex_non_taxable_ignored_rows += 1
        reason = (
            "Forex ignored: inherited Review Status NON-TAXABLE"
            if inherited_status_applied
            else "Forex ignored: Review Status override NON-TAXABLE"
        )
        if not inherited_status_applied:
            review_notes_parts.append("Review Status override applied")
        return reason, review_required, "; ".join(review_notes_parts)

    summary.forex_review_required_rows += 1
    summary.review_required_rows += 1
    review_required = True

    if review_status_normalized == REVIEW_STATUS_TAXABLE:
        if not inherited_status_applied and from_here_directive is None:
            summary.review_status_overrides_rows += 1
        reason = (
            "Forex ignored: inherited Review Status TAXABLE (taxable forex not supported)"
            if inherited_status_applied
            else "Forex ignored: Review Status override TAXABLE (taxable forex not supported)"
        )
        if not inherited_status_applied:
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


def _effective_forex_review_status(
    *,
    review_status_normalized: str,
    inherited_review_status: str | None,
) -> tuple[str, str | None, bool, str | None]:
    if review_status_normalized == REVIEW_STATUS_TAXABLE_FROM_HERE:
        return REVIEW_STATUS_TAXABLE, REVIEW_STATUS_TAXABLE, False, REVIEW_STATUS_TAXABLE
    if review_status_normalized == REVIEW_STATUS_NON_TAXABLE_FROM_HERE:
        return REVIEW_STATUS_NON_TAXABLE, REVIEW_STATUS_NON_TAXABLE, False, REVIEW_STATUS_NON_TAXABLE
    if review_status_normalized in {REVIEW_STATUS_TAXABLE, REVIEW_STATUS_NON_TAXABLE}:
        return review_status_normalized, inherited_review_status, False, None
    if review_status_normalized == "" and inherited_review_status is not None:
        return inherited_review_status, inherited_review_status, True, None
    return review_status_normalized, inherited_review_status, False, None


def _set_non_closing_trade_extras(
    *,
    ctx: _TradeRowContext,
    tax_exempt_mode: str,
    tax_year: int,
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
        "Tax Year Scope": "IN_TAX_YEAR" if ctx.trade_date.year == tax_year else "OUTSIDE_TAX_YEAR",
        "Tax Treatment Reason": "Non-closing Trade row (informational only)",
        "Review Required": "NO",
    }
    if ctx.trade_basis_eur_from_trade is not None:
        values["Basis (EUR)"] = _fmt(ctx.trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
    _set_trade_extras(row_extras, row_idx=ctx.row_idx, values=values)


def _effective_tax_exemption_market_classification(
    *,
    tax_exempt_mode: str,
    listing_exchange_class: str | None,
    execution_exchange_class: str,
) -> str:
    if listing_exchange_class is None:
        return ""
    if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL:
        return listing_exchange_class
    if listing_exchange_class in {EXCHANGE_CLASS_EU_REGULATED, EXCHANGE_CLASS_UNMAPPED}:
        return execution_exchange_class
    return listing_exchange_class


def _set_futures_trade_extras(
    *,
    row_idx: int,
    trade_date: date,
    tax_exempt_mode: str,
    tax_year: int,
    row_extras: dict[int, list[str]],
) -> None:
    _set_trade_extras(
        row_extras,
        row_idx=row_idx,
        values={
            "Tax Exempt Mode": tax_exempt_mode,
            "Tax Year Scope": "IN_TAX_YEAR" if trade_date.year == tax_year else "OUTSIDE_TAX_YEAR",
            "Appendix Target": APPENDIX_IGNORED,
            "Tax Treatment Reason": "Futures trade not summed directly; Mark-to-Market Performance Summary is used",
            "Review Required": "NO",
        },
    )


def _is_trade_asset_supported_for_parsing(asset_category: str) -> bool:
    return _is_supported_asset(asset_category) or _is_cfd_asset(asset_category) or _is_option_asset(asset_category)


def _is_futures_asset(asset_category: str) -> bool:
    return asset_category.strip() == FUTURES_ASSET_CATEGORY


def _code_tokens(code: str) -> set[str]:
    return {token for token in re.split(r"[^A-Za-z0-9]+", code.upper()) if token}


def _is_option_exercise_assignment_code(code: str) -> bool:
    return bool(_code_tokens(code) & {"A", "EX", "AEX", "MEX", "GEA"})


def _is_option_expiry_code(code: str) -> bool:
    return "EP" in _code_tokens(code)


def _option_trade_detail(ctx: _TradeRowContext, *, reason: str) -> dict[str, str]:
    return {
        "row": str(ctx.row_number),
        "section": "Trades",
        "symbol": ctx.symbol,
        "date": ctx.trade_date.isoformat(),
        "code": ctx.code or "-",
        "reason": reason,
    }


def _sum_closedlot_basis_and_quantity_eur(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    row_base_len: dict[int, int],
    closedlot_indices: list[int],
    row_extras: dict[int, list[str]],
    consumed_closedlots: set[int],
    fx_provider: FxRateProvider,
    fallback_currency: str,
    report_date_format: IbkrReportDateFormat,
) -> tuple[Decimal, Decimal | None, Decimal | None]:
    closedlot_basis_eur_sum = ZERO
    closedlot_abs_quantity_sum: Decimal | None = ZERO
    closedlot_realized_pl_sum: Decimal | None = ZERO
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
        closed_realized_idx = _optional_index(
            closed_header.headers,
            "Realized P/L",
            "Realized P&L",
            "Realized Profit and Loss",
            "RealizedProfitLoss",
        )
        closed_realized_pl: Decimal | None = None
        if closed_realized_idx is not None:
            closed_realized_raw = closed_data[closed_realized_idx].strip()
            if closed_realized_raw != "":
                closed_realized_pl = _parse_decimal(
                    closed_realized_raw,
                    row_number=closed_row_number,
                    field_name="Realized P/L",
                )
        closed_quantity = (
            _parse_decimal(
                closed_data[closed_idxes.quantity],
                row_number=closed_row_number,
                field_name="Quantity",
            )
            if closed_idxes.quantity is not None
            else None
        )
        closed_dt = _parse_closedlot_date(
            closed_data[closed_idxes.date_time],
            row_number=closed_row_number,
            slash_format=report_date_format,
        )
        closed_currency = closed_data[closed_idxes.currency].strip().upper() or fallback_currency
        closed_basis_eur, closed_fx_rate = _to_eur(
            closed_basis,
            closed_currency,
            closed_dt,
            fx_provider,
            row_number=closed_row_number,
        )
        closedlot_basis_eur_sum += closed_basis_eur
        if closedlot_realized_pl_sum is not None:
            if closed_realized_pl is None:
                closedlot_realized_pl_sum = None
            else:
                closedlot_realized_pl_sum += closed_realized_pl
        if closedlot_abs_quantity_sum is not None:
            if closed_quantity is None:
                closedlot_abs_quantity_sum = None
            else:
                closedlot_abs_quantity_sum += abs(closed_quantity)
        consumed_closedlots.add(closed_idx)
        _set_trade_extras(
            row_extras,
            row_idx=closed_idx,
            values={
                "Fx Rate": _fmt(closed_fx_rate, quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(closed_basis_eur, quant=DECIMAL_EIGHT),
            },
        )
    return closedlot_basis_eur_sum, closedlot_abs_quantity_sum, closedlot_realized_pl_sum


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
    report_date_format: IbkrReportDateFormat,
) -> None:
    (
        closedlot_basis_eur_sum,
        closedlot_abs_quantity,
        closedlot_realized_pl_sum,
    ) = _sum_closedlot_basis_and_quantity_eur(
        rows=rows,
        active_headers=active_headers,
        row_base_len=row_base_len,
        closedlot_indices=closedlot_indices,
        row_extras=row_extras,
        consumed_closedlots=consumed_closedlots,
        fx_provider=fx_provider,
        fallback_currency=ctx.currency,
        report_date_format=report_date_format,
    )
    trade_basis_eur = -closedlot_basis_eur_sum
    trade_abs_quantity = abs(ctx.quantity)
    closing_fraction = (
        closedlot_abs_quantity / trade_abs_quantity
        if closedlot_abs_quantity is not None
        and trade_abs_quantity != ZERO
        and closedlot_abs_quantity != trade_abs_quantity
        else Decimal("1")
    )
    closing_proceeds_eur = ctx.proceeds_eur * closing_fraction
    closing_commission_eur = ctx.commission_eur * closing_fraction

    cash_leg_eur = closing_proceeds_eur + closing_commission_eur
    if cash_leg_eur >= ZERO:
        sale_price_component_eur = abs(cash_leg_eur)
        purchase_component_eur = abs(trade_basis_eur)
    else:
        sale_price_component_eur = abs(trade_basis_eur)
        purchase_component_eur = abs(cash_leg_eur)
    pnl_eur = closing_proceeds_eur + trade_basis_eur + closing_commission_eur

    if _is_cfd_asset(ctx.asset_category):
        if closedlot_realized_pl_sum is not None:
            # ClosedLot Date/Time is the opened lot/acquisition date. CFD
            # realized P/L is realized by the closing trade, so convert it
            # using the closing trade currency/date.
            pnl_eur, _ = _to_eur(
                closedlot_realized_pl_sum,
                ctx.currency,
                ctx.trade_date,
                fx_provider,
                row_number=ctx.row_number,
            )
        elif ctx.realized_pl_eur is not None:
            pnl_eur = ctx.realized_pl_eur * closing_fraction
        if pnl_eur >= ZERO:
            sale_price_component_eur = pnl_eur
            purchase_component_eur = ZERO
        else:
            sale_price_component_eur = ZERO
            purchase_component_eur = -pnl_eur

    pnl_win = pnl_eur if pnl_eur > 0 else ZERO
    pnl_loss = -pnl_eur if pnl_eur < 0 else ZERO

    if _is_cfd_asset(ctx.asset_category):
        instrument = listings.get(ctx.symbol)
        normalized_symbol = ctx.symbol
        symbol_for_messages = ctx.symbol
        listing_exchange = ""
        listing_exchange_raw = ""
        listing_exchange_class = None
        symbol_is_eu_listed = False
        appendix_target = APPENDIX_5
        reason = "CFD derivative instrument -> Appendix 5 code 508"
        review_required = False
    elif _is_option_asset(ctx.asset_category):
        instrument = listings.get(ctx.symbol)
        normalized_symbol = ctx.symbol
        symbol_for_messages = ctx.symbol
        listing_exchange = ""
        listing_exchange_raw = ""
        listing_exchange_class = None
        symbol_is_eu_listed = False
        appendix_target = APPENDIX_5
        reason = "Equity/index option -> Appendix 5 code 508"
        review_required = False
    else:
        instrument, normalized_symbol, forced_review_reason = _resolve_instrument_for_trade_symbol(
            asset_category=ctx.asset_category,
            trade_symbol=ctx.symbol_raw,
            listings=listings,
        )
        symbol_for_messages = normalized_symbol or ctx.symbol
        missing_symbol_mapping = instrument is None
        listing_exchange = instrument.listing_exchange_normalized if instrument is not None else ""
        listing_exchange_raw = instrument.listing_exchange if instrument is not None else ""
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
            listing_for_warning = listing_exchange_raw or "<missing from Financial Instrument Information>"
            mapped_classification = listing_exchange_class or "MISSING"
            summary.warnings.append(
                f"row {ctx.row_number}: {reason} "
                f"(symbol={symbol_for_messages}, listing_exchange={listing_for_warning}, "
                f"mapped_classification={mapped_classification}, "
                f"execution_exchange={ctx.execution_exchange_norm or '<EMPTY>'})"
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
        if _is_cfd_asset(ctx.asset_category):
            summary.cfd_trade_rows += 1
        if _is_option_asset(ctx.asset_category):
            summary.option_closedlot_rows += len(closedlot_indices)
            if closedlot_realized_pl_sum is not None:
                currency = ctx.currency or "-"
                summary.option_closedlot_realized_pl_by_currency[currency] = (
                    summary.option_closedlot_realized_pl_by_currency.get(currency, ZERO)
                    + closedlot_realized_pl_sum
                )
        if review_required:
            summary.review_entries.append(
                ReviewEntry(
                    row_number=ctx.row_number,
                    symbol=symbol_for_messages,
                    trade_date=ctx.trade_date.isoformat(),
                    listing_exchange=listing_exchange or "<MISSING>",
                    listing_exchange_raw=listing_exchange_raw or "<missing from Financial Instrument Information>",
                    mapped_listing_classification=listing_exchange_class or "MISSING",
                    execution_exchange=ctx.execution_exchange_norm or "<EMPTY>",
                    reason=reason,
                    proceeds_eur=closing_proceeds_eur,
                    basis_eur=trade_basis_eur,
                    pnl_eur=pnl_eur,
                )
            )
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE and appendix_target == APPENDIX_REVIEW:
            summary.review_rows += 1
            summary.review_exchanges.add(ctx.execution_exchange_norm or "<EMPTY>")
            _sum_bucket(summary.review, sale_price_component_eur, purchase_component_eur, pnl_eur)
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
            "Comm/Fee (EUR)": _fmt(closing_commission_eur, quant=DECIMAL_EIGHT),
            "Proceeds (EUR)": _fmt(closing_proceeds_eur, quant=DECIMAL_EIGHT),
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
            "Tax Exemption Market Classification": _effective_tax_exemption_market_classification(
                tax_exempt_mode=tax_exempt_mode,
                listing_exchange_class=listing_exchange_class,
                execution_exchange_class=ctx.execution_exchange_class,
            ),
            "Tax Exempt Mode": tax_exempt_mode,
            "Tax Year Scope": "IN_TAX_YEAR" if in_tax_year else "OUTSIDE_TAX_YEAR",
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
    report_date_format: IbkrReportDateFormat,
) -> TradesSectionResult:
    row_extras: dict[int, list[str]] = {}
    row_base_len: dict[int, int] = {}
    consumed_closedlots: set[int] = set()
    current_trades_header: _ActiveHeader | None = None
    inherited_forex_review_status: str | None = None
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

        found_trade_section_data = True
        summary.trades_data_rows_total += 1
        data = _trade_data(
            row,
            active_header=active_trades_header,
            row_base_len=row_base_len,
            row_idx=row_idx,
        )
        asset_category = _trade_value(data, active_trades_header, "Asset Category")
        lowered = _trade_value(data, active_trades_header, "DataDiscriminator").lower()
        if lowered == "trade":
            summary.trade_discriminator_rows += 1
        elif lowered == "closedlot":
            summary.closedlot_discriminator_rows += 1
        elif lowered == "order":
            summary.order_discriminator_rows += 1

        if row_idx in consumed_closedlots:
            continue

        if _is_futures_asset(asset_category):
            if lowered == "trade":
                summary.futures_trade_rows += 1
                summary.appendix_5.rows += 1
                field_idx = _trade_indexes(active_trades_header)
                trade_date = _parse_trade_datetime(data[field_idx.date_time], row_number=row_number).date()
                _set_futures_trade_extras(
                    row_idx=row_idx,
                    trade_date=trade_date,
                    tax_exempt_mode=tax_exempt_mode,
                    tax_year=tax_year,
                    row_extras=row_extras,
                )
            continue

        if not _is_forex_asset(asset_category) and not _is_trade_asset_supported_for_parsing(asset_category):
            _record_unsupported_trade_asset_category(
                summary,
                row_number=row_number,
                asset_category=asset_category,
            )
            continue

        field_idx = _trade_indexes(active_trades_header)
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
        if _is_option_asset(ctx.asset_category):
            summary.option_trade_rows += 1

        if _is_forex_asset(ctx.asset_category):
            summary.forex_ignored_rows += 1
            summary.forex_ignored_abs_proceeds_eur += abs(ctx.proceeds_eur)
            review_status_raw = (
                ctx.data[ctx.field_idx.review_status].strip()
                if ctx.field_idx.review_status is not None
                else ""
            )
            review_status_normalized = _normalize_review_status(review_status_raw)
            (
                effective_review_status,
                inherited_forex_review_status,
                inherited_status_applied,
                from_here_directive,
            ) = _effective_forex_review_status(
                review_status_normalized=review_status_normalized,
                inherited_review_status=inherited_forex_review_status,
            )
            reason, review_required, review_notes = _apply_forex_review_status(
                summary,
                row_number=ctx.row_number,
                symbol=ctx.symbol,
                execution_exchange_norm=ctx.execution_exchange_norm,
                review_status_normalized=effective_review_status,
                inherited_review_status=inherited_forex_review_status,
                inherited_status_applied=inherited_status_applied,
                from_here_directive=from_here_directive,
            )
            for closed_idx in closedlot_indices:
                consumed_closedlots.add(closed_idx)
            _set_forex_trade_extras(
                ctx=ctx,
                tax_exempt_mode=tax_exempt_mode,
                tax_year=tax_year,
                row_extras=row_extras,
                tax_treatment_reason=reason,
                review_required=review_required,
                review_notes=review_notes,
            )
            continue

        option_exercise_assignment = (
            _is_option_asset(ctx.asset_category)
            and _is_option_exercise_assignment_code(ctx.code)
        )
        option_realized_close = _is_option_asset(ctx.asset_category) and bool(closedlot_indices)
        if not ctx.is_closing_trade and not option_realized_close:
            if option_exercise_assignment:
                summary.option_exercise_assignment_rows += 1
                summary.option_exercise_assignment_without_closedlot_rows += 1
                summary.option_exercise_assignment_details.append(
                    _option_trade_detail(
                        ctx,
                        reason="exercise/assignment code without attached ClosedLot; no standalone option taxable event",
                    )
                )
            elif _is_option_asset(ctx.asset_category) and _is_option_expiry_code(ctx.code):
                summary.option_unhandled_trade_rows += 1
                summary.option_unhandled_trade_details.append(
                    _option_trade_detail(ctx, reason="expiry-style option row without attached ClosedLot")
                )
            summary.ignored_non_closing_trade_rows += 1
            _set_non_closing_trade_extras(
                ctx=ctx,
                tax_exempt_mode=tax_exempt_mode,
                tax_year=tax_year,
                row_extras=row_extras,
            )
            continue

        summary.closing_trade_candidates += 1

        if not closedlot_indices:
            if option_exercise_assignment:
                summary.option_exercise_assignment_rows += 1
                summary.option_exercise_assignment_without_closedlot_rows += 1
                summary.option_exercise_assignment_details.append(
                    _option_trade_detail(
                        ctx,
                        reason="exercise/assignment code without attached ClosedLot; no standalone option taxable event",
                    )
                )
                summary.ignored_non_closing_trade_rows += 1
                _set_non_closing_trade_extras(
                    ctx=ctx,
                    tax_exempt_mode=tax_exempt_mode,
                    tax_year=tax_year,
                    row_extras=row_extras,
                )
                continue
            if _is_option_asset(ctx.asset_category):
                summary.option_unhandled_trade_rows += 1
                summary.option_unhandled_trade_details.append(
                    _option_trade_detail(ctx, reason="closing option row without attached ClosedLot")
                )
                summary.ignored_non_closing_trade_rows += 1
                _set_non_closing_trade_extras(
                    ctx=ctx,
                    tax_exempt_mode=tax_exempt_mode,
                    tax_year=tax_year,
                    row_extras=row_extras,
                )
                continue
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
            report_date_format=report_date_format,
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
        row_base_len: dict[int, int] = {}
        data = _trade_data(
            row,
            active_header=active_trades_header,
            row_base_len=row_base_len,
            row_idx=row_idx,
        )
        if _trade_value(data, active_trades_header, "DataDiscriminator").lower() != "trade":
            continue

        asset_category = _trade_value(data, active_trades_header, "Asset Category")
        if _is_forex_asset(asset_category) or not _is_trade_asset_supported_for_parsing(asset_category):
            continue

        field_idx = _trade_indexes(active_trades_header)
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
        row_base_len: dict[int, int] = {}
        data = _trade_data(
            row,
            active_header=active_trades_header,
            row_base_len=row_base_len,
            row_idx=row_idx,
        )

        asset_category = _trade_value(data, active_trades_header, "Asset Category")
        if _is_forex_asset(asset_category) or not _is_trade_asset_supported_for_parsing(asset_category):
            continue
        field_idx = _trade_indexes(active_trades_header)
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
