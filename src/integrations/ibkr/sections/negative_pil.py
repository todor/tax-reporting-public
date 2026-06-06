from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from ..constants import (
    NEGATIVE_PIL_MODE_ALWAYS_NET,
    NEGATIVE_PIL_MODE_IGNORE,
    NEGATIVE_PIL_MODE_POSITION_AWARE,
    NEGATIVE_PIL_STATUS_DEFER,
    NEGATIVE_PIL_STATUS_IGNORE,
    NEGATIVE_PIL_STATUS_NET,
    NEGATIVE_PIL_STATUS_REVIEW,
    ZERO,
)
from ..models import CsvStructureError, InstrumentListing, _ActiveHeader
from ..shared import (
    IbkrReportDateFormat,
    _index_for,
    _normalize_data_discriminator,
    _optional_index,
    _parse_closedlot_date,
    _parse_decimal,
    _parse_ibkr_date,
    _parse_reconciliation_quantity,
    _parse_trade_datetime,
)
from .instruments import _is_cfd_asset, _is_supported_asset, _resolve_instrument_for_trade_symbol

_PIL_SYMBOL_RE = re.compile(
    r"^\s*(?P<symbol>[A-Za-z0-9._/\- ]+)\((?P<isin>[A-Z]{2}[A-Z0-9]{10})\)\s+"
    r"Payment in Lieu of Dividend \(Ordinary Dividend\)",
    re.IGNORECASE,
)
_ACCRUAL_CLASSIFICATION_CFD = "cfd"
_ACCRUAL_CLASSIFICATION_SECURITY = "security"
_ACCRUAL_CLASSIFICATION_UNSUPPORTED = "unsupported"
_ACCRUAL_LINK_NONE = "none"
_ACCRUAL_LINK_UNIQUE = "unique"
_ACCRUAL_LINK_MULTIPLE = "multiple"


@dataclass(slots=True)
class NegativePilExposureRange:
    kind: str
    symbol: str
    isin: str
    start: date
    end: date | None
    source_rows: tuple[int, ...]
    source: str

    def contains(self, target: date) -> bool:
        return self.start <= target and (self.end is None or target <= self.end)

    def closed_by_year_end(self, tax_year: int) -> bool:
        return self.end is not None and self.end <= date(tax_year, 12, 31)

    def format(self) -> str:
        end = self.end.isoformat() if self.end is not None else "open"
        rows = ",".join(str(row) for row in self.source_rows)
        symbol = self.symbol or "-"
        isin = self.isin or "-"
        return f"{self.kind} {symbol}/{isin} [{self.start.isoformat()}, {end}] source_lines={rows} source={self.source}"


@dataclass(slots=True)
class NegativePilAccrualMatch:
    asset_category: str
    asset_classification: str
    symbol: str
    currency: str
    pay_date: date
    exposure_date: date
    amount: Decimal
    row_number: int


@dataclass(slots=True)
class NegativePilExposureIndex:
    security_ranges: list[NegativePilExposureRange]
    cfd_ranges: list[NegativePilExposureRange]
    dividend_accruals: list[NegativePilAccrualMatch]


@dataclass(slots=True)
class NegativePilAutoDecision:
    auto_status: str
    tax_status: str
    parsed_symbol: str
    parsed_isin: str
    likely_source: str
    candidate_ranges: list[NegativePilExposureRange]
    accrual_link_status: str = ""
    accrual_asset_category: str = ""
    accrual_asset_classification: str = ""
    accrual_symbol: str = ""
    accrual_ex_date: date | None = None
    accrual_pay_date: date | None = None
    accrual_amount: Decimal | None = None
    matching_date_source: str = ""


def _row_data(row: list[str], active_header: _ActiveHeader) -> list[str]:
    return row[2:] + [""] * (len(active_header.headers) - len(row[2:]))


def _trade_indexes(active_header: _ActiveHeader) -> dict[str, int | None]:
    section_name = f"Trades header at row {active_header.row_number}"
    return {
        "asset": _index_for(active_header.headers, "Asset Category", section_name=section_name),
        "symbol": _index_for(active_header.headers, "Symbol", section_name=section_name),
        "quantity": _optional_index(active_header.headers, "Quantity", "Qty"),
        "date_time": _index_for(active_header.headers, "Date/Time", section_name=section_name),
        "discriminator": _index_for(active_header.headers, "DataDiscriminator", section_name=section_name),
    }


def _open_positions_indexes(active_header: _ActiveHeader) -> dict[str, int]:
    section_name = f"Open Positions header at row {active_header.row_number}"
    return {
        "asset": _index_for(active_header.headers, "Asset Category", section_name=section_name),
        "symbol": _index_for(active_header.headers, "Symbol", section_name=section_name),
        "quantity": _index_for(active_header.headers, "Summary Quantity", "Quantity", section_name=section_name),
        "discriminator": _index_for(
            active_header.headers,
            "DataDiscriminator",
            "Data Discriminator",
            section_name=section_name,
        ),
    }


