from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ..constants import ADDED_TRADES_COLUMNS, DECIMAL_EIGHT, ZERO
from ..models import InstrumentListing, _ActiveHeader, _SanityCheckResult, _SanityFailure
from ..shared import _code_has_closing_token, _fmt, _optional_index, _try_parse_decimal
from .instruments import (
    _is_forex_asset,
    _is_supported_asset,
    _resolve_instrument_for_trade_symbol,
)
from .trades import _trade_indexes


@dataclass(slots=True)
class _SanityState:
    failures: list[_SanityFailure] = field(default_factory=list)
    row_failure_reasons: dict[int, list[str]] = field(default_factory=dict)
    sanity_extras_by_row: dict[int, dict[str, str]] = field(default_factory=dict)
    sanity_row_kind_by_row: dict[int, str] = field(default_factory=dict)
    checked_trade_rows: int = 0
    checked_closedlots: int = 0
    checked_subtotals: int = 0
    checked_totals: int = 0
    forex_ignored_rows: int = 0
    symbol_agg: dict[tuple[str, str, str], dict[str, Decimal]] = field(default_factory=dict)
    asset_agg: dict[tuple[str, str], dict[str, Decimal]] = field(default_factory=dict)
    subtotal_rows: list[dict[str, object]] = field(default_factory=list)
    total_rows: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class _TradeSanityMetrics:
    basis_for_checks: Decimal
    realized_for_checks: Decimal
    sale_price_for_checks: Decimal
    purchase_price_for_checks: Decimal
    wins: Decimal
    losses: Decimal
    is_closing_trade: bool


def _add_failure(
    state: _SanityState,
    *,
    check_type: str,
    row_number: int | None,
    row_kind: str,
    asset_category: str,
    symbol: str,
    field_name: str,
    expected: Decimal | str,
    actual: Decimal | str,
    details: str,
) -> None:
    expected_str = _fmt(expected) if isinstance(expected, Decimal) else str(expected)
    actual_str = _fmt(actual) if isinstance(actual, Decimal) else str(actual)
    if isinstance(expected, Decimal) and isinstance(actual, Decimal):
        diff_str = _fmt(expected - actual)
    else:
        diff_str = "-"

    failure = _SanityFailure(
        check_type=check_type,
        row_number=row_number,
        row_kind=row_kind,
        asset_category=asset_category,
        symbol=symbol,
        field_name=field_name,
        expected=expected_str,
        actual=actual_str,
        difference=diff_str,
        details=details,
    )
    state.failures.append(failure)
    if row_number is not None:
        state.row_failure_reasons.setdefault(row_number, []).append(failure.to_message())


def _ensure_bucket(bucket: dict, key: tuple) -> dict[str, Decimal]:
    if key not in bucket:
        bucket[key] = {
            "proceeds": ZERO,
            "basis": ZERO,
            "comm_fee": ZERO,
            "sale_price": ZERO,
            "purchase_price": ZERO,
            "realized_pl": ZERO,
            "wins": ZERO,
            "losses": ZERO,
        }
    return bucket[key]


def _set_sanity_extras(state: _SanityState, row_idx: int, row_kind: str, values: dict[str, str]) -> None:
    existing = state.sanity_extras_by_row.get(row_idx, {})
    existing.update(values)
    state.sanity_extras_by_row[row_idx] = existing
    state.sanity_row_kind_by_row[row_idx] = row_kind


