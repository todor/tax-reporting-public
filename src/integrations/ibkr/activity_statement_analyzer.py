from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Literal

from config import OUTPUT_DIR
from services.bnb_fx import get_exchange_rate

logger = logging.getLogger(__name__)

SUPPORTED_ASSET_CATEGORIES = {"Stocks", "Treasury Bills"}
FOREX_ASSET_CATEGORY = "Forex"

EU_REGULATED_MARKETS = {
    "IBIS",
    "IBIS2",
    "FWB",
    "AEB",
    "SBF",
    "ENEXT.BE",
    "ENEXT.PT",
    "ISE",
    "ENEXT.IR",
    "BVME",
    "BVME.ETF",
    "VSE",
    "WSE",
    "PSE",
    "OMXCP",
    "OMXSTO",
    "OMXHEX",
    "BME",
    "SIBE",
    "BM",
    "BVL",
    "PRA",
    "CPH",
    "OMXNO",
    "OSE",
}

EU_NON_REGULATED = {
    "EUIBSI",
    "EUDARK",
    "IBDARK",
    "CHIXEN",
    "CHIXES",
    "CHIXUK",
    "BATS",
    "TRQX",
    "AQUIS",
    "GETTEX",
    "GETTEX2",
    "TGATE",
    "SWB",
}

EXCHANGE_ALIASES = {
    "ISE": "ENEXT.IR",
    "BME": "SIBE",
    "BM": "SIBE",
}

ADDED_TRADES_COLUMNS = [
    "Fx Rate",
    "Comm/Fee (EUR)",
    "Proceeds (EUR)",
    "Basis (EUR)",
    "Sale Price (EUR)",
    "Purchase Price (EUR)",
    "Realized P/L (EUR)",
    "Realized P/L Wins (EUR)",
    "Realized P/L Losses (EUR)",
    "Normalized Symbol",
    "Listing Exchange",
    "Symbol Listed On EU Regulated Market",
    "Execution Exchange Classification",
    "Tax Exempt Mode",
    "Appendix Target",
    "Tax Treatment Reason",
    "Review Required",
    "Review Notes",
]

DECIMAL_TWO = Decimal("0.01")
DECIMAL_EIGHT = Decimal("0.00000001")
ZERO = Decimal("0")

TAX_MODE_LISTED_SYMBOL = "listed_symbol"
TAX_MODE_EXECUTION_EXCHANGE = "execution_exchange"

APPENDIX_5 = "APPENDIX_5"
APPENDIX_13 = "APPENDIX_13"
APPENDIX_REVIEW = "REVIEW_REQUIRED"
APPENDIX_IGNORED = "IGNORED"

EXCHANGE_CLASS_EU_REGULATED = "EU_REGULATED"
EXCHANGE_CLASS_EU_NON_REGULATED = "EU_NON_REGULATED"
EXCHANGE_CLASS_UNKNOWN = "UNKNOWN"
REVIEW_STATUS_TAXABLE = "TAXABLE"
REVIEW_STATUS_NON_TAXABLE = "NON-TAXABLE"

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "ibkr" / "activity_statement"

FxRateProvider = Callable[[str, date], Decimal]


class IbkrAnalyzerError(Exception):
    """Base error for IBKR analyzer failures."""


class CsvStructureError(IbkrAnalyzerError):
    """Raised when required sections/columns are missing."""


class FxConversionError(IbkrAnalyzerError):
    """Raised when FX conversion cannot be performed."""


@dataclass(slots=True)
class InstrumentListing:
    symbol: str
    listing_exchange: str
    listing_exchange_normalized: str
    listing_exchange_class: str
    is_eu_listed: bool


@dataclass(slots=True)
class BucketTotals:
    sale_price_eur: Decimal = ZERO
    purchase_eur: Decimal = ZERO
    wins_eur: Decimal = ZERO
    losses_eur: Decimal = ZERO
    rows: int = 0


@dataclass(slots=True)
class ReviewEntry:
    row_number: int
    symbol: str
    trade_date: str
    listing_exchange: str
    execution_exchange: str
    reason: str
    proceeds_eur: Decimal
    basis_eur: Decimal
    pnl_eur: Decimal


@dataclass(slots=True)
class AnalysisSummary:
    tax_year: int
    tax_exempt_mode: str
    appendix_5: BucketTotals = field(default_factory=BucketTotals)
    appendix_13: BucketTotals = field(default_factory=BucketTotals)
    review: BucketTotals = field(default_factory=BucketTotals)
    processed_trades_in_tax_year: int = 0
    trades_outside_tax_year: int = 0
    forex_ignored_rows: int = 0
    forex_ignored_abs_proceeds_eur: Decimal = ZERO
    ignored_non_closing_trade_rows: int = 0
    review_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    exchanges_used: set[str] = field(default_factory=set)
    review_exchanges: set[str] = field(default_factory=set)
    review_entries: list[ReviewEntry] = field(default_factory=list)
    review_required_rows: int = 0
    review_status_overrides_rows: int = 0
    unknown_review_status_rows: int = 0
    unknown_review_status_values: set[str] = field(default_factory=set)
    trades_data_rows_total: int = 0
    trade_discriminator_rows: int = 0
    closedlot_discriminator_rows: int = 0
    order_discriminator_rows: int = 0
    closing_trade_candidates: int = 0
    sanity_passed: bool = False
    sanity_checked_closing_trades: int = 0
    sanity_checked_closedlots: int = 0
    sanity_checked_subtotals: int = 0
    sanity_checked_totals: int = 0
    sanity_forex_ignored_rows: int = 0
    sanity_debug_artifacts_dir: str = ""
    sanity_debug_csv_path: str = ""
    sanity_report_path: str = ""
    sanity_failures_count: int = 0
    sanity_failure_messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisResult:
    input_csv_path: Path
    output_csv_path: Path
    declaration_txt_path: Path
    report_alias: str
    summary: AnalysisSummary


@dataclass(slots=True)
class _ActiveHeader:
    section: str
    row_number: int
    headers: list[str]