def _mtm_indexes(active_header: _ActiveHeader) -> dict[str, int]:
    section_name = f"Mark-to-Market Performance Summary header at row {active_header.row_number}"
    return {
        "asset": _index_for(active_header.headers, "Asset Category", section_name=section_name),
        "symbol": _index_for(active_header.headers, "Symbol", section_name=section_name),
        "prior_quantity": _index_for(active_header.headers, "Prior Quantity", section_name=section_name),
    }


def _dividend_accrual_indexes(active_header: _ActiveHeader) -> dict[str, int]:
    section_name = f"Change in Dividend Accruals header at row {active_header.row_number}"
    return {
        "asset": _index_for(active_header.headers, "Asset Category", section_name=section_name),
        "currency": _index_for(active_header.headers, "Currency", section_name=section_name),
        "symbol": _index_for(active_header.headers, "Symbol", section_name=section_name),
        "ex_date": _index_for(active_header.headers, "Ex Date", section_name=section_name),
        "pay_date": _index_for(active_header.headers, "Pay Date", section_name=section_name),
        "quantity": _index_for(active_header.headers, "Quantity", section_name=section_name),
        "gross_amount": _index_for(active_header.headers, "Gross Amount", section_name=section_name),
        "net_amount": _index_for(active_header.headers, "Net Amount", section_name=section_name),
    }


def _security_identity(
    *,
    asset_category: str,
    symbol_raw: str,
    listings: dict[str, InstrumentListing],
) -> tuple[str, str] | None:
    instrument, normalized_symbol, _reason = _resolve_instrument_for_trade_symbol(
        asset_category=asset_category,
        trade_symbol=symbol_raw,
        listings=listings,
    )
    if instrument is None:
        return None
    symbol = normalized_symbol or instrument.canonical_symbol or symbol_raw.strip().upper()
    return symbol, instrument.isin


def _range_identity(
    *,
    asset_category: str,
    symbol_raw: str,
    listings: dict[str, InstrumentListing],
) -> tuple[str, str] | None:
    if _is_cfd_asset(asset_category):
        return symbol_raw.strip().upper(), ""
    if _is_supported_asset(asset_category):
        return _security_identity(asset_category=asset_category, symbol_raw=symbol_raw, listings=listings)
    return None


def _accrual_asset_classification(asset_category: str) -> str:
    if _is_cfd_asset(asset_category):
        return _ACCRUAL_CLASSIFICATION_CFD
    if _is_supported_asset(asset_category):
        return _ACCRUAL_CLASSIFICATION_SECURITY
    return _ACCRUAL_CLASSIFICATION_UNSUPPORTED


def _attached_closedlot_indices(
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    start_idx: int,
) -> list[int]:
    indices: list[int] = []
    scan_idx = start_idx + 1
    while scan_idx < len(rows):
        scan_row = rows[scan_idx]
        if len(scan_row) < 2 or scan_row[0] != "Trades" or scan_row[1] != "Data":
            break
        active_header = active_headers.get(scan_idx)
        if active_header is None:
            break
        idx = _trade_indexes(active_header)
        data = _row_data(scan_row, active_header)
        discriminator = _normalize_data_discriminator(data[idx["discriminator"]].strip())  # type: ignore[index]
        if discriminator != "closedlot":
            break
        indices.append(scan_idx)
        scan_idx += 1
    return indices