def _collect_attached_closedlots(
    state: _SanityState,
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    row_idx: int,
) -> tuple[Decimal, int]:
    closedlot_sum = ZERO
    closedlot_count_for_trade = 0
    scan_idx = row_idx + 1
    while scan_idx < len(rows):
        scan_row = rows[scan_idx]
        if len(scan_row) < 2 or scan_row[0] != "Trades" or scan_row[1] != "Data":
            break
        scan_header = active_headers.get(scan_idx)
        if scan_header is None:
            break
        scan_idxes = _trade_indexes(scan_header)
        scan_padded = scan_row + [""] * (2 + len(scan_header.headers) - len(scan_row))
        scan_data = scan_padded[2 : 2 + len(scan_header.headers)]
        scan_discriminator = scan_data[scan_idxes.discriminator].strip().lower()
        if scan_discriminator != "closedlot":
            break
        closedlot_basis = (
            _try_parse_decimal(scan_data[scan_idxes.basis])
            if scan_idxes.basis is not None
            else None
        )
        if closedlot_basis is not None:
            closedlot_sum += closedlot_basis
        closedlot_count_for_trade += 1
        state.checked_closedlots += 1
        _set_sanity_extras(
            state,
            scan_idx,
            "ClosedLot",
            {
                "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(closedlot_basis or ZERO, quant=DECIMAL_EIGHT),
            },
        )
        scan_idx += 1

    return closedlot_sum, closedlot_count_for_trade


def _compute_trade_metrics(
    state: _SanityState,
    *,
    row_number: int,
    asset_category: str,
    grouping_symbol: str,
    code: str,
    proceeds: Decimal,
    commission: Decimal,
    trade_basis: Decimal | None,
    realized_pl: Decimal | None,
    closedlot_sum: Decimal,
    closedlot_count_for_trade: int,
    tolerance: Decimal,
) -> _TradeSanityMetrics:
    is_closing_trade = _code_has_closing_token(code)
    expected_trade_basis = -closedlot_sum
    if (
        is_closing_trade
        and trade_basis is not None
        and closedlot_count_for_trade > 0
        and trade_basis != expected_trade_basis
    ):
        _add_failure(
            state,
            check_type="BASIS_SIGN_MISMATCH",
            row_number=row_number,
            row_kind="Trade",
            asset_category=asset_category,
            symbol=grouping_symbol,
            field_name="Basis",
            expected=expected_trade_basis,
            actual=trade_basis,
            details="Trade.Basis must equal -sum(attached ClosedLot.Basis)",
        )

    if is_closing_trade and closedlot_count_for_trade > 0:
        basis_for_checks = expected_trade_basis
    else:
        basis_for_checks = trade_basis if trade_basis is not None else ZERO

    if is_closing_trade:
        expected_realized = proceeds + basis_for_checks + commission
        cash_leg = proceeds + commission
        if cash_leg >= ZERO:
            sale_price_for_checks = abs(cash_leg)
            purchase_price_for_checks = abs(basis_for_checks)
        else:
            sale_price_for_checks = abs(basis_for_checks)
            purchase_price_for_checks = abs(cash_leg)
        if realized_pl is not None and abs(expected_realized - realized_pl) > tolerance:
            _add_failure(
                state,
                check_type="ROW_PNL_IDENTITY_MISMATCH",
                row_number=row_number,
                row_kind="Trade",
                asset_category=asset_category,
                symbol=grouping_symbol,
                field_name="Realized P/L",
                expected=expected_realized,
                actual=realized_pl,
                details="Expected Proceeds + Basis + Comm/Fee ~= Realized P/L",
            )
        realized_for_checks = realized_pl if realized_pl is not None else expected_realized
    else:
        expected_realized = ZERO
        sale_price_for_checks = ZERO
        purchase_price_for_checks = ZERO
        if realized_pl is not None and abs(realized_pl) > tolerance:
            _add_failure(
                state,
                check_type="ENTRY_REALIZED_NONZERO",
                row_number=row_number,
                row_kind="Trade",
                asset_category=asset_category,
                symbol=grouping_symbol,
                field_name="Realized P/L",
                expected=expected_realized,
                actual=realized_pl,
                details="Entry Trade rows are expected to have zero realized P/L",
            )
        realized_for_checks = ZERO

    wins = realized_for_checks if realized_for_checks > 0 else ZERO
    losses = -realized_for_checks if realized_for_checks < 0 else ZERO
    if wins - losses != realized_for_checks:
        _add_failure(
            state,
            check_type="WINS_LOSSES_MISMATCH",
            row_number=row_number,
            row_kind="Trade",
            asset_category=asset_category,
            symbol=grouping_symbol,
            field_name="Realized P/L",
            expected=realized_for_checks,
            actual=wins - losses,
            details="Wins minus losses must equal realized P/L",
        )

    return _TradeSanityMetrics(
        basis_for_checks=basis_for_checks,
        realized_for_checks=realized_for_checks,
        sale_price_for_checks=sale_price_for_checks,
        purchase_price_for_checks=purchase_price_for_checks,
        wins=wins,
        losses=losses,
        is_closing_trade=is_closing_trade,
    )


