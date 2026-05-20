from __future__ import annotations

from dataclasses import dataclass

from ..appendices.declaration_text import _sum_bucket
from ..constants import ZERO
from ..models import AnalysisSummary, CsvStructureError, _ActiveHeader
from ..shared import (
    IbkrReportDateFormat,
    _activate_header,
    _index_for,
    _is_interest_total_row,
    _parse_decimal,
    _parse_interest_date,
    _to_eur,
)


@dataclass(slots=True)
class FeesSectionResult:
    processed_rows: int = 0


@dataclass(slots=True)
class _FeesFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int


def is_cfd_interest_fee(description: str) -> bool:
    normalized = description.strip().lower()
    return "cfd interest" in normalized or "cfd financing" in normalized


def _fees_indexes(active_header: _ActiveHeader) -> _FeesFieldIndexes:
    section_name = f"Fees header at row {active_header.row_number}"
    return _FeesFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
    )


def process_fees_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    summary: AnalysisSummary,
    fx_provider,
    tax_year: int,
    report_date_format: IbkrReportDateFormat,
    net_cfd_financing: bool,
) -> FeesSectionResult:
    current_fees_header: _ActiveHeader | None = None
    processed_rows = 0

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Fees":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_fees_header = _activate_header("Fees", row, row_number=row_number)
            continue

        if current_fees_header is None:
            raise CsvStructureError(f"row {row_number}: Fees row encountered before Fees Header")
        if row_type != "Data":
            continue

        active_fees_header = active_headers.get(row_idx)
        if active_fees_header is None:
            raise CsvStructureError(f"row {row_number}: Fees Data row encountered before Fees Header")
        current_fees_header = active_fees_header

        field_idx = _fees_indexes(active_fees_header)
        data = row[2:] + [""] * (len(active_fees_header.headers) - len(row[2:]))
        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            continue

        description = data[field_idx.description].strip()
        if not is_cfd_interest_fee(description):
            continue

        fee_date = _parse_interest_date(
            data[field_idx.date],
            row_number=row_number,
            report_date_format=report_date_format,
            field_name="Fees date",
        )
        amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        processed_rows += 1
        summary.cfd_financing_rows += 1

        if fee_date.year != tax_year:
            continue

        amount_eur, _ = _to_eur(
            amount,
            currency,
            fee_date,
            fx_provider,
            row_number=row_number,
        )
        if amount_eur > ZERO:
            summary.cfd_financing_positive_eur += amount_eur
            if net_cfd_financing:
                _sum_bucket(summary.appendix_5, amount_eur, ZERO, amount_eur)
            else:
                summary.appendix_6_positive_cfd_financing_eur += amount_eur
                summary.appendix_6_code_606_eur += amount_eur
            continue

        if amount_eur < ZERO:
            negative_abs = -amount_eur
            summary.cfd_financing_negative_eur += negative_abs
            if net_cfd_financing:
                _sum_bucket(summary.appendix_5, ZERO, negative_abs, amount_eur)
            else:
                summary.cfd_financing_negative_skipped_eur += negative_abs

    return FeesSectionResult(processed_rows=processed_rows)


__all__ = [name for name in globals() if not name.startswith("__")]