def _closedlot_ranges(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    report_date_format: IbkrReportDateFormat,
) -> tuple[list[NegativePilExposureRange], list[NegativePilExposureRange]]:
    security_ranges: list[NegativePilExposureRange] = []
    cfd_ranges: list[NegativePilExposureRange] = []
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        idx = _trade_indexes(active_header)
        data = _row_data(row, active_header)
        discriminator = _normalize_data_discriminator(data[idx["discriminator"]].strip())  # type: ignore[index]
        if discriminator != "trade":
            continue
        asset_category = data[idx["asset"]].strip()  # type: ignore[index]
        if not (_is_supported_asset(asset_category) or _is_cfd_asset(asset_category)):
            continue
        closing_date = _parse_trade_datetime(
            data[idx["date_time"]],  # type: ignore[index]
            row_number=row_number,
        ).date()
        symbol_raw = data[idx["symbol"]].strip()  # type: ignore[index]
        identity = _range_identity(asset_category=asset_category, symbol_raw=symbol_raw, listings=listings)
        if identity is None:
            continue
        symbol, isin = identity
        for closed_idx in _attached_closedlot_indices(rows, active_headers, row_idx):
            closed_row = rows[closed_idx]
            closed_header = active_headers.get(closed_idx)
            if closed_header is None:
                continue
            closed_data = _row_data(closed_row, closed_header)
            closed_indexes = _trade_indexes(closed_header)
            closed_discriminator = _normalize_data_discriminator(
                closed_data[closed_indexes["discriminator"]].strip()  # type: ignore[index]
            )
            if closed_discriminator != "closedlot":
                continue
            closed_asset = closed_data[closed_indexes["asset"]].strip()  # type: ignore[index]
            if closed_asset != asset_category:
                continue
            closed_symbol = closed_data[closed_indexes["symbol"]].strip()  # type: ignore[index]
            closed_identity = _range_identity(
                asset_category=closed_asset,
                symbol_raw=closed_symbol,
                listings=listings,
            )
            if closed_identity is not None and closed_identity != identity:
                continue
            quantity_idx = closed_indexes["quantity"]
            if quantity_idx is None:
                continue
            quantity = _parse_decimal(
                closed_data[quantity_idx],
                row_number=closed_idx + 1,
                field_name="ClosedLot Quantity",
            )
            if quantity >= ZERO:
                continue
            opened = _parse_closedlot_date(
                closed_data[closed_indexes["date_time"]],  # type: ignore[index]
                row_number=closed_idx + 1,
                slash_format=report_date_format,
            )
            target = cfd_ranges if _is_cfd_asset(asset_category) else security_ranges
            target.append(
                NegativePilExposureRange(
                    kind="short-cfd" if _is_cfd_asset(asset_category) else "short-security",
                    symbol=symbol,
                    isin=isin,
                    start=opened,
                    end=closing_date,
                    source_rows=(row_number, closed_idx + 1),
                    source="attached ClosedLot",
                )
            )
    return security_ranges, cfd_ranges


def _prior_short_quantities(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
) -> dict[tuple[str, str, str], Decimal]:
    quantities: dict[tuple[str, str, str], Decimal] = {}
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Mark-to-Market Performance Summary" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        try:
            idx = _mtm_indexes(active_header)
        except CsvStructureError:
            continue
        data = _row_data(row, active_header)
        asset_category = data[idx["asset"]].strip()
        if not (_is_supported_asset(asset_category) or _is_cfd_asset(asset_category)):
            continue
        symbol_raw = data[idx["symbol"]].strip()
        if symbol_raw == "":
            continue
        identity = _range_identity(asset_category=asset_category, symbol_raw=symbol_raw, listings=listings)
        if identity is None:
            continue
        quantity = _parse_reconciliation_quantity(data[idx["prior_quantity"]])
        if quantity is None:
            continue
        symbol, isin = identity
        quantities[(asset_category, symbol, isin)] = quantities.get((asset_category, symbol, isin), ZERO) + quantity
    return quantities


def _trade_quantity_events(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
) -> dict[tuple[str, str, str], list[tuple[date, Decimal]]]:
    events: dict[tuple[str, str, str], list[tuple[date, Decimal]]] = {}
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        idx = _trade_indexes(active_header)
        data = _row_data(row, active_header)
        discriminator = _normalize_data_discriminator(data[idx["discriminator"]].strip())  # type: ignore[index]
        if discriminator != "trade":
            continue
        asset_category = data[idx["asset"]].strip()  # type: ignore[index]
        if not (_is_supported_asset(asset_category) or _is_cfd_asset(asset_category)):
            continue
        quantity_idx = idx["quantity"]
        if quantity_idx is None:
            continue
        symbol_raw = data[idx["symbol"]].strip()  # type: ignore[index]
        identity = _range_identity(asset_category=asset_category, symbol_raw=symbol_raw, listings=listings)
        if identity is None:
            continue
        quantity = _parse_decimal(data[quantity_idx], row_number=row_number, field_name="Quantity")
        trade_date = _parse_trade_datetime(data[idx["date_time"]], row_number=row_number).date()  # type: ignore[index]
        symbol, isin = identity
        key = (asset_category, symbol, isin)
        events.setdefault(key, []).append((trade_date, quantity))
    return events


def _short_start_by_key(
    *,
    tax_year: int,
    prior_quantities: dict[tuple[str, str, str], Decimal],
    trade_events: dict[tuple[str, str, str], list[tuple[date, Decimal]]],
) -> dict[tuple[str, str, str], date | None]:
    starts: dict[tuple[str, str, str], date | None] = {}
    for key in set(prior_quantities) | set(trade_events):
        quantity = prior_quantities.get(key, ZERO)
        start = date(tax_year, 1, 1) if quantity < ZERO else None
        for event_date, delta in sorted(trade_events.get(key, []), key=lambda item: item[0]):
            before = quantity
            quantity += delta
            if before >= ZERO and quantity < ZERO:
                start = event_date
            elif before < ZERO and quantity >= ZERO:
                start = None
        starts[key] = start
    return starts