def _accumulate_trade_metrics(
    state: _SanityState,
    *,
    asset_category: str,
    currency: str,
    grouping_symbol: str,
    proceeds: Decimal,
    commission: Decimal,
    metrics: _TradeSanityMetrics,
) -> None:
    symbol_bucket = _ensure_bucket(state.symbol_agg, (asset_category, currency, grouping_symbol))
    symbol_bucket["proceeds"] += proceeds
    symbol_bucket["basis"] += metrics.basis_for_checks
    symbol_bucket["comm_fee"] += commission
    symbol_bucket["sale_price"] += metrics.sale_price_for_checks
    symbol_bucket["purchase_price"] += metrics.purchase_price_for_checks
    symbol_bucket["realized_pl"] += metrics.realized_for_checks
    symbol_bucket["wins"] += metrics.wins
    symbol_bucket["losses"] += metrics.losses

    asset_bucket = _ensure_bucket(state.asset_agg, (asset_category, currency))
    asset_bucket["proceeds"] += proceeds
    asset_bucket["basis"] += metrics.basis_for_checks
    asset_bucket["comm_fee"] += commission
    asset_bucket["sale_price"] += metrics.sale_price_for_checks
    asset_bucket["purchase_price"] += metrics.purchase_price_for_checks
    asset_bucket["realized_pl"] += metrics.realized_for_checks
    asset_bucket["wins"] += metrics.wins
    asset_bucket["losses"] += metrics.losses


def _process_trade_data_row(
    state: _SanityState,
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    row_idx: int,
    row_number: int,
    asset_category: str,
    currency: str,
    symbol_raw: str,
    symbol_upper: str,
    code: str,
    data: list[str],
    field_idx,
    tolerance: Decimal,
) -> None:
    if _is_forex_asset(asset_category):
        state.forex_ignored_rows += 1
        return
    if not _is_supported_asset(asset_category):
        return

    proceeds = _try_parse_decimal(data[field_idx.proceeds]) or ZERO
    commission = (
        _try_parse_decimal(data[field_idx.commission]) or ZERO
        if field_idx.commission is not None
        else ZERO
    )
    active_header = active_headers[row_idx]
    realized_idx = _optional_index(
        active_header.headers,
        "Realized P/L",
        "Realized P&L",
        "Realized Profit and Loss",
        "RealizedProfitLoss",
    )
    realized_pl = _try_parse_decimal(data[realized_idx]) if realized_idx is not None else None
    trade_basis = _try_parse_decimal(data[field_idx.basis]) if field_idx.basis is not None else None

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

    closedlot_sum, closedlot_count_for_trade = _collect_attached_closedlots(
        state,
        rows=rows,
        active_headers=active_headers,
        row_idx=row_idx,
    )
    metrics = _compute_trade_metrics(
        state,
        row_number=row_number,
        asset_category=asset_category,
        grouping_symbol=grouping_symbol,
        code=code,
        proceeds=proceeds,
        commission=commission,
        trade_basis=trade_basis,
        realized_pl=realized_pl,
        closedlot_sum=closedlot_sum,
        closedlot_count_for_trade=closedlot_count_for_trade,
        tolerance=tolerance,
    )
    _accumulate_trade_metrics(
        state,
        asset_category=asset_category,
        currency=currency,
        grouping_symbol=grouping_symbol,
        proceeds=proceeds,
        commission=commission,
        metrics=metrics,
    )

    state.checked_trade_rows += 1
    _set_sanity_extras(
        state,
        row_idx,
        "Trade",
        {
            "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
            "Comm/Fee (EUR)": _fmt(commission, quant=DECIMAL_EIGHT),
            "Proceeds (EUR)": _fmt(proceeds, quant=DECIMAL_EIGHT),
            "Basis (EUR)": _fmt(metrics.basis_for_checks, quant=DECIMAL_EIGHT),
            "Sale Price (EUR)": _fmt(metrics.sale_price_for_checks, quant=DECIMAL_EIGHT)
            if metrics.is_closing_trade
            else "",
            "Purchase Price (EUR)": _fmt(metrics.purchase_price_for_checks, quant=DECIMAL_EIGHT)
            if metrics.is_closing_trade
            else "",
            "Realized P/L (EUR)": _fmt(metrics.realized_for_checks, quant=DECIMAL_EIGHT),
            "Realized P/L Wins (EUR)": _fmt(metrics.wins, quant=DECIMAL_EIGHT),
            "Realized P/L Losses (EUR)": _fmt(metrics.losses, quant=DECIMAL_EIGHT),
            "Normalized Symbol": normalized_symbol,
        },
    )


