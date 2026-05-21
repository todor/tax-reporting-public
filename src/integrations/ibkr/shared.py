from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from services.bnb_fx import get_exchange_rate

from .constants import REVIEW_STATUS_NON_TAXABLE, ZERO, FxRateProvider
from .models import CsvStructureError, FxConversionError, IbkrAnalyzerError, _ActiveHeader


@dataclass(frozen=True, slots=True)
class IbkrReportDateFormat:
    label: str
    strptime_format: str
    reason: str


ClosedLotDateFormatInference = IbkrReportDateFormat


def _activate_header(section: str, row: list[str], *, row_number: int) -> _ActiveHeader:
    return _ActiveHeader(section=section, row_number=row_number, headers=row[2:])


def _build_active_headers(
    rows: list[list[str]],
) -> tuple[dict[int, _ActiveHeader], set[str]]:
    active_by_section: dict[str, _ActiveHeader] = {}
    active_for_row: dict[int, _ActiveHeader] = {}
    seen_headers: set[str] = set()

    for row_idx, row in enumerate(rows):
        if len(row) < 2:
            continue
        section = row[0]
        row_type = row[1]
        if row_type == "Header":
            active = _activate_header(section, row, row_number=row_idx + 1)
            active_by_section[section] = active
            seen_headers.add(section)
            continue
        active = active_by_section.get(section)
        if active is not None:
            active_for_row[row_idx] = active

    return active_for_row, seen_headers


def _index_for(headers: list[str], *candidates: str, section_name: str) -> int:
    normalized = {name.strip(): i for i, name in enumerate(headers)}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise CsvStructureError(f"{section_name}: missing required column; expected one of {candidates}")


def _optional_index(headers: list[str], *candidates: str) -> int | None:
    normalized = {name.strip(): i for i, name in enumerate(headers)}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _fmt(value: Decimal, *, quant: Decimal | None = None) -> str:
    if quant is not None:
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(value, "f")


def _normalize_report_alias(raw: str | None) -> str:
    if raw is None:
        return ""
    alias = raw.strip()
    if alias == "":
        return ""
    alias = re.sub(r"\s+", "_", alias)
    alias = re.sub(r"[^A-Za-z0-9._-]+", "", alias)
    alias = alias.strip("._-")
    if alias == "":
        raise IbkrAnalyzerError("report alias must contain at least one alphanumeric character")
    return alias