def _open_short_ranges(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    tax_year: int,
) -> tuple[list[NegativePilExposureRange], list[NegativePilExposureRange]]:
    security_ranges: list[NegativePilExposureRange] = []
    cfd_ranges: list[NegativePilExposureRange] = []
    starts = _short_start_by_key(
        tax_year=tax_year,
        prior_quantities=_prior_short_quantities(rows=rows, active_headers=active_headers, listings=listings),
        trade_events=_trade_quantity_events(rows=rows, active_headers=active_headers, listings=listings),
    )
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Open Positions" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        try:
            idx = _open_positions_indexes(active_header)
        except CsvStructureError:
            continue
        data = _row_data(row, active_header)
        discriminator = data[idx["discriminator"]].strip().lower()
        if discriminator != "summary":
            continue
        asset_category = data[idx["asset"]].strip()
        if not (_is_supported_asset(asset_category) or _is_cfd_asset(asset_category)):
            continue
        quantity = _parse_reconciliation_quantity(data[idx["quantity"]])
        if quantity is None or quantity >= ZERO:
            continue
        symbol_raw = data[idx["symbol"]].strip()
        identity = _range_identity(asset_category=asset_category, symbol_raw=symbol_raw, listings=listings)
        if identity is None:
            continue
        symbol, isin = identity
        key = (asset_category, symbol, isin)
        start = starts.get(key) or date(tax_year, 1, 1)
        target = cfd_ranges if _is_cfd_asset(asset_category) else security_ranges
        target.append(
            NegativePilExposureRange(
                kind="short-cfd" if _is_cfd_asset(asset_category) else "short-security",
                symbol=symbol,
                isin=isin,
                start=start,
                end=None,
                source_rows=(row_number,),
                source="negative Open Positions summary",
            )
        )
    return security_ranges, cfd_ranges


def _dividend_accrual_matches(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    report_date_format: IbkrReportDateFormat,
) -> list[NegativePilAccrualMatch]:
    matches: list[NegativePilAccrualMatch] = []
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Change in Dividend Accruals" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        try:
            idx = _dividend_accrual_indexes(active_header)
        except CsvStructureError:
            continue
        data = _row_data(row, active_header)
        asset_category = data[idx["asset"]].strip()
        asset_classification = _accrual_asset_classification(asset_category)
        symbol = data[idx["symbol"]].strip().upper()
        currency = data[idx["currency"]].strip().upper()
        if symbol == "" or currency == "":
            continue
        quantity = _parse_reconciliation_quantity(data[idx["quantity"]])
        if quantity is None or quantity >= ZERO:
            continue
        gross_amount = _parse_decimal(
            data[idx["gross_amount"]],
            row_number=row_number,
            field_name="Change in Dividend Accruals Gross Amount",
        )
        net_amount = _parse_decimal(
            data[idx["net_amount"]],
            row_number=row_number,
            field_name="Change in Dividend Accruals Net Amount",
        )
        amount = gross_amount if gross_amount < ZERO else net_amount
        if amount >= ZERO:
            continue
        matches.append(
            NegativePilAccrualMatch(
                asset_category=asset_category,
                asset_classification=asset_classification,
                symbol=symbol,
                currency=currency,
                pay_date=_parse_ibkr_date(
                    data[idx["pay_date"]],
                    row_number=row_number,
                    field_name="Change in Dividend Accruals Pay Date",
                    report_date_format=report_date_format,
                ),
                exposure_date=_parse_ibkr_date(
                    data[idx["ex_date"]],
                    row_number=row_number,
                    field_name="Change in Dividend Accruals Ex Date",
                    report_date_format=report_date_format,
                ),
                amount=amount,
                row_number=row_number,
            )
        )
    return matches


def build_negative_pil_exposure_index(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    tax_year: int,
    report_date_format: IbkrReportDateFormat,
) -> NegativePilExposureIndex:
    closed_security, closed_cfd = _closedlot_ranges(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        report_date_format=report_date_format,
    )
    open_security, open_cfd = _open_short_ranges(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        tax_year=tax_year,
    )
    return NegativePilExposureIndex(
        security_ranges=[*closed_security, *open_security],
        cfd_ranges=[*closed_cfd, *open_cfd],
        dividend_accruals=_dividend_accrual_matches(
            rows=rows,
            active_headers=active_headers,
            report_date_format=report_date_format,
        ),
    )


def parse_symbol_encoded_pil(description: str) -> tuple[str, str] | None:
    match = _PIL_SYMBOL_RE.match(description)
    if match is None:
        return None
    return match.group("symbol").strip().upper(), match.group("isin").strip().upper()