@dataclass(slots=True)
class _SanityFailure:
    check_type: str
    row_number: int | None
    row_kind: str
    asset_category: str
    symbol: str
    field_name: str
    expected: str
    actual: str
    difference: str
    details: str

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "check_type": self.check_type,
            "row_number": self.row_number,
            "row_kind": self.row_kind,
            "asset_category": self.asset_category,
            "symbol": self.symbol,
            "field_name": self.field_name,
            "expected": self.expected,
            "actual": self.actual,
            "difference": self.difference,
            "details": self.details,
        }

    def to_message(self) -> str:
        row = f"row {self.row_number}" if self.row_number is not None else "row n/a"
        symbol = self.symbol or "-"
        asset = self.asset_category or "-"
        return (
            f"{self.check_type}: {row} kind={self.row_kind} asset={asset} symbol={symbol} "
            f"field={self.field_name} expected={self.expected} actual={self.actual} "
            f"diff={self.difference} details={self.details}"
        )


@dataclass(slots=True)
class _SanityCheckResult:
    passed: bool
    checked_closing_trades: int
    checked_closedlots: int
    checked_subtotals: int
    checked_totals: int
    forex_ignored_rows: int
    debug_dir: Path
    debug_csv_path: Path
    report_path: Path
    failures: list[_SanityFailure]


def _fmt(value: Decimal, *, quant: Decimal | None = None) -> str:
    if quant is not None:
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(value, "f")


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


def _parse_trade_datetime(raw: str, *, row_number: int) -> datetime:
    text = raw.strip()
    for fmt in ("%Y-%m-%d, %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise IbkrAnalyzerError(f"row {row_number}: invalid Trade date/time format: {raw!r}")


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


def _parse_closedlot_date(raw: str, *, row_number: int) -> date:
    text = raw.strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid ClosedLot date format: {raw!r}") from exc


def _normalize_exchange(raw: str) -> str:
    normalized = raw.strip().upper()
    if not normalized:
        return ""
    if normalized.startswith("EUIBSI"):
        return "EUIBSI"
    return EXCHANGE_ALIASES.get(normalized, normalized)


def _split_symbol_aliases(raw: str) -> list[str]:
    aliases = [part.strip().upper() for part in raw.split(",")]
    return [alias for alias in aliases if alias]


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


def _classify_exchange(raw: str) -> str:
    normalized = _normalize_exchange(raw)
    if normalized in EU_REGULATED_MARKETS:
        return EXCHANGE_CLASS_EU_REGULATED
    if normalized in EU_NON_REGULATED:
        return EXCHANGE_CLASS_EU_NON_REGULATED
    return EXCHANGE_CLASS_UNKNOWN


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


def _normalize_review_status(raw: str) -> str:
    normalized = raw.strip().upper().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    if normalized == "NONTAXABLE":
        return REVIEW_STATUS_NON_TAXABLE
    return normalized


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
    symbol_is_eu_listed: bool | None,
    execution_exchange_class: str,
    missing_symbol_mapping: bool,
    forced_review_reason: str | None = None,
) -> tuple[str, str, bool]:
    if forced_review_reason is not None:
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
            return APPENDIX_REVIEW, forced_review_reason, True
        return APPENDIX_5, forced_review_reason, True

    if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL:
        if missing_symbol_mapping:
            return APPENDIX_5, "Missing symbol mapping", True
        if symbol_is_eu_listed:
            return APPENDIX_13, "EU-listed symbol (listed_symbol mode)", False
        return APPENDIX_5, "Non-EU-listed symbol", False

    if missing_symbol_mapping:
        return APPENDIX_REVIEW, "Missing symbol mapping", True

    if not symbol_is_eu_listed:
        return APPENDIX_5, "Non-EU-listed symbol", False

    if execution_exchange_class == EXCHANGE_CLASS_EU_REGULATED:
        return APPENDIX_13, "EU-listed + EU-regulated execution", False
    if execution_exchange_class == EXCHANGE_CLASS_EU_NON_REGULATED:
        return APPENDIX_REVIEW, "EU-listed + non-regulated execution", True
    return APPENDIX_REVIEW, "EU-listed + unknown execution", True


