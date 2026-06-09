from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re

from ..appendices.declaration_text import _sum_bucket
from ..constants import (
    ADDED_FEES_COLUMNS,
    APPENDIX_5,
    APPENDIX_IGNORED,
    CFD_FINANCING_MODE_ALWAYS_NET,
    CFD_FINANCING_MODE_IGNORE,
    DECIMAL_EIGHT,
    NEGATIVE_PIL_STATUS_DEFER,
    NEGATIVE_PIL_STATUS_IGNORE,
    NEGATIVE_PIL_STATUS_NET,
    NEGATIVE_PIL_STATUS_REVIEW,
    NEGATIVE_PIL_STATUSES,
    ZERO,
)
from ..models import AnalysisSummary, CfdFinancingDecision, CsvStructureError, _ActiveHeader
from ..shared import (
    IbkrReportDateFormat,
    _activate_header,
    _fmt,
    _index_for,
    _is_interest_total_row,
    _optional_index,
    _parse_decimal,
    _parse_interest_date,
    _to_eur,
)
from .negative_pil import NegativePilExposureRange, _status_from_candidates


_CFD_FINANCING_DATE_RE = re.compile(r"\bfor\s+(\d{1,2})-([A-Z]{3})-(\d{4})\b", re.IGNORECASE)
_IBKR_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(slots=True)
class FeesSectionResult:
    processed_rows: int = 0
    row_extras: dict[int, dict[str, str]] = field(default_factory=dict)
    row_base_len: dict[int, int] = field(default_factory=dict)
    row_added_columns: dict[int, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class _FeesFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int
    review_status: int | None


def is_cfd_interest_fee(description: str) -> bool:
    normalized = description.strip().lower()
    return "cfd" in normalized


def _fees_indexes(active_header: _ActiveHeader) -> _FeesFieldIndexes:
    section_name = f"Fees header at row {active_header.row_number}"
    return _FeesFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


@dataclass(slots=True)
class _CfdFinancingAutoDecision:
    auto_status: str
    tax_status: str
    candidate_ranges: list[NegativePilExposureRange]
    assignment_status: str


def _parse_embedded_cfd_financing_date(description: str) -> tuple[date | None, str]:
    match = _CFD_FINANCING_DATE_RE.search(description)
    if match is None:
        return None, "missing embedded financing date in format DD-MMM-YYYY"
    day_text, month_text, year_text = match.groups()
    month = _IBKR_MONTHS.get(month_text.upper())
    if month is None:
        return None, f"invalid embedded financing month {month_text!r}"
    try:
        return date(int(year_text), month, int(day_text)), ""
    except ValueError as exc:
        return None, f"invalid embedded financing date {match.group(0)!r}: {exc}"


def _cfd_financing_candidates_for_date(
    *,
    ranges: list[NegativePilExposureRange],
    fee_date,
) -> list[NegativePilExposureRange]:
    seen: set[tuple[str, str, object, object, tuple[int, ...], str]] = set()
    candidates: list[NegativePilExposureRange] = []
    for candidate in ranges:
        if not candidate.contains(fee_date):
            continue
        key = (candidate.kind, candidate.symbol, candidate.start, candidate.end, candidate.source_rows, candidate.source)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _decide_cfd_financing_auto_status(
    *,
    fee_date,
    mode: str,
    tax_year: int,
    exposure_ranges: list[NegativePilExposureRange],
) -> _CfdFinancingAutoDecision:
    candidates = _cfd_financing_candidates_for_date(ranges=exposure_ranges, fee_date=fee_date)
    assignment_status = (
        "matched_by_trade_date_approximation"
        if candidates
        else "unmatched_by_trade_date_approximation"
    )
    if fee_date.year != tax_year:
        return _CfdFinancingAutoDecision(
            auto_status=NEGATIVE_PIL_STATUS_IGNORE,
            tax_status=(
                "Excluded from CFD taxable result because the Fees Date is outside the selected tax year."
            ),
            candidate_ranges=candidates,
            assignment_status=assignment_status,
        )
    if mode == CFD_FINANCING_MODE_ALWAYS_NET:
        return _CfdFinancingAutoDecision(
            auto_status=NEGATIVE_PIL_STATUS_NET,
            tax_status="Included in CFD taxable result because CFD financing mode is always-net.",
            candidate_ranges=candidates,
            assignment_status=assignment_status,
        )
    if mode == CFD_FINANCING_MODE_IGNORE:
        return _CfdFinancingAutoDecision(
            auto_status=NEGATIVE_PIL_STATUS_IGNORE,
            tax_status="Excluded from CFD taxable result because CFD financing mode is ignore.",
            candidate_ranges=candidates,
            assignment_status=assignment_status,
        )
    if not candidates:
        return _CfdFinancingAutoDecision(
            auto_status=NEGATIVE_PIL_STATUS_NET,
            tax_status=(
                "Included in CFD taxable result: IBKR-reported CFD financing fee accepted but "
                "not matched to a trade-date-open CFD position by the approximate assignment "
                "check. This can happen because IBKR may use settlement/value-date, overnight, "
                "weekend, holiday, or posting-date logic not visible in standard Trades rows."
            ),
            candidate_ranges=[],
            assignment_status=assignment_status,
        )
    auto_status, tax_status = _status_from_candidates(
        candidates=candidates,
        tax_year=tax_year,
        net_message=(
            "Included in CFD taxable result: all CFD positions active on the financing date "
            "were closed by year-end."
        ),
        defer_message=(
            "Deferred: all CFD positions active on the financing date remained open at year-end."
        ),
        review_none_message="",
        review_mixed_message=(
            "Manual review: CFD financing overlaps both closed and open CFD positions; "
            "IBKR does not provide instrument-level allocation."
        ),
    )
    return _CfdFinancingAutoDecision(
        auto_status=auto_status,
        tax_status=tax_status,
        candidate_ranges=candidates,
        assignment_status=assignment_status,
    )


def _normalize_cfd_financing_review_status(raw: str) -> str:
    return raw.strip().upper()


def _resolve_cfd_financing_final_status(
    summary: AnalysisSummary,
    *,
    row_number: int,
    review_status_raw: str,
    auto_status: str,
    description: str,
) -> tuple[str, str]:
    review_status = _normalize_cfd_financing_review_status(review_status_raw)
    if review_status == "":
        return auto_status, ""
    if review_status in NEGATIVE_PIL_STATUSES:
        summary.review_status_overrides_rows += 1
        return review_status, review_status
    summary.unknown_review_status_rows += 1
    summary.unknown_review_status_values.add(review_status)
    summary.review_required_rows += 1
    summary.warnings.append(
        f"row {row_number}: invalid CFD financing Review Status={review_status!r}; "
        f"expected one of {sorted(NEGATIVE_PIL_STATUSES)} (description={description!r})"
    )
    return NEGATIVE_PIL_STATUS_REVIEW, review_status


def _apply_cfd_financing_amount(
    *,
    summary: AnalysisSummary,
    amount_eur,
    final_status: str,
) -> str:
    amount_abs = abs(amount_eur)
    if amount_eur > ZERO:
        summary.cfd_financing_positive_eur += amount_eur
    elif amount_eur < ZERO:
        summary.cfd_financing_negative_eur += -amount_eur
    if final_status == NEGATIVE_PIL_STATUS_NET:
        summary.cfd_financing_net_rows += 1
        summary.cfd_financing_netted_eur += amount_abs
        if amount_eur > ZERO:
            _sum_bucket(summary.appendix_5, amount_eur, ZERO, amount_eur, count_row=False)
        elif amount_eur < ZERO:
            _sum_bucket(summary.appendix_5, ZERO, -amount_eur, amount_eur, count_row=False)
        return APPENDIX_5
    if final_status == NEGATIVE_PIL_STATUS_DEFER:
        summary.cfd_financing_defer_rows += 1
        summary.cfd_financing_deferred_eur += amount_abs
        if amount_eur < ZERO:
            summary.cfd_financing_negative_skipped_eur += -amount_eur
        return APPENDIX_IGNORED
    if final_status == NEGATIVE_PIL_STATUS_IGNORE:
        summary.cfd_financing_ignore_rows += 1
        summary.cfd_financing_ignored_eur += amount_abs
        if amount_eur < ZERO:
            summary.cfd_financing_negative_skipped_eur += -amount_eur
        return APPENDIX_IGNORED
    summary.cfd_financing_review_rows += 1
    summary.cfd_financing_review_eur += amount_abs
    if amount_eur < ZERO:
        summary.cfd_financing_negative_skipped_eur += -amount_eur
    return APPENDIX_IGNORED


def process_fees_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    summary: AnalysisSummary,
    fx_provider,
    tax_year: int,
    report_date_format: IbkrReportDateFormat,
    cfd_financing_mode: str,
    cfd_financing_exposure_ranges: list[NegativePilExposureRange],
) -> FeesSectionResult:
    current_fees_header: _ActiveHeader | None = None
    processed_rows = 0
    row_extras: dict[int, dict[str, str]] = {}
    row_base_len: dict[int, int] = {}
    row_added_columns: dict[int, list[str]] = {}

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Fees":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_fees_header = _activate_header("Fees", row, row_number=row_number)
            row_base_len[row_idx] = 2 + len(current_fees_header.headers)
            row_added_columns[row_idx] = [col for col in ADDED_FEES_COLUMNS if col not in current_fees_header.headers]
            continue

        if current_fees_header is None:
            raise CsvStructureError(f"row {row_number}: Fees row encountered before Fees Header")
        row_base_len[row_idx] = 2 + len(current_fees_header.headers)
        if row_type != "Data":
            continue

        active_fees_header = active_headers.get(row_idx)
        if active_fees_header is None:
            raise CsvStructureError(f"row {row_number}: Fees Data row encountered before Fees Header")
        current_fees_header = active_fees_header
        row_base_len[row_idx] = 2 + len(active_fees_header.headers)
        row_added_columns[row_idx] = [col for col in ADDED_FEES_COLUMNS if col not in active_fees_header.headers]

        field_idx = _fees_indexes(active_fees_header)
        data = row[2:] + [""] * (len(active_fees_header.headers) - len(row[2:]))
        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            continue

        description = data[field_idx.description].strip()
        if not is_cfd_interest_fee(description):
            continue
        summary.cfd_financing_detected_rows += 1

        row_date = _parse_interest_date(
            data[field_idx.date],
            row_number=row_number,
            report_date_format=report_date_format,
            field_name="Fees date",
        )
        amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        embedded_fee_date, embedded_date_error = _parse_embedded_cfd_financing_date(description)
        fee_date = row_date
        tax_year_scope = "IN_TAX_YEAR" if fee_date.year == tax_year else "OUTSIDE_TAX_YEAR"
        embedded_date_status = (
            "MATCHES_FEES_DATE"
            if embedded_fee_date == fee_date
            else "DIFFERS_FROM_FEES_DATE"
            if embedded_fee_date is not None
            else f"NOT_FOUND: {embedded_date_error}"
        )

        processed_rows += 1
        summary.cfd_financing_rows += 1
        if fee_date.year != tax_year:
            summary.cfd_financing_outside_tax_year_rows += 1
        amount_eur, _ = _to_eur(
            amount,
            currency,
            fee_date,
            fx_provider,
            row_number=row_number,
        )
        amount_eur_text = _fmt(amount_eur, quant=DECIMAL_EIGHT)
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        auto_decision = _decide_cfd_financing_auto_status(
            fee_date=fee_date,
            mode=cfd_financing_mode,
            tax_year=tax_year,
            exposure_ranges=cfd_financing_exposure_ranges,
        )
        if auto_decision.assignment_status == "matched_by_trade_date_approximation":
            summary.cfd_financing_matched_rows += 1
        elif auto_decision.assignment_status == "unmatched_by_trade_date_approximation":
            summary.cfd_financing_unmatched_by_trade_date_rows += 1
        final_status, review_status = _resolve_cfd_financing_final_status(
            summary,
            row_number=row_number,
            review_status_raw=review_status_raw,
            auto_status=auto_decision.auto_status,
            description=description,
        )
        tax_treatment_reason = auto_decision.tax_status
        if final_status != auto_decision.auto_status and review_status in NEGATIVE_PIL_STATUSES:
            tax_treatment_reason = (
                f"User override applied from Review Status: final status is {final_status}. "
                f"Auto decision was {auto_decision.auto_status}: {auto_decision.tax_status}"
            )

        if amount_eur == ZERO:
            appendix_target = APPENDIX_IGNORED
            reason = "Zero CFD financing"
        else:
            appendix_target = _apply_cfd_financing_amount(
                summary=summary,
                amount_eur=amount_eur,
                final_status=final_status,
            )
            reason = tax_treatment_reason

        summary.cfd_financing_decisions.append(
            CfdFinancingDecision(
                row_number=row_number,
                date=fee_date,
                currency=currency,
                amount=amount,
                amount_eur=amount_eur,
                description=description,
                candidate_ranges=[candidate.format() for candidate in auto_decision.candidate_ranges],
                assignment_status=auto_decision.assignment_status,
                embedded_fee_date=embedded_fee_date.isoformat() if embedded_fee_date is not None else "",
                embedded_fee_date_status=embedded_date_status,
                auto_status=auto_decision.auto_status,
                review_status=review_status,
                final_status=final_status,
                tax_status=tax_treatment_reason,
            )
        )
        row_extras[row_idx] = {
            "Amount (EUR)": amount_eur_text,
            "Appendix Target": appendix_target,
            "Tax Treatment Reason": reason,
            "Tax Year Scope": tax_year_scope,
            "Review Status": review_status_raw,
            "Auto Status": auto_decision.auto_status,
            "Tax Status": final_status,
        }

    return FeesSectionResult(
        processed_rows=processed_rows,
        row_extras=row_extras,
        row_base_len=row_base_len,
        row_added_columns=row_added_columns,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