def _status_from_candidates(
    *,
    candidates: list[NegativePilExposureRange],
    tax_year: int,
    net_message: str,
    defer_message: str,
    review_none_message: str,
    review_mixed_message: str,
) -> tuple[str, str]:
    if not candidates:
        return NEGATIVE_PIL_STATUS_REVIEW, review_none_message
    closed = [candidate for candidate in candidates if candidate.closed_by_year_end(tax_year)]
    open_ranges = [candidate for candidate in candidates if not candidate.closed_by_year_end(tax_year)]
    if closed and not open_ranges:
        return NEGATIVE_PIL_STATUS_NET, net_message
    if open_ranges and not closed:
        return NEGATIVE_PIL_STATUS_DEFER, defer_message
    return NEGATIVE_PIL_STATUS_REVIEW, review_mixed_message


def _matching_accrual_rows(
    *,
    exposure_index: NegativePilExposureIndex,
    symbol: str | None,
    currency: str,
    pil_date: date,
    amount: Decimal,
) -> list[NegativePilAccrualMatch]:
    currency = currency.strip().upper()
    return [
        accrual
        for accrual in exposure_index.dividend_accruals
        if (symbol is None or accrual.symbol == symbol)
        and accrual.currency == currency
        and accrual.pay_date == pil_date
        and accrual.amount == amount
    ]