def _parse_decimal(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise IbkrAnalyzerError(f"row {row_number}: missing {field_name}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def _parse_decimal_or_zero(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        return ZERO
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def _parse_optional_decimal(raw: str, *, row_number: int, field_name: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def _parse_trade_datetime(raw: str, *, row_number: int) -> datetime:
    text = raw.strip()
    for fmt in ("%Y-%m-%d, %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise IbkrAnalyzerError(f"row {row_number}: invalid Trade date/time format: {raw!r}")


def _infer_ibkr_slash_date_format(values: list[str]) -> IbkrReportDateFormat:
    saw_slash_date = False
    for raw in values:
        text = raw.strip()
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if match is None:
            continue
        saw_slash_date = True
        first = int(match.group(1))
        second = int(match.group(2))
        if second > 12:
            return IbkrReportDateFormat(
                label="M/D/YYYY",
                strptime_format="%m/%d/%Y",
                reason=f"found unambiguous slash date {text!r}",
            )
        if first > 12:
            return IbkrReportDateFormat(
                label="D/M/YYYY",
                strptime_format="%d/%m/%Y",
                reason=f"found unambiguous slash date {text!r}",
            )
    reason = (
        "all slash dates were ambiguous; defaulted to IBKR M/D/YYYY"
        if saw_slash_date
        else "no slash date values found; defaulted to IBKR M/D/YYYY"
    )
    return IbkrReportDateFormat(label="M/D/YYYY", strptime_format="%m/%d/%Y", reason=reason)


def _infer_closedlot_slash_date_format(values: list[str]) -> ClosedLotDateFormatInference:
    return _infer_ibkr_slash_date_format(values)


def _infer_ibkr_report_date_format(
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
) -> IbkrReportDateFormat:
    values: list[str] = []
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        base_len = 2 + len(active_header.headers)
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]
        for header_idx, header in enumerate(active_header.headers):
            if header.strip() not in {"Date", "Date/Time"}:
                continue
            if header_idx < len(data):
                values.append(data[header_idx])
    return _infer_ibkr_slash_date_format(values)


def _parse_ibkr_date(
    raw: str,
    *,
    row_number: int,
    field_name: str,
    report_date_format: IbkrReportDateFormat | None = None,
) -> date:
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d, %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    detected = report_date_format or _infer_ibkr_slash_date_format([text])
    if "/" in text:
        try:
            return datetime.strptime(text, detected.strptime_format).date()
        except ValueError as exc:
            raise IbkrAnalyzerError(
                f"row {row_number}: invalid {field_name} format: {raw!r} "
                f"with detected IBKR report date format {detected.label}"
            ) from exc
    raise IbkrAnalyzerError(
        f"row {row_number}: invalid {field_name} format: {raw!r} "
        f"with detected IBKR report date format {detected.label}"
    )


def _parse_closedlot_date(
    raw: str,
    *,
    row_number: int,
    slash_format: ClosedLotDateFormatInference | None = None,
) -> date:
    return _parse_ibkr_date(
        raw,
        row_number=row_number,
        field_name="ClosedLot date",
        report_date_format=slash_format,
    )


def _parse_interest_date(
    raw: str,
    *,
    row_number: int,
    report_date_format: IbkrReportDateFormat | None = None,
    field_name: str = "Interest date",
) -> date:
    return _parse_ibkr_date(
        raw,
        row_number=row_number,
        field_name=field_name,
        report_date_format=report_date_format,
    )


def _try_parse_decimal(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_reconciliation_quantity(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return ZERO
    cleaned = text.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_decimal_loose_or_zero(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return ZERO
    cleaned = text.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _normalize_review_status(raw: str) -> str:
    normalized = raw.strip().upper().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    if normalized == "NONTAXABLE":
        return REVIEW_STATUS_NON_TAXABLE
    if normalized == "TAXABLEFROMHERE":
        return "TAXABLE-FROM-HERE"
    if normalized == "NONTAXABLEFROMHERE":
        return "NON-TAXABLE-FROM-HERE"
    return normalized


def _is_interest_total_row(currency: str) -> bool:
    return currency.strip().upper().startswith("TOTAL")


def _code_has_closing_token(code: str) -> bool:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", code.upper()) if token]
    return "C" in tokens


def _default_fx_provider(cache_dir: str | Path | None) -> FxRateProvider:
    def provider(currency: str, on_date: date) -> Decimal:
        normalized = currency.strip().upper()
        if normalized == "EUR":
            return Decimal("1")
        fx = get_exchange_rate(normalized, on_date, cache_dir=cache_dir)
        return fx.rate

    return provider


def _to_eur(amount: Decimal, currency: str, on_date: date, fx_provider: FxRateProvider, *, row_number: int) -> tuple[Decimal, Decimal]:
    normalized = currency.strip().upper()
    try:
        fx_rate = fx_provider(normalized, on_date)
    except Exception as exc:  # noqa: BLE001
        raise FxConversionError(
            f"row {row_number}: FX conversion failed for currency={normalized} on date={on_date.isoformat()}"
        ) from exc
    return amount * fx_rate, fx_rate


def _set_existing_section_value(
    *,
    rows: list[list[str]],
    row_idx: int,
    active_header: _ActiveHeader,
    field_idx: int | None,
    value: str,
    only_if_empty: bool,
) -> None:
    if field_idx is None:
        return
    base_len = 2 + len(active_header.headers)
    row = rows[row_idx]
    if len(row) < base_len:
        row.extend([""] * (base_len - len(row)))
    current = row[2 + field_idx].strip()
    if only_if_empty and current != "":
        return
    row[2 + field_idx] = value


__all__ = [name for name in globals() if not name.startswith("__")]