def _record_subtotal_total_candidate(
    state: _SanityState,
    *,
    row_type: str,
    row_number: int,
    asset_category: str,
    currency: str,
    symbol_raw: str,
    symbol_upper: str,
    data: list[str],
    active_header: _ActiveHeader,
    field_idx,
    listings: dict[str, InstrumentListing],
) -> None:
    if _is_forex_asset(asset_category):
        return
    if not _is_supported_asset(asset_category):
        return

    subtotal_symbol = symbol_upper
    if row_type == "SubTotal":
        sub_instrument, sub_normalized_symbol, _sub_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if sub_normalized_symbol:
            subtotal_symbol = sub_normalized_symbol
        elif sub_instrument is not None:
            subtotal_symbol = sub_instrument.symbol

    proceeds_val = _try_parse_decimal(data[field_idx.proceeds])
    comm_idx = _optional_index(active_header.headers, "Comm/Fee", "Comm in EUR", "Commission")
    comm_val = _try_parse_decimal(data[comm_idx]) if comm_idx is not None else None
    basis_val = _try_parse_decimal(data[field_idx.basis]) if field_idx.basis is not None else None
    realized_idx = _optional_index(
        active_header.headers,
        "Realized P/L",
        "Realized P&L",
        "Realized Profit and Loss",
        "RealizedProfitLoss",
    )
    realized_val = _try_parse_decimal(data[realized_idx]) if realized_idx is not None else None

    entry = {
        "row_number": row_number,
        "asset_category": asset_category,
        "currency": currency,
        "symbol": subtotal_symbol,
        "proceeds": proceeds_val,
        "basis": basis_val,
        "comm_fee": comm_val,
        "realized_pl": realized_val,
        "row_kind": row_type,
    }
    if row_type == "SubTotal":
        state.subtotal_rows.append(entry)
    else:
        state.total_rows.append(entry)