def _dedupe_accrual_events(matches: list[NegativePilAccrualMatch]) -> list[NegativePilAccrualMatch]:
    seen: set[tuple[str, str, str, str, date, date, Decimal]] = set()
    events: list[NegativePilAccrualMatch] = []
    for match in matches:
        key = (
            match.asset_category,
            match.asset_classification,
            match.symbol,
            match.currency,
            match.exposure_date,
            match.pay_date,
            match.amount,
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(match)
    return events


def _matching_accrual_events(
    *,
    exposure_index: NegativePilExposureIndex,
    symbol: str | None,
    currency: str,
    pil_date: date,
    amount: Decimal,
) -> list[NegativePilAccrualMatch]:
    return _dedupe_accrual_events(
        _matching_accrual_rows(
            exposure_index=exposure_index,
            symbol=symbol,
            currency=currency,
            pil_date=pil_date,
            amount=amount,
        )
    )


def _format_accrual_events(events: list[NegativePilAccrualMatch]) -> str:
    if not events:
        return "-"
    return "; ".join(
        (
            f"asset_category={event.asset_category or '-'} "
            f"classification={event.asset_classification or '-'} "
            f"symbol={event.symbol or '-'} "
            f"ex_date={event.exposure_date.isoformat()} "
            f"pay_date={event.pay_date.isoformat()} "
            f"amount={event.amount}"
        )
        for event in events
    )


def _with_accrual_fields(
    *,
    auto_status: str,
    tax_status: str,
    parsed_symbol: str,
    parsed_isin: str,
    likely_source: str,
    candidate_ranges: list[NegativePilExposureRange],
    accrual_link_status: str,
    matching_date_source: str,
    event: NegativePilAccrualMatch | None = None,
) -> NegativePilAutoDecision:
    return NegativePilAutoDecision(
        auto_status=auto_status,
        tax_status=tax_status,
        parsed_symbol=parsed_symbol,
        parsed_isin=parsed_isin,
        likely_source=likely_source,
        candidate_ranges=candidate_ranges,
        accrual_link_status=accrual_link_status,
        accrual_asset_category=event.asset_category if event is not None else "",
        accrual_asset_classification=event.asset_classification if event is not None else "",
        accrual_symbol=event.symbol if event is not None else "",
        accrual_ex_date=event.exposure_date if event is not None else None,
        accrual_pay_date=event.pay_date if event is not None else None,
        accrual_amount=event.amount if event is not None else None,
        matching_date_source=matching_date_source,
    )


def _security_candidates_for_dates(
    *,
    ranges: list[NegativePilExposureRange],
    dates: list[date],
    symbol: str,
    isin: str,
    resolved_symbol: str,
    resolved_isin: str,
) -> list[NegativePilExposureRange]:
    seen: set[tuple[str, str, date, date | None, tuple[int, ...], str]] = set()
    candidates: list[NegativePilExposureRange] = []
    for candidate in ranges:
        if not (
            (isin and candidate.isin == isin)
            or (resolved_isin and candidate.isin == resolved_isin)
            or candidate.symbol == resolved_symbol
            or candidate.symbol == symbol
        ):
            continue
        if not any(candidate.contains(candidate_date) for candidate_date in dates):
            continue
        key = (candidate.kind, candidate.symbol, candidate.start, candidate.end, candidate.source_rows, candidate.source)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _cfd_candidates_for_event(
    *,
    ranges: list[NegativePilExposureRange],
    event: NegativePilAccrualMatch,
) -> list[NegativePilExposureRange]:
    return [
        candidate
        for candidate in ranges
        if candidate.symbol == event.symbol and candidate.contains(event.exposure_date)
    ]


def decide_negative_pil_auto_status(
    *,
    description: str,
    pil_date: date,
    currency: str,
    amount: Decimal,
    mode: str,
    tax_year: int,
    exposure_index: NegativePilExposureIndex,
    listings: dict[str, InstrumentListing],
) -> NegativePilAutoDecision:
    if mode == NEGATIVE_PIL_MODE_ALWAYS_NET:
        return NegativePilAutoDecision(
            auto_status=NEGATIVE_PIL_STATUS_NET,
            tax_status="Negative PIL netted because --negative-pil-mode=always-net.",
            parsed_symbol="",
            parsed_isin="",
            likely_source="mode override",
            candidate_ranges=[],
        )
    if mode == NEGATIVE_PIL_MODE_IGNORE:
        return NegativePilAutoDecision(
            auto_status=NEGATIVE_PIL_STATUS_IGNORE,
            tax_status="Negative PIL ignored because --negative-pil-mode=ignore.",
            parsed_symbol="",
            parsed_isin="",
            likely_source="mode override",
            candidate_ranges=[],
        )
    if mode != NEGATIVE_PIL_MODE_POSITION_AWARE:
        raise ValueError(f"unsupported negative PIL mode: {mode}")

    parsed = parse_symbol_encoded_pil(description)
    if parsed is not None:
        symbol, isin = parsed
        instrument = listings.get(symbol)
        resolved_symbol = instrument.canonical_symbol if instrument is not None else symbol
        resolved_isin = instrument.isin if instrument is not None and instrument.isin else isin
        accrual_events = _matching_accrual_events(
            exposure_index=exposure_index,
            symbol=symbol,
            currency=currency,
            pil_date=pil_date,
            amount=amount,
        )
        if len(accrual_events) > 1:
            return _with_accrual_fields(
                auto_status=NEGATIVE_PIL_STATUS_REVIEW,
                tax_status=(
                    "Multiple possible dividend-accrual events matched the cash negative PIL row; "
                    f"manual review required. Events: {_format_accrual_events(accrual_events)}"
                ),
                parsed_symbol=symbol,
                parsed_isin=isin,
                likely_source="likely short security / stock-borrow dividend compensation debit",
                candidate_ranges=[],
                accrual_link_status=_ACCRUAL_LINK_MULTIPLE,
                matching_date_source="ambiguous_dividend_accrual",
            )
        accrual_event = accrual_events[0] if accrual_events else None
        if accrual_event is not None and accrual_event.asset_classification != _ACCRUAL_CLASSIFICATION_SECURITY:
            return _with_accrual_fields(
                auto_status=NEGATIVE_PIL_STATUS_REVIEW,
                tax_status=(
                    "A matching dividend-accrual event was found for the symbol-encoded negative PIL row, "
                    f"but its asset category is unsupported or ambiguous for short-security matching "
                    f"(asset_category={accrual_event.asset_category}, "
                    f"classification={accrual_event.asset_classification})."
                ),
                parsed_symbol=symbol,
                parsed_isin=isin,
                likely_source="likely short security / stock-borrow dividend compensation debit",
                candidate_ranges=[],
                accrual_link_status=_ACCRUAL_LINK_UNIQUE,
                matching_date_source="unsupported_dividend_accrual",
                event=accrual_event,
            )
        matching_date = accrual_event.exposure_date if accrual_event is not None else pil_date
        matching_date_source = "linked_dividend_accrual_ex_date" if accrual_event is not None else "cash_pil_row_date_fallback"
        matching_date_text = (
            f"linked dividend accrual Ex Date {matching_date.isoformat()} "
            f"(Pay Date {accrual_event.pay_date.isoformat()})"
            if accrual_event is not None
            else "cash PIL row date fallback"
        )
        candidates = _security_candidates_for_dates(
            ranges=exposure_index.security_ranges,
            dates=[matching_date],
            symbol=symbol,
            isin=isin,
            resolved_symbol=resolved_symbol,
            resolved_isin=resolved_isin,
        )
        if instrument is None:
            return _with_accrual_fields(
                auto_status=NEGATIVE_PIL_STATUS_REVIEW,
                tax_status=(
                    f"No matching Financial Instrument Information entry was found for negative PIL "
                    f"symbol={symbol} ISIN={isin}; manual review required."
                ),
                parsed_symbol=symbol,
                parsed_isin=isin,
                likely_source="likely short security / stock-borrow dividend compensation debit",
                candidate_ranges=candidates,
                accrual_link_status=_ACCRUAL_LINK_UNIQUE if accrual_event is not None else _ACCRUAL_LINK_NONE,
                matching_date_source=matching_date_source,
                event=accrual_event,
            )
        status, message = _status_from_candidates(
            candidates=candidates,
            tax_year=tax_year,
            net_message=(
                f"Matching short-security exposure was found for symbol={symbol} ISIN={isin} "
                f"on {matching_date_text} and all matching ranges were closed by year-end."
            ),
            defer_message=(
                f"Matching short-security exposure was found for symbol={symbol} ISIN={isin} "
                f"on {matching_date_text} but remained open at year-end."
            ),
            review_none_message=(
                f"No matching short-security exposure was found for symbol={symbol} ISIN={isin} "
                f"on {matching_date_text}."
            ),
            review_mixed_message=(
                f"Negative PIL allocation is ambiguous for symbol={symbol} ISIN={isin}: "
                "some matching short-security ranges were closed by year-end and some remained open."
            ),
        )
        return _with_accrual_fields(
            auto_status=status,
            tax_status=message,
            parsed_symbol=symbol,
            parsed_isin=isin,
            likely_source="likely short security / stock-borrow dividend compensation debit",
            candidate_ranges=candidates,
            accrual_link_status=_ACCRUAL_LINK_UNIQUE if accrual_event is not None else _ACCRUAL_LINK_NONE,
            matching_date_source=matching_date_source,
            event=accrual_event,
        )

    accrual_events = _matching_accrual_events(
        exposure_index=exposure_index,
        symbol=None,
        currency=currency,
        pil_date=pil_date,
        amount=amount,
    )
    if len(accrual_events) > 1:
        return _with_accrual_fields(
            auto_status=NEGATIVE_PIL_STATUS_REVIEW,
            tax_status=(
                "No-symbol negative PIL: multiple possible dividend-accrual events matched the cash PIL row; "
                f"manual review required. Events: {_format_accrual_events(accrual_events)}"
            ),
            parsed_symbol="",
            parsed_isin="",
            likely_source="ambiguous dividend-accrual linkage for no-symbol negative PIL",
            candidate_ranges=[],
            accrual_link_status=_ACCRUAL_LINK_MULTIPLE,
            matching_date_source="ambiguous_dividend_accrual",
        )
    if len(accrual_events) == 1:
        accrual_event = accrual_events[0]
        if accrual_event.asset_classification == _ACCRUAL_CLASSIFICATION_SECURITY:
            instrument = listings.get(accrual_event.symbol)
            resolved_symbol = instrument.canonical_symbol if instrument is not None else accrual_event.symbol
            resolved_isin = instrument.isin if instrument is not None and instrument.isin else ""
            candidates = _security_candidates_for_dates(
                ranges=exposure_index.security_ranges,
                dates=[accrual_event.exposure_date],
                symbol=accrual_event.symbol,
                isin=resolved_isin,
                resolved_symbol=resolved_symbol,
                resolved_isin=resolved_isin,
            )
            if instrument is None:
                return _with_accrual_fields(
                    auto_status=NEGATIVE_PIL_STATUS_REVIEW,
                    tax_status=(
                        "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique security-mapped "
                        f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                        f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                        f"{accrual_event.pay_date.isoformat()}, but the symbol was not resolved via "
                        "Financial Instrument Information. Manual review required."
                    ),
                    parsed_symbol="",
                    parsed_isin="",
                    likely_source="linked unique security-mapped dividend accrual event for no-symbol negative PIL",
                    candidate_ranges=candidates,
                    accrual_link_status=_ACCRUAL_LINK_UNIQUE,
                    matching_date_source="linked_dividend_accrual_ex_date",
                    event=accrual_event,
                )
            status, message = _status_from_candidates(
                candidates=candidates,
                tax_year=tax_year,
                net_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique security-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                    f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                    f"{accrual_event.pay_date.isoformat()}. Matching short-security exposure was checked "
                    "on the accrual Ex Date and all matching ranges were closed by year-end. "
                    "Manual review recommended because the cash PIL row does not identify the instrument directly."
                ),
                defer_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique security-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                    f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                    f"{accrual_event.pay_date.isoformat()}. Matching short-security exposure was checked "
                    "on the accrual Ex Date but remained open at year-end. Manual review recommended because "
                    "the cash PIL row does not identify the instrument directly."
                ),
                review_none_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique security-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                    f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                    f"{accrual_event.pay_date.isoformat()}, but no matching short-security exposure was found "
                    "on the accrual Ex Date. Manual review required."
                ),
                review_mixed_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique security-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, but allocation is ambiguous: "
                    "matching short-security ranges include both closed and still-open exposure ranges. "
                    "Manual review required."
                ),
            )
            return _with_accrual_fields(
                auto_status=status,
                tax_status=message,
                parsed_symbol="",
                parsed_isin="",
                likely_source="linked unique security-mapped dividend accrual event for no-symbol negative PIL",
                candidate_ranges=candidates,
                accrual_link_status=_ACCRUAL_LINK_UNIQUE,
                matching_date_source="linked_dividend_accrual_ex_date",
                event=accrual_event,
            )
        if accrual_event.asset_classification == _ACCRUAL_CLASSIFICATION_CFD:
            candidates = _cfd_candidates_for_event(ranges=exposure_index.cfd_ranges, event=accrual_event)
            status, message = _status_from_candidates(
                candidates=candidates,
                tax_year=tax_year,
                net_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique CFD-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                    f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                    f"{accrual_event.pay_date.isoformat()}. Matching short-CFD exposure was checked on the "
                    "accrual Ex Date and all matching ranges were closed by year-end. Manual review recommended "
                    "because the cash PIL row does not identify the instrument directly."
                ),
                defer_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique CFD-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                    f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                    f"{accrual_event.pay_date.isoformat()}. Matching short-CFD exposure was checked on the "
                    "accrual Ex Date but remained open at year-end. Manual review recommended because the cash "
                    "PIL row does not identify the instrument directly."
                ),
                review_none_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique CFD-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, "
                    f"Ex Date {accrual_event.exposure_date.isoformat()} and Pay Date "
                    f"{accrual_event.pay_date.isoformat()}, but no matching short-CFD exposure was found on "
                    "the accrual Ex Date. Manual review required because the cash PIL row does not identify "
                    "the instrument directly."
                ),
                review_mixed_message=(
                    "Negative PIL without parsed SYMBOL(ISIN). Linked to a unique CFD-mapped "
                    f"dividend-accrual event with Symbol {accrual_event.symbol}, but allocation is ambiguous: "
                    "matching short-CFD ranges include both closed and still-open exposure ranges. "
                    "Manual review required."
                ),
            )
            return _with_accrual_fields(
                auto_status=status,
                tax_status=message,
                parsed_symbol="",
                parsed_isin="",
                likely_source="linked unique CFD-mapped dividend accrual event for no-symbol negative PIL",
                candidate_ranges=candidates,
                accrual_link_status=_ACCRUAL_LINK_UNIQUE,
                matching_date_source="linked_dividend_accrual_ex_date",
                event=accrual_event,
            )
        return _with_accrual_fields(
            auto_status=NEGATIVE_PIL_STATUS_REVIEW,
            tax_status=(
                "Negative PIL without parsed SYMBOL(ISIN). A matching dividend-accrual event exists, "
                f"but its asset category is unsupported or ambiguous for negative PIL exposure matching "
                f"(asset_category={accrual_event.asset_category}, "
                f"classification={accrual_event.asset_classification}). Manual review required."
            ),
            parsed_symbol="",
            parsed_isin="",
            likely_source="unsupported dividend-accrual linkage for no-symbol negative PIL",
            candidate_ranges=[],
            accrual_link_status=_ACCRUAL_LINK_UNIQUE,
            matching_date_source="unsupported_dividend_accrual",
            event=accrual_event,
        )

    candidates = [candidate for candidate in exposure_index.cfd_ranges if candidate.contains(pil_date)]
    status, message = _status_from_candidates(
        candidates=candidates,
        tax_year=tax_year,
        net_message=(
            "Heuristic no-symbol negative PIL: most likely short CFD / derivative dividend-equivalent debit; "
            "matching short-CFD exposure was found on the cash PIL row date fallback and all matching ranges were closed by "
            "year-end. Manual review recommended."
        ),
        defer_message=(
            "Heuristic no-symbol negative PIL: most likely short CFD / derivative dividend-equivalent debit; "
            "matching short-CFD exposure was found on the cash PIL row date fallback but remained open at year-end. "
            "Manual review recommended."
        ),
        review_none_message=(
            "No-symbol negative PIL: no parsed symbol/ISIN and no matching short-CFD exposure was found "
            "on the cash PIL row date fallback."
        ),
        review_mixed_message=(
            "No-symbol negative PIL allocation is ambiguous: matching short-CFD ranges include both closed "
            "and still-open exposure ranges."
        ),
    )
    return _with_accrual_fields(
        auto_status=status,
        tax_status=message,
        parsed_symbol="",
        parsed_isin="",
        likely_source="heuristic most likely short CFD / derivative dividend-equivalent debit",
        candidate_ranges=candidates,
        accrual_link_status=_ACCRUAL_LINK_NONE,
        matching_date_source="cash_pil_row_date_fallback",
    )