def _try_parse_decimal(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


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

    failures: list[_SanityFailure] = []
    row_failure_reasons: dict[int, list[str]] = {}

    def add_failure(
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
            diff = expected - actual
            diff_str = _fmt(diff)
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
        failures.append(failure)
        if row_number is not None:
            row_failure_reasons.setdefault(row_number, []).append(failure.to_message())

    sanity_extras_by_row: dict[int, dict[str, str]] = {}
    sanity_row_kind_by_row: dict[int, str] = {}
    checked_trade_rows = 0
    checked_closedlots = 0
    checked_subtotals = 0
    checked_totals = 0
    forex_ignored_rows = 0

    symbol_agg: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    asset_agg: dict[tuple[str, str], dict[str, Decimal]] = {}

    def ensure_bucket(bucket: dict, key: tuple) -> dict[str, Decimal]:
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

    def set_sanity_extras(row_idx: int, row_kind: str, values: dict[str, str]) -> None:
        existing = sanity_extras_by_row.get(row_idx, {})
        existing.update(values)
        sanity_extras_by_row[row_idx] = existing
        sanity_row_kind_by_row[row_idx] = row_kind

    subtotal_rows: list[dict[str, object]] = []
    total_rows: list[dict[str, object]] = []

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades":
            continue
        row_type = row[1]
        if row_type == "Header":
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            add_failure(
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
            if _is_forex_asset(asset_category):
                forex_ignored_rows += 1
                continue
            if not _is_supported_asset(asset_category):
                continue

            proceeds = _try_parse_decimal(data[field_idx.proceeds]) or ZERO
            commission = (
                _try_parse_decimal(data[field_idx.commission]) or ZERO
                if field_idx.commission is not None
                else ZERO
            )
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
                closedlot_basis = _try_parse_decimal(scan_data[scan_idxes.basis]) if scan_idxes.basis is not None else None
                if closedlot_basis is not None:
                    closedlot_sum += closedlot_basis
                closedlot_count_for_trade += 1
                checked_closedlots += 1
                set_sanity_extras(
                    scan_idx,
                    "ClosedLot",
                    {
                        "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                        "Basis (EUR)": _fmt(closedlot_basis or ZERO, quant=DECIMAL_EIGHT),
                    },
                    )
                scan_idx += 1

            is_closing_trade = _code_has_closing_token(code)
            expected_trade_basis = -closedlot_sum
            if (
                is_closing_trade
                and trade_basis is not None
                and closedlot_count_for_trade > 0
                and trade_basis != expected_trade_basis
            ):
                add_failure(
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
                    add_failure(
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
                    add_failure(
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
                add_failure(
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

            symbol_bucket = ensure_bucket(symbol_agg, (asset_category, currency, grouping_symbol))
            symbol_bucket["proceeds"] += proceeds
            symbol_bucket["basis"] += basis_for_checks
            symbol_bucket["comm_fee"] += commission
            symbol_bucket["sale_price"] += sale_price_for_checks
            symbol_bucket["purchase_price"] += purchase_price_for_checks
            symbol_bucket["realized_pl"] += realized_for_checks
            symbol_bucket["wins"] += wins
            symbol_bucket["losses"] += losses

            asset_bucket = ensure_bucket(asset_agg, (asset_category, currency))
            asset_bucket["proceeds"] += proceeds
            asset_bucket["basis"] += basis_for_checks
            asset_bucket["comm_fee"] += commission
            asset_bucket["sale_price"] += sale_price_for_checks
            asset_bucket["purchase_price"] += purchase_price_for_checks
            asset_bucket["realized_pl"] += realized_for_checks
            asset_bucket["wins"] += wins
            asset_bucket["losses"] += losses

            checked_trade_rows += 1
            set_sanity_extras(
                row_idx,
                "Trade",
                {
                    "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                    "Comm/Fee (EUR)": _fmt(commission, quant=DECIMAL_EIGHT),
                    "Proceeds (EUR)": _fmt(proceeds, quant=DECIMAL_EIGHT),
                    "Basis (EUR)": _fmt(basis_for_checks, quant=DECIMAL_EIGHT),
                    "Sale Price (EUR)": _fmt(sale_price_for_checks, quant=DECIMAL_EIGHT) if is_closing_trade else "",
                    "Purchase Price (EUR)": _fmt(purchase_price_for_checks, quant=DECIMAL_EIGHT) if is_closing_trade else "",
                    "Realized P/L (EUR)": _fmt(realized_for_checks, quant=DECIMAL_EIGHT),
                    "Realized P/L Wins (EUR)": _fmt(wins, quant=DECIMAL_EIGHT),
                    "Realized P/L Losses (EUR)": _fmt(losses, quant=DECIMAL_EIGHT),
                    "Normalized Symbol": normalized_symbol,
                },
            )
            continue

        if row_type in {"SubTotal", "Total"}:
            if _is_forex_asset(asset_category):
                continue
            if not _is_supported_asset(asset_category):
                continue

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

            container = subtotal_rows if row_type == "SubTotal" else total_rows
            container.append(
                {
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

    selected_subtotals: list[dict[str, object]] = []
    subtotals_by_group: dict[tuple[str, str], list[dict[str, object]]] = {}
    for entry in subtotal_rows:
        key = (str(entry["asset_category"]), str(entry["symbol"]))
        subtotals_by_group.setdefault(key, []).append(entry)

    for (asset_category, symbol), group_entries in subtotals_by_group.items():
        non_eur_rows = [item for item in group_entries if str(item["currency"]).upper() != "EUR"]
        eur_rows = [item for item in group_entries if str(item["currency"]).upper() == "EUR"]

        selected_subtotals.extend(non_eur_rows)

        eur_expected = symbol_agg.get((asset_category, "EUR", symbol))
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
            add_failure(
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

    selected_totals: list[dict[str, object]] = []
    totals_by_asset: dict[str, list[dict[str, object]]] = {}
    for entry in total_rows:
        totals_by_asset.setdefault(str(entry["asset_category"]), []).append(entry)

    for asset_category, group_entries in totals_by_asset.items():
        non_eur_rows = [item for item in group_entries if str(item["currency"]).upper() != "EUR"]
        eur_rows = [item for item in group_entries if str(item["currency"]).upper() == "EUR"]

        selected_totals.extend(non_eur_rows)

        eur_expected = asset_agg.get((asset_category, "EUR"))
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
            add_failure(
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

    for entry in selected_subtotals:
        row_number = int(entry["row_number"])
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        symbol = str(entry["symbol"])
        agg = symbol_agg.get((asset_category, currency, symbol))
        if agg is None:
            add_failure(
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

        checked_subtotals += 1
        row_fail_before = len(failures)
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
                add_failure(
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
            add_failure(
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

        set_sanity_extras(
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

    for entry in selected_totals:
        row_number = int(entry["row_number"])
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        agg = asset_agg.get((asset_category, currency))
        if agg is None:
            add_failure(
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

        checked_totals += 1
        row_fail_before = len(failures)
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
                add_failure(
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
            add_failure(
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

        set_sanity_extras(
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
        extras_map = sanity_extras_by_row.get(row_idx, {})
        extras = [extras_map.get(col, "") for col in ADDED_TRADES_COLUMNS]
        failures_for_row = row_failure_reasons.get(row_number, [])
        if failures_for_row:
            debug_status = "FAIL"
        elif row_idx in sanity_extras_by_row:
            debug_status = "PASS"
        else:
            debug_status = ""
        debug_kind = sanity_row_kind_by_row.get(row_idx, row_type)
        sanity_output_rows.append(
            padded
            + extras
            + [
                debug_status,
                debug_kind,
                " | ".join(failures_for_row),
            ]
        )

    with debug_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(sanity_output_rows)

    report_data = {
        "passed": len(failures) == 0,
        "checked_closing_trades": checked_trade_rows,
        "checked_closedlots": checked_closedlots,
        "checked_subtotals": checked_subtotals,
        "checked_totals": checked_totals,
        "forex_ignored_rows": forex_ignored_rows,
        "debug_csv_path": str(debug_csv_path),
        "failures_count": len(failures),
        "failures": [failure.to_dict() for failure in failures],
        "note": "Debug sanity artifacts are verification-only and not production tax outputs.",
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return _SanityCheckResult(
        passed=len(failures) == 0,
        checked_closing_trades=checked_trade_rows,
        checked_closedlots=checked_closedlots,
        checked_subtotals=checked_subtotals,
        checked_totals=checked_totals,
        forex_ignored_rows=forex_ignored_rows,
        debug_dir=debug_dir,
        debug_csv_path=debug_csv_path,
        report_path=report_path,
        failures=failures,
    )


def _sum_bucket(bucket: BucketTotals, sale_price_eur: Decimal, purchase_eur: Decimal, pnl_eur: Decimal) -> None:
    bucket.sale_price_eur += sale_price_eur
    bucket.purchase_eur += purchase_eur

    if pnl_eur > 0:
        bucket.wins_eur += pnl_eur
    elif pnl_eur < 0:
        bucket.losses_eur += -pnl_eur
    bucket.rows += 1


def _build_declaration_text(result: AnalysisResult) -> str:
    summary = result.summary
    app5 = summary.appendix_5
    app13 = summary.appendix_13
    review = summary.review
    manual_check_reasons: list[str] = []
    if summary.sanity_failures_count > 0:
        manual_check_reasons.append(f"sanity checks failed: {summary.sanity_failures_count}")
    if summary.review_required_rows > 0:
        manual_check_reasons.append(f"има {summary.review_required_rows} записа с изисквана ръчна проверка")
    if summary.unknown_review_status_rows > 0:
        values = ", ".join(sorted(summary.unknown_review_status_values)) or "-"
        manual_check_reasons.append(
            f"има {summary.unknown_review_status_rows} записа с непознат Review Status ({values})"
        )
    if summary.forex_ignored_rows > 0:
        manual_check_reasons.append(f"има {summary.forex_ignored_rows} Forex записа, които са изключени")
    manual_check_required = bool(manual_check_reasons)

    lines: list[str] = []
    if manual_check_required:
        lines.append("!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!")
        lines.append("СТАТУС: REQUIRED")
        for reason in manual_check_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("ПРОВЕРКА НА ИЗЧИСЛЕНИЯТА")
    lines.append(f"- Sanity checks: {'PASS' if summary.sanity_passed else 'FAIL'}")
    lines.append(f"- Проверени Trade редове (entry + exit): {summary.sanity_checked_closing_trades}")
    lines.append(f"- Проверени ClosedLot редове: {summary.sanity_checked_closedlots}")
    lines.append(f"- Проверени SubTotal редове: {summary.sanity_checked_subtotals}")
    lines.append(f"- Проверени Total редове: {summary.sanity_checked_totals}")
    lines.append(f"- Игнорирани Forex редове: {summary.sanity_forex_ignored_rows}")
    if summary.sanity_forex_ignored_rows > 0:
        lines.append("- ВНИМАНИЕ: Forex операциите не са включени в sanity проверките, защото са игнорирани от анализатора в тази версия.")
    if summary.sanity_debug_artifacts_dir:
        lines.append(f"- Sanity-check debug artifacts path: {summary.sanity_debug_artifacts_dir}")
        lines.append("- Debug artifacts are verification-only and not production tax outputs.")
    if summary.sanity_report_path:
        lines.append(f"- Sanity report: {summary.sanity_report_path}")
    if summary.sanity_failure_messages:
        lines.append("- Sanity diagnostics:")
        for item in summary.sanity_failure_messages[:20]:
            lines.append(f"  {item}")
    lines.append("")

    lines.append("Приложение 5")
    lines.append("Таблица 2")
    lines.append(f"- продажна цена (EUR) - код 508: {_fmt(app5.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- цена на придобиване (EUR) - код 508: {_fmt(app5.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- печалба (EUR) - код 508: {_fmt(app5.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR) - код 508: {_fmt(app5.losses_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- нетен резултат (EUR): {_fmt(app5.wins_eur - app5.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {app5.rows}")
    lines.append("")
    lines.append("Приложение 13")
    lines.append("Част ІІ")
    lines.append(f"- Брутен размер на дохода (EUR) - код 5081: {_fmt(app13.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Цена на придобиване (EUR) - код 5081: {_fmt(app13.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- печалба (EUR): {_fmt(app13.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR): {_fmt(app13.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- нетен резултат (EUR): {_fmt(app13.wins_eur - app13.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {app13.rows}")
    lines.append("")

    if summary.tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
        lines.append("РЪЧНА ПРОВЕРКА (ИЗКЛЮЧЕНИ ОТ АВТОМАТИЧНИТЕ ТАБЛИЦИ)")
        lines.append(f"- изключени записи: {summary.review_rows}")
        lines.append(f"- продажна цена (EUR): {_fmt(review.sale_price_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- цена на придобиване (EUR): {_fmt(review.purchase_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- печалба (EUR): {_fmt(review.wins_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- загуба (EUR): {_fmt(review.losses_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- нетен резултат (EUR): {_fmt(review.wins_eur - review.losses_eur, quant=DECIMAL_TWO)}")
        lines.append("")
        for entry in summary.review_entries:
            lines.append(
                "- row={row} symbol={symbol} date={dt} listing={listing} execution={execution} "
                "reason={reason} proceeds_eur={proceeds} basis_eur={basis} pnl_eur={pnl}".format(
                    row=entry.row_number,
                    symbol=entry.symbol,
                    dt=entry.trade_date,
                    listing=entry.listing_exchange,
                    execution=entry.execution_exchange,
                    reason=entry.reason,
                    proceeds=_fmt(entry.proceeds_eur, quant=DECIMAL_TWO),
                    basis=_fmt(entry.basis_eur, quant=DECIMAL_TWO),
                    pnl=_fmt(entry.pnl_eur, quant=DECIMAL_TWO),
                )
            )
        lines.append("")

    lines.append("ВНИМАНИЕ: FOREX ОПЕРАЦИИ")
    lines.append("- Forex сделки (конвертиране на валута или търговия) НЕ са включени в изчисленията за Приложение 5 и Приложение 13")
    lines.append("- Тези операции са игнорирани от анализатора в тази версия")
    lines.append("- При наличие на значителни Forex операции е необходима ръчна проверка")
    lines.append(f"- брой Forex записи: {summary.forex_ignored_rows}")
    lines.append(f"- общ обем (EUR): {_fmt(summary.forex_ignored_abs_proceeds_eur, quant=DECIMAL_TWO)}")
    lines.append("")

    lines.append("Доказателствена част")
    lines.append(f"- избран режим: {summary.tax_exempt_mode}")
    lines.append(f"- report alias: {result.report_alias or '-'}")
    lines.append(f"- данъчна година: {summary.tax_year}")
    lines.append(f"- обработени сделки (в данъчната година): {summary.processed_trades_in_tax_year}")
    lines.append(f"- сделки извън данъчната година: {summary.trades_outside_tax_year}")
    lines.append(f"- игнорирани редове без token C: {summary.ignored_non_closing_trade_rows}")
    lines.append(f"- review overrides (TAXABLE/NON-TAXABLE): {summary.review_status_overrides_rows}")
    lines.append(f"- unknown Review Status rows: {summary.unknown_review_status_rows}")
    if summary.unknown_review_status_values:
        lines.append(f"- unknown Review Status values: {', '.join(sorted(summary.unknown_review_status_values))}")
    lines.append(f"- използвани execution борси: {', '.join(sorted(summary.exchanges_used)) or '-'}")
    lines.append(f"- review execution борси: {', '.join(sorted(summary.review_exchanges)) or '-'}")
    lines.append("")

    if summary.warnings:
        lines.append("Warnings")
        for warning in summary.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _parse_instrument_listings(rows: list[list[str]]) -> dict[str, InstrumentListing]:
    active_headers, seen_headers = _build_active_headers(rows)
    return _parse_instrument_listings_with_headers(
        rows,
        active_headers=active_headers,
        seen_headers=seen_headers,
    )


def _parse_instrument_listings_with_headers(
    rows: list[list[str]],
    *,
    active_headers: dict[int, _ActiveHeader],
    seen_headers: set[str],
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

        asset_idx = _index_for(active_header.headers, "Asset Category", section_name=f"{section_name} header at row {active_header.row_number}")
        symbol_idx = _index_for(active_header.headers, "Symbol", section_name=f"{section_name} header at row {active_header.row_number}")
        listing_idx = _index_for(active_header.headers, "Listing Exch", section_name=f"{section_name} header at row {active_header.row_number}")

        data = row[2:] + [""] * (len(active_header.headers) - len(row[2:]))
        asset_category = data[asset_idx].strip()
        if asset_category not in SUPPORTED_ASSET_CATEGORIES:
            continue
        raw_symbol = data[symbol_idx].strip()
        symbols = _split_symbol_aliases(raw_symbol)
        if not symbols:
            raise CsvStructureError(f"row {row_number}: empty symbol in Financial Instrument Information")

        listing_exchange = data[listing_idx].strip()
        listing_exchange_normalized = _normalize_exchange(listing_exchange)
        listing_class = _classify_exchange(listing_exchange)
        is_eu_listed = listing_class == EXCHANGE_CLASS_EU_REGULATED

        for symbol in symbols:
            new_item = InstrumentListing(
                symbol=symbol,
                listing_exchange=listing_exchange,
                listing_exchange_normalized=listing_exchange_normalized,
                listing_exchange_class=listing_class,
                is_eu_listed=is_eu_listed,
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
            # Same EU/non-EU classification, keep first mapping deterministically.

    if section_name not in seen_headers:
        raise CsvStructureError(f"missing section header: {section_name}")
    if not listings:
        raise CsvStructureError("Financial Instrument Information section has no supported symbol mappings")
    return listings


def analyze_ibkr_activity_statement(
    *,
    input_csv: str | Path,
    tax_year: int,
    tax_exempt_mode: Literal["listed_symbol", "execution_exchange"],
    report_alias: str | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    fx_rate_provider: FxRateProvider | None = None,
) -> AnalysisResult:
    if tax_year < 2009 or tax_year > 2100:
        raise IbkrAnalyzerError(f"invalid tax year: {tax_year}")

    if tax_exempt_mode not in {TAX_MODE_LISTED_SYMBOL, TAX_MODE_EXECUTION_EXCHANGE}:
        raise IbkrAnalyzerError(f"unsupported tax exempt mode: {tax_exempt_mode}")

    input_path = Path(input_csv).expanduser().resolve()
    if not input_path.exists():
        raise IbkrAnalyzerError(f"input CSV does not exist: {input_path}")
    normalized_alias = _normalize_report_alias(report_alias)

    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    fx_provider = fx_rate_provider if fx_rate_provider is not None else _default_fx_provider(cache_dir)

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise CsvStructureError("empty CSV input")

    active_headers, seen_headers = _build_active_headers(rows)
    listings = _parse_instrument_listings_with_headers(
        rows,
        active_headers=active_headers,
        seen_headers=seen_headers,
    )
    trades_row_extras: dict[int, list[str]] = {}
    trades_row_base_len: dict[int, int] = {}
    summary = AnalysisSummary(tax_year=tax_year, tax_exempt_mode=tax_exempt_mode)

    def _set_trade_extras(row_idx: int, values: dict[str, str]) -> None:
        extras = [""] * len(ADDED_TRADES_COLUMNS)
        for key, value in values.items():
            extras[ADDED_TRADES_COLUMNS.index(key)] = value
        trades_row_extras[row_idx] = extras

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
            current_trades_header = _activate_header("Trades", row, row_number=row_number)
            seen_trades_header = True
            trades_row_base_len[row_idx] = 2 + len(current_trades_header.headers)
            continue

        if current_trades_header is None:
            raise CsvStructureError(f"row {row_number}: Trades row encountered before Trades Header")

        trades_row_base_len[row_idx] = 2 + len(current_trades_header.headers)
        if row_type != "Data":
            continue

        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            raise CsvStructureError(f"row {row_number}: Trades Data row encountered before Trades Header")
        current_trades_header = active_trades_header
        trades_row_base_len[row_idx] = 2 + len(active_trades_header.headers)
        field_idx = _trade_indexes(active_trades_header)

        found_trade_section_data = True

        padded = row + [""] * (trades_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]
        summary.trades_data_rows_total += 1

        discriminator = data[field_idx.discriminator].strip()
        lowered = discriminator.lower()
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
        execution_exchange_norm = _normalize_exchange(execution_exchange_raw)
        execution_exchange_class = _classify_exchange(execution_exchange_raw)

        summary.exchanges_used.add(execution_exchange_norm or "<EMPTY>")
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

        closedlot_indices: list[int] = []
        scan_idx = row_idx + 1
        while scan_idx < len(rows):
            scan_row = rows[scan_idx]
            if len(scan_row) < 2 or scan_row[0] != "Trades" or scan_row[1] != "Data":
                break
            scan_header = active_headers.get(scan_idx)
            if scan_header is None:
                raise CsvStructureError(f"row {scan_idx + 1}: Trades Data row encountered before Trades Header")
            trades_row_base_len[scan_idx] = 2 + len(scan_header.headers)
            scan_idxes = _trade_indexes(scan_header)
            padded_scan = scan_row + [""] * (trades_row_base_len[scan_idx] - len(scan_row))
            scan_data = padded_scan[2 : 2 + len(scan_header.headers)]
            scan_discriminator = scan_data[scan_idxes.discriminator].strip()
            if scan_discriminator.lower() != "closedlot":
                break
            closedlot_indices.append(scan_idx)
            scan_idx += 1

        if _is_forex_asset(asset_category):
            summary.forex_ignored_rows += 1
            summary.forex_ignored_abs_proceeds_eur += abs(proceeds_eur)
            for closed_idx in closedlot_indices:
                consumed_closedlots.add(closed_idx)

            forex_values: dict[str, str] = {
                "Fx Rate": _fmt(trade_fx_rate, quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(commission_eur, quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(proceeds_eur, quant=DECIMAL_EIGHT),
                "Tax Exempt Mode": tax_exempt_mode,
                "Appendix Target": APPENDIX_IGNORED,
                "Tax Treatment Reason": "Forex ignored (not included in Appendix 5/13)",
                "Review Required": "NO",
            }
            if trade_basis_eur_from_trade is not None:
                forex_values["Basis (EUR)"] = _fmt(trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
            if realized_pl_eur is not None:
                forex_values["Realized P/L (EUR)"] = _fmt(realized_pl_eur, quant=DECIMAL_EIGHT)
            _set_trade_extras(
                row_idx,
                forex_values,
            )
            continue

        if not _is_supported_asset(asset_category):
            raise IbkrAnalyzerError(
                f"Unsupported Asset Category encountered: {asset_category}. Review required before using analyzer."
            )

        if not is_closing_trade:
            summary.ignored_non_closing_trade_rows += 1
            non_closing_values: dict[str, str] = {
                "Fx Rate": _fmt(trade_fx_rate, quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(commission_eur, quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(proceeds_eur, quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
                "Tax Exempt Mode": tax_exempt_mode,
                "Tax Treatment Reason": "Non-closing Trade row (informational only)",
                "Review Required": "NO",
            }
            if trade_basis_eur_from_trade is not None:
                non_closing_values["Basis (EUR)"] = _fmt(trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
            _set_trade_extras(row_idx, non_closing_values)
            continue

        summary.closing_trade_candidates += 1

        # ClosedLot rows must be an immediate sequence following Trade row.
        if not closedlot_indices:
            raise IbkrAnalyzerError(f"row {row_number}: no ClosedLot rows attached to closing Trade")

        closedlot_basis_eur_sum = ZERO
        closedlot_basis_original_sum = ZERO
        for closed_idx in closedlot_indices:
            closed_row_number = closed_idx + 1
            closed_row = rows[closed_idx]
            closed_header = active_headers.get(closed_idx)
            if closed_header is None:
                raise CsvStructureError(f"row {closed_row_number}: Trades Data row encountered before Trades Header")
            closed_idxes = _trade_indexes(closed_header)
            trades_row_base_len[closed_idx] = 2 + len(closed_header.headers)
            padded_closed = closed_row + [""] * (trades_row_base_len[closed_idx] - len(closed_row))
            closed_data = padded_closed[2 : 2 + len(closed_header.headers)]
            if closed_idxes.basis is None:
                raise CsvStructureError(
                    f"Trades header at row {closed_header.row_number}: missing required column; "
                    "expected one of ('Basis', 'Cost Basis', 'CostBasis')"
                )
            closed_basis_raw = closed_data[closed_idxes.basis]
            closed_basis = _parse_decimal(closed_basis_raw, row_number=closed_row_number, field_name="Basis")
            closed_dt = _parse_closedlot_date(closed_data[closed_idxes.date_time], row_number=closed_row_number)
            closed_currency = closed_data[closed_idxes.currency].strip().upper() or currency
            closed_basis_eur, closed_fx_rate = _to_eur(
                closed_basis,
                closed_currency,
                closed_dt,
                fx_provider,
                row_number=closed_row_number,
            )
            closedlot_basis_eur_sum += closed_basis_eur
            closedlot_basis_original_sum += closed_basis
            consumed_closedlots.add(closed_idx)
            _set_trade_extras(
                closed_idx,
                {
                    "Fx Rate": _fmt(closed_fx_rate, quant=DECIMAL_EIGHT),
                    "Basis (EUR)": _fmt(closed_basis_eur, quant=DECIMAL_EIGHT),
                },
            )

        trade_basis_eur = -closedlot_basis_eur_sum

        cash_leg_eur = proceeds_eur + commission_eur
        if cash_leg_eur >= ZERO:
            sale_price_component_eur = abs(cash_leg_eur)
            purchase_component_eur = abs(trade_basis_eur)
        else:
            sale_price_component_eur = abs(trade_basis_eur)
            purchase_component_eur = abs(cash_leg_eur)
        pnl_eur = proceeds_eur + trade_basis_eur + commission_eur

        pnl_win = pnl_eur if pnl_eur > 0 else ZERO
        pnl_loss = -pnl_eur if pnl_eur < 0 else ZERO

        instrument, normalized_symbol, forced_review_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        missing_symbol_mapping = instrument is None
        listing_exchange = instrument.listing_exchange_normalized if instrument is not None else ""
        symbol_is_eu_listed: bool | None = None if instrument is None else instrument.is_eu_listed

        appendix_target, reason, review_required = _resolve_tax_target(
            tax_exempt_mode=tax_exempt_mode,
            symbol_is_eu_listed=symbol_is_eu_listed,
            execution_exchange_class=execution_exchange_class,
            missing_symbol_mapping=missing_symbol_mapping,
            forced_review_reason=forced_review_reason,
        )

        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)
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

        review_notes = ""
        if review_required:
            summary.review_required_rows += 1
            if not review_notes_parts:
                review_notes_parts.append("Review required by tax mode rules")
            review_notes = "; ".join(review_notes_parts)
            summary.warnings.append(
                f"row {row_number}: {reason} (symbol={symbol}, execution_exchange={execution_exchange_norm or '<EMPTY>'})"
            )
            logger.warning(
                "row %s marked REVIEW_REQUIRED: %s (symbol=%s, execution_exchange=%s)",
                row_number,
                reason,
                symbol,
                execution_exchange_norm or "<EMPTY>",
            )
        elif review_notes_parts:
            review_notes = "; ".join(review_notes_parts)

        if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL and symbol_is_eu_listed:
            if execution_exchange_class in {EXCHANGE_CLASS_EU_NON_REGULATED, EXCHANGE_CLASS_UNKNOWN}:
                warning = (
                    f"row {row_number}: execution exchange {execution_exchange_norm or '<EMPTY>'} "
                    "is informational only in listed_symbol mode"
                )
                summary.warnings.append(warning)
                logger.warning("%s", warning)

        in_tax_year = trade_date.year == tax_year
        if in_tax_year:
            summary.processed_trades_in_tax_year += 1
            if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE and appendix_target == APPENDIX_REVIEW:
                summary.review_rows += 1
                summary.review_exchanges.add(execution_exchange_norm or "<EMPTY>")
                _sum_bucket(summary.review, sale_price_component_eur, purchase_component_eur, pnl_eur)
                summary.review_entries.append(
                    ReviewEntry(
                        row_number=row_number,
                        symbol=symbol,
                        trade_date=trade_date.isoformat(),
                        listing_exchange=listing_exchange or "<MISSING>",
                        execution_exchange=execution_exchange_norm or "<EMPTY>",
                        reason=reason,
                        proceeds_eur=proceeds_eur,
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
            row_idx,
            {
                "Fx Rate": _fmt(trade_fx_rate, quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(commission_eur, quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(proceeds_eur, quant=DECIMAL_EIGHT),
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
                "Execution Exchange Classification": execution_exchange_class,
                "Tax Exempt Mode": tax_exempt_mode,
                "Appendix Target": appendix_target,
                "Tax Treatment Reason": reason,
                "Review Required": "YES" if review_required else "NO",
                "Review Notes": review_notes,
            },
        )

    if not seen_trades_header:
        raise CsvStructureError("missing section header: Trades")
    if not found_trade_section_data:
        raise CsvStructureError("Trades section has no Data rows")

    # Populate EUR columns for Trades SubTotal/Total rows in the production CSV
    # using the same methodology as sanity aggregation over Trade rows.
    aggregate_col_idx = {
        "comm": ADDED_TRADES_COLUMNS.index("Comm/Fee (EUR)"),
        "proceeds": ADDED_TRADES_COLUMNS.index("Proceeds (EUR)"),
        "basis": ADDED_TRADES_COLUMNS.index("Basis (EUR)"),
        "sale_price": ADDED_TRADES_COLUMNS.index("Sale Price (EUR)"),
        "purchase_price": ADDED_TRADES_COLUMNS.index("Purchase Price (EUR)"),
        "realized": ADDED_TRADES_COLUMNS.index("Realized P/L (EUR)"),
    }
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]] = {}

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

    def _row_distance_to_expected_for_output(entry: dict[str, object], expected: dict[str, Decimal]) -> Decimal:
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
                key=lambda item: _row_distance_to_expected_for_output(item, expected_eur),
            )
            selected_subtotals_for_output.append(best_eur)

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
                key=lambda item: _row_distance_to_expected_for_output(item, expected_eur),
            )
            selected_totals_for_output.append(best_eur)

    for entry in selected_subtotals_for_output:
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        symbol = str(entry["symbol"])
        agg = symbol_agg_eur.get((asset_category, currency, symbol))
        if agg is None:
            continue
        _set_trade_extras(
            int(entry["row_idx"]),
            {
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

    for entry in selected_totals_for_output:
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        agg = asset_agg_eur.get((asset_category, currency))
        if agg is None:
            continue
        _set_trade_extras(
            int(entry["row_idx"]),
            {
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

    output_rows: list[list[str]] = []
    for idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Trades":
            output_rows.append(row)
            continue

        if row[1] == "Header":
            output_rows.append(row + ADDED_TRADES_COLUMNS)
            continue

        base_len = trades_row_base_len.get(idx)
        if base_len is None:
            raise CsvStructureError(f"row {idx + 1}: Trades row encountered before Trades Header")
        padded = row + [""] * (base_len - len(row))
        extras = trades_row_extras.get(idx, [""] * len(ADDED_TRADES_COLUMNS))
        output_rows.append(padded + extras)

    for idx, row in enumerate(output_rows):
        if len(row) >= 2 and row[0] == "Trades":
            base_len = trades_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Trades row encountered before Trades Header")
            expected_len = base_len + len(ADDED_TRADES_COLUMNS)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Trades row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )

    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    output_csv_path = out_dir / f"ibkr_activity{alias_suffix}_modified_{tax_year}.csv"
    declaration_txt_path = out_dir / f"ibkr_activity{alias_suffix}_declaration_{tax_year}.txt"

    with output_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(output_rows)

    sanity = _run_sanity_checks(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        output_dir=out_dir,
        normalized_alias=normalized_alias,
        tax_year=tax_year,
    )
    summary.sanity_passed = sanity.passed
    summary.sanity_checked_closing_trades = sanity.checked_closing_trades
    summary.sanity_checked_closedlots = sanity.checked_closedlots
    summary.sanity_checked_subtotals = sanity.checked_subtotals
    summary.sanity_checked_totals = sanity.checked_totals
    summary.sanity_forex_ignored_rows = sanity.forex_ignored_rows
    summary.sanity_debug_artifacts_dir = str(sanity.debug_dir)
    summary.sanity_debug_csv_path = str(sanity.debug_csv_path)
    summary.sanity_report_path = str(sanity.report_path)
    summary.sanity_failures_count = len(sanity.failures)
    summary.sanity_failure_messages = [failure.to_message() for failure in sanity.failures[:50]]

    result = AnalysisResult(
        input_csv_path=input_path,
        output_csv_path=output_csv_path,
        declaration_txt_path=declaration_txt_path,
        report_alias=normalized_alias,
        summary=summary,
    )

    declaration_txt_path.write_text(_build_declaration_text(result), encoding="utf-8")
    if not sanity.passed:
        report_exists = sanity.report_path.exists()
        debug_exists = sanity.debug_csv_path.exists()
        raise IbkrAnalyzerError(
            "SANITY CHECKS FAILED: {count} issues.\n"
            "Sanity report: {report} (exists={report_exists})\n"
            "Sanity debug CSV: {debug} (exists={debug_exists})".format(
                count=len(sanity.failures),
                report=sanity.report_path,
                debug=sanity.debug_csv_path,
                report_exists=str(report_exists).lower(),
                debug_exists=str(debug_exists).lower(),
            )
        )

    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ibkr-activity-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="IBKR Activity Statement CSV")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
    parser.add_argument(
        "--tax-exempt-mode",
        choices=[TAX_MODE_LISTED_SYMBOL, TAX_MODE_EXECUTION_EXCHANGE],
        required=True,
        help="Tax exempt classification mode",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: output/ibkr/activity_statement)",
    )
    parser.add_argument(
        "--report-alias",
        help="Optional report alias to include in output filenames (for multiple accounts)",
    )
    parser.add_argument("--cache-dir", type=Path, help="Optional bnb_fx cache dir override")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        result = analyze_ibkr_activity_statement(
            input_csv=args.input,
            tax_year=args.tax_year,
            tax_exempt_mode=args.tax_exempt_mode,
            report_alias=args.report_alias,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except IbkrAnalyzerError as exc:
        logger.error("%s", exc)
        return 2

    summary = result.summary
    print(f"processed_rows: {summary.processed_trades_in_tax_year}")
    print(f"ignored_rows: {summary.ignored_non_closing_trade_rows + summary.forex_ignored_rows + summary.trades_outside_tax_year}")
    print(f"trades_data_rows_total: {summary.trades_data_rows_total}")
    print(f"trade_discriminator_rows: {summary.trade_discriminator_rows}")
    print(f"closedlot_discriminator_rows: {summary.closedlot_discriminator_rows}")
    print(f"order_discriminator_rows: {summary.order_discriminator_rows}")
    print(f"closing_trade_candidates: {summary.closing_trade_candidates}")
    print(f"forex_ignored_rows: {summary.forex_ignored_rows}")
    print(f"ignored_non_closing_trade_rows: {summary.ignored_non_closing_trade_rows}")
    print(f"trades_outside_tax_year: {summary.trades_outside_tax_year}")
    print(f"appendix_5_rows: {summary.appendix_5.rows}")
    print(f"appendix_13_rows: {summary.appendix_13.rows}")
    print(f"review_rows: {summary.review_rows}")
    print(f"review_status_overrides_rows: {summary.review_status_overrides_rows}")
    print(f"unknown_review_status_rows: {summary.unknown_review_status_rows}")
    if summary.unknown_review_status_values:
        print(f"unknown_review_status_values: {', '.join(sorted(summary.unknown_review_status_values))}")
    print(f"appendix_5_profit_eur: {_fmt(summary.appendix_5.wins_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_5_loss_eur: {_fmt(summary.appendix_5.losses_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_13_profit_eur: {_fmt(summary.appendix_13.wins_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_13_loss_eur: {_fmt(summary.appendix_13.losses_eur, quant=DECIMAL_TWO)}")
    print(f"review_profit_eur: {_fmt(summary.review.wins_eur, quant=DECIMAL_TWO)}")
    print(f"review_loss_eur: {_fmt(summary.review.losses_eur, quant=DECIMAL_TWO)}")
    print("SANITY CHECKS PASSED" if summary.sanity_passed else "SANITY CHECKS FAILED")
    print(f"sanity_checks_passed: {'YES' if summary.sanity_passed else 'NO'}")
    print(f"sanity_checked_trade_rows: {summary.sanity_checked_closing_trades}")
    print(f"sanity_checked_closedlots: {summary.sanity_checked_closedlots}")
    print(f"sanity_checked_subtotals: {summary.sanity_checked_subtotals}")
    print(f"sanity_checked_totals: {summary.sanity_checked_totals}")
    print(f"sanity_forex_ignored_rows: {summary.sanity_forex_ignored_rows}")
    print(f"Modified CSV: {result.output_csv_path}")
    print(f"Declaration TXT: {result.declaration_txt_path}")
    print(f"Sanity-check debug artifacts written to: {summary.sanity_debug_artifacts_dir}")
    print("These are verification artifacts, not production tax outputs.")
    if summary.warnings:
        print("Warnings:")
        for warning in summary.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