def _collect_trade_and_aggregate_data(
    state: _SanityState,
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    tolerance: Decimal,
) -> None:
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades":
            continue
        row_type = row[1]
        if row_type == "Header":
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            _add_failure(
                state,
                check_type="ROW_FIELD_MISMATCH",
                row_number=row_number,
                row_kind=row_type,
                asset_category="",
                symbol="",
                field_name="active_header",
                expected="Trades header available",
                actual="missing",
                details="Trades row cannot be interpreted without active header",
            )
            continue

        field_idx = _trade_indexes(active_header)
        padded = row + [""] * (2 + len(active_header.headers) - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        asset_category = data[field_idx.asset].strip()
        currency = data[field_idx.currency].strip().upper()
        symbol_raw = data[field_idx.symbol].strip()
        symbol_upper = symbol_raw.upper()
        code = data[field_idx.code].strip()
        discriminator = data[field_idx.discriminator].strip().lower()

        if row_type == "Data" and discriminator == "trade":
            _process_trade_data_row(
                state,
                rows=rows,
                active_headers=active_headers,
                listings=listings,
                row_idx=row_idx,
                row_number=row_number,
                asset_category=asset_category,
                currency=currency,
                symbol_raw=symbol_raw,
                symbol_upper=symbol_upper,
                code=code,
                data=data,
                field_idx=field_idx,
                tolerance=tolerance,
            )
            continue

        if row_type in {"SubTotal", "Total"}:
            _record_subtotal_total_candidate(
                state,
                row_type=row_type,
                row_number=row_number,
                asset_category=asset_category,
                currency=currency,
                symbol_raw=symbol_raw,
                symbol_upper=symbol_upper,
                data=data,
                active_header=active_header,
                field_idx=field_idx,
                listings=listings,
            )


def _row_distance_to_expected(entry: dict[str, object], expected: dict[str, Decimal]) -> Decimal:
    distance = ZERO
    for field_name, agg_key in (
        ("proceeds", "proceeds"),
        ("basis", "basis"),
        ("comm_fee", "comm_fee"),
        ("realized_pl", "realized_pl"),
    ):
        row_val = entry[field_name]
        if isinstance(row_val, Decimal):
            distance += abs(expected[agg_key] - row_val)
    return distance


def _select_subtotals(state: _SanityState) -> list[dict[str, object]]:
    selected_subtotals: list[dict[str, object]] = []
    subtotals_by_group: dict[tuple[str, str], list[dict[str, object]]] = {}
    for entry in state.subtotal_rows:
        key = (str(entry["asset_category"]), str(entry["symbol"]))
        subtotals_by_group.setdefault(key, []).append(entry)

    for (asset_category, symbol), group_entries in subtotals_by_group.items():
        non_eur_rows = [item for item in group_entries if str(item["currency"]).upper() != "EUR"]
        eur_rows = [item for item in group_entries if str(item["currency"]).upper() == "EUR"]

        selected_subtotals.extend(non_eur_rows)

        eur_expected = state.symbol_agg.get((asset_category, "EUR", symbol))
        if eur_expected is not None and eur_rows:
            best_eur_row = min(
                eur_rows,
                key=lambda item: _row_distance_to_expected(item, eur_expected),
            )
            selected_subtotals.append(best_eur_row)

    subtotal_seen: dict[tuple[str, str, str], int] = {}
    for entry in selected_subtotals:
        key = (str(entry["asset_category"]), str(entry["currency"]), str(entry["symbol"]))
        subtotal_seen[key] = subtotal_seen.get(key, 0) + 1
    for (asset_category, currency, symbol), count in subtotal_seen.items():
        if count > 1:
            _add_failure(
                state,
                check_type="DUPLICATE_SUBTOTAL",
                row_number=None,
                row_kind="SubTotal",
                asset_category=asset_category,
                symbol=symbol,
                field_name="grouping key",
                expected="single subtotal row",
                actual=str(count),
                details=f"Duplicate SubTotal rows detected for currency={currency}",
            )
    return selected_subtotals


def _select_totals(state: _SanityState) -> list[dict[str, object]]:
    selected_totals: list[dict[str, object]] = []
    totals_by_asset: dict[str, list[dict[str, object]]] = {}
    for entry in state.total_rows:
        totals_by_asset.setdefault(str(entry["asset_category"]), []).append(entry)

    for asset_category, group_entries in totals_by_asset.items():
        non_eur_rows = [item for item in group_entries if str(item["currency"]).upper() != "EUR"]
        eur_rows = [item for item in group_entries if str(item["currency"]).upper() == "EUR"]

        selected_totals.extend(non_eur_rows)

        eur_expected = state.asset_agg.get((asset_category, "EUR"))
        if eur_expected is not None and eur_rows:
            best_eur_row = min(
                eur_rows,
                key=lambda item: _row_distance_to_expected(item, eur_expected),
            )
            selected_totals.append(best_eur_row)

    total_seen: dict[tuple[str, str], int] = {}
    for entry in selected_totals:
        key = (str(entry["asset_category"]), str(entry["currency"]))
        total_seen[key] = total_seen.get(key, 0) + 1
    for (asset_category, currency), count in total_seen.items():
        if count > 1:
            _add_failure(
                state,
                check_type="DUPLICATE_TOTAL",
                row_number=None,
                row_kind="Total",
                asset_category=asset_category,
                symbol="",
                field_name="grouping key",
                expected="single total row",
                actual=str(count),
                details=f"Duplicate Total rows detected for currency={currency}",
            )
    return selected_totals


def _validate_subtotals(
    state: _SanityState,
    *,
    selected_subtotals: list[dict[str, object]],
    tolerance: Decimal,
) -> None:
    for entry in selected_subtotals:
        row_number = int(entry["row_number"])
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        symbol = str(entry["symbol"])
        agg = state.symbol_agg.get((asset_category, currency, symbol))
        if agg is None:
            _add_failure(
                state,
                check_type="MISSING_SUBTOTAL",
                row_number=row_number,
                row_kind="SubTotal",
                asset_category=asset_category,
                symbol=symbol,
                field_name="aggregate",
                expected="trade aggregates for symbol exist",
                actual="missing",
                details="No matching Trade rows found for this subtotal row",
            )
            continue

        state.checked_subtotals += 1
        for field_name, agg_key in (
            ("Proceeds", "proceeds"),
            ("Basis", "basis"),
            ("Comm/Fee", "comm_fee"),
            ("Realized P/L", "realized_pl"),
        ):
            row_value = entry[agg_key]
            if not isinstance(row_value, Decimal):
                continue
            expected = agg[agg_key]
            if abs(expected - row_value) > tolerance:
                _add_failure(
                    state,
                    check_type="SUBTOTAL_MISMATCH",
                    row_number=row_number,
                    row_kind="SubTotal",
                    asset_category=asset_category,
                    symbol=symbol,
                    field_name=field_name,
                    expected=expected,
                    actual=row_value,
                    details="Subtotal does not match sum of Trade rows",
                )

        if agg["wins"] - agg["losses"] != agg["realized_pl"]:
            _add_failure(
                state,
                check_type="WINS_LOSSES_MISMATCH",
                row_number=row_number,
                row_kind="SubTotal",
                asset_category=asset_category,
                symbol=symbol,
                field_name="Realized P/L",
                expected=agg["realized_pl"],
                actual=agg["wins"] - agg["losses"],
                details="Wins minus losses must equal realized P/L aggregate",
            )

        _set_sanity_extras(
            state,
            row_number - 1,
            "SubTotal",
            {
                "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
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


def _validate_totals(
    state: _SanityState,
    *,
    selected_totals: list[dict[str, object]],
    tolerance: Decimal,
) -> None:
    for entry in selected_totals:
        row_number = int(entry["row_number"])
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        agg = state.asset_agg.get((asset_category, currency))
        if agg is None:
            _add_failure(
                state,
                check_type="MISSING_TOTAL",
                row_number=row_number,
                row_kind="Total",
                asset_category=asset_category,
                symbol="",
                field_name="aggregate",
                expected="trade aggregates for asset category exist",
                actual="missing",
                details="No matching Trade rows found for this total row",
            )
            continue

        state.checked_totals += 1
        for field_name, agg_key in (
            ("Proceeds", "proceeds"),
            ("Basis", "basis"),
            ("Comm/Fee", "comm_fee"),
            ("Realized P/L", "realized_pl"),
        ):
            row_value = entry[agg_key]
            if not isinstance(row_value, Decimal):
                continue
            expected = agg[agg_key]
            if abs(expected - row_value) > tolerance:
                _add_failure(
                    state,
                    check_type="TOTAL_MISMATCH",
                    row_number=row_number,
                    row_kind="Total",
                    asset_category=asset_category,
                    symbol="",
                    field_name=field_name,
                    expected=expected,
                    actual=row_value,
                    details="Total does not match sum of Trade rows",
                )

        if agg["wins"] - agg["losses"] != agg["realized_pl"]:
            _add_failure(
                state,
                check_type="WINS_LOSSES_MISMATCH",
                row_number=row_number,
                row_kind="Total",
                asset_category=asset_category,
                symbol="",
                field_name="Realized P/L",
                expected=agg["realized_pl"],
                actual=agg["wins"] - agg["losses"],
                details="Wins minus losses must equal realized P/L aggregate",
            )

        _set_sanity_extras(
            state,
            row_number - 1,
            "Total",
            {
                "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
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


def _build_sanity_output_rows(
    state: _SanityState,
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
) -> list[list[str]]:
    debug_columns = [
        "DEBUG_SANITY_STATUS",
        "DEBUG_SANITY_ROW_KIND",
        "DEBUG_SANITY_FAILURES",
    ]
    sanity_output_rows: list[list[str]] = []
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades":
            sanity_output_rows.append(row)
            continue

        row_type = row[1]
        if row_type == "Header":
            sanity_output_rows.append(row + ADDED_TRADES_COLUMNS + debug_columns)
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            sanity_output_rows.append(row)
            continue

        base_len = 2 + len(active_header.headers)
        padded = row + [""] * (base_len - len(row))
        extras_map = state.sanity_extras_by_row.get(row_idx, {})
        extras = [extras_map.get(col, "") for col in ADDED_TRADES_COLUMNS]
        failures_for_row = state.row_failure_reasons.get(row_number, [])
        if failures_for_row:
            debug_status = "FAIL"
        elif row_idx in state.sanity_extras_by_row:
            debug_status = "PASS"
        else:
            debug_status = ""
        debug_kind = state.sanity_row_kind_by_row.get(row_idx, row_type)
        sanity_output_rows.append(
            padded
            + extras
            + [
                debug_status,
                debug_kind,
                " | ".join(failures_for_row),
            ]
        )

    return sanity_output_rows


def _write_sanity_report(
    state: _SanityState,
    *,
    report_path: Path,
    debug_csv_path: Path,
) -> None:
    report_data = {
        "passed": len(state.failures) == 0,
        "checked_closing_trades": state.checked_trade_rows,
        "checked_closedlots": state.checked_closedlots,
        "checked_subtotals": state.checked_subtotals,
        "checked_totals": state.checked_totals,
        "forex_ignored_rows": state.forex_ignored_rows,
        "debug_csv_path": str(debug_csv_path),
        "failures_count": len(state.failures),
        "failures": [failure.to_dict() for failure in state.failures],
        "note": "Debug sanity artifacts are verification-only and not production tax outputs.",
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_sanity_checks(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    output_dir: Path,
    normalized_alias: str,
    tax_year: int,
) -> _SanityCheckResult:
    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    debug_dir = output_dir / "_sanity_debug" / f"ibkr_activity{alias_suffix}_{tax_year}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_csv_path = debug_dir / "ibkr_activity_modified_fx1_debug.csv"
    report_path = debug_dir / "sanity_report.json"
    tolerance = Decimal("0.01")

    state = _SanityState()
    _collect_trade_and_aggregate_data(
        state,
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        tolerance=tolerance,
    )
    selected_subtotals = _select_subtotals(state)
    selected_totals = _select_totals(state)
    _validate_subtotals(state, selected_subtotals=selected_subtotals, tolerance=tolerance)
    _validate_totals(state, selected_totals=selected_totals, tolerance=tolerance)

    sanity_output_rows = _build_sanity_output_rows(
        state,
        rows=rows,
        active_headers=active_headers,
    )
    with debug_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(sanity_output_rows)

    _write_sanity_report(state, report_path=report_path, debug_csv_path=debug_csv_path)

    return _SanityCheckResult(
        passed=len(state.failures) == 0,
        checked_closing_trades=state.checked_trade_rows,
        checked_closedlots=state.checked_closedlots,
        checked_subtotals=state.checked_subtotals,
        checked_totals=state.checked_totals,
        forex_ignored_rows=state.forex_ignored_rows,
        debug_dir=debug_dir,
        debug_csv_path=debug_csv_path,
        report_path=report_path,
        failures=state.failures,
    )
