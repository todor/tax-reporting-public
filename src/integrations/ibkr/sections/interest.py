from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..appendices.aggregations import _country_component
from ..constants import (
    ADDED_INTEREST_COLUMNS,
    DECIMAL_EIGHT,
    INTEREST_DECLARED_TYPES,
    INTEREST_STATUS_NON_TAXABLE,
    INTEREST_STATUS_TAXABLE,
    INTEREST_STATUS_UNKNOWN,
    REVIEW_STATUS_NON_TAXABLE,
    REVIEW_STATUS_TAXABLE,
    ZERO,
)
from ..models import (
    AnalysisSummary,
    Appendix9CountryTotals,
    CsvStructureError,
    _ActiveHeader,
    _CountryCreditComponent,
)
from ..shared import (
    _activate_header,
    _fmt,
    _index_for,
    _is_interest_total_row,
    _normalize_review_status,
    _optional_index,
    _parse_decimal,
    _parse_interest_date,
    _to_eur,
)
from .income import (
    _appendix9_default_country,
    _classify_interest_type,
    _extract_period_key_from_description,
    _normalize_interest_type,
)


@dataclass(slots=True)
class InterestSectionResult:
    row_extras: dict[int, list[str]]
    row_base_len: dict[int, int]
    components_by_country: dict[str, dict[str, _CountryCreditComponent]]


@dataclass(slots=True)
class _InterestFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int
    review_status: int | None


def _interest_indexes(active_header: _ActiveHeader) -> _InterestFieldIndexes:
    section_name = f"Interest header at row {active_header.row_number}"
    return _InterestFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def process_interest_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    summary: AnalysisSummary,
    fx_provider,
    tax_year: int,
) -> InterestSectionResult:
    interest_row_extras: dict[int, list[str]] = {}
    interest_row_base_len: dict[int, int] = {}

    def set_interest_extras(row_idx: int, values: dict[str, str]) -> None:
        extras = [""] * len(ADDED_INTEREST_COLUMNS)
        for key, value in values.items():
            extras[ADDED_INTEREST_COLUMNS.index(key)] = value
        interest_row_extras[row_idx] = extras

    appendix9_components: dict[str, dict[str, _CountryCreditComponent]] = {}

    def appendix9_bucket(country_iso: str, country_english: str, country_bulgarian: str):
        bucket = summary.appendix_9_by_country.get(country_iso)
        if bucket is None:
            bucket = Appendix9CountryTotals(
                country_iso=country_iso,
                country_english=country_english,
                country_bulgarian=country_bulgarian,
            )
            summary.appendix_9_by_country[country_iso] = bucket
        return bucket

    current_interest_header: _ActiveHeader | None = None
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Interest":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_interest_header = _activate_header("Interest", row, row_number=row_number)
            interest_row_base_len[row_idx] = 2 + len(current_interest_header.headers)
            continue

        if current_interest_header is None:
            raise CsvStructureError(f"row {row_number}: Interest row encountered before Interest Header")
        interest_row_base_len[row_idx] = 2 + len(current_interest_header.headers)
        if row_type != "Data":
            continue

        active_interest_header = active_headers.get(row_idx)
        if active_interest_header is None:
            raise CsvStructureError(f"row {row_number}: Interest Data row encountered before Interest Header")
        current_interest_header = active_interest_header
        interest_row_base_len[row_idx] = 2 + len(active_interest_header.headers)

        field_idx = _interest_indexes(active_interest_header)
        padded = row + [""] * (interest_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_interest_header.headers)]

        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            summary.interest_total_rows_skipped += 1
            continue

        summary.interest_processed_rows += 1
        interest_date = _parse_interest_date(data[field_idx.date], row_number=row_number)
        description = data[field_idx.description].strip()
        amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        normalized_type = _normalize_interest_type(description, currency=currency)
        status = _classify_interest_type(normalized_type)
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)
        if review_status_normalized == REVIEW_STATUS_TAXABLE:
            if status != INTEREST_STATUS_TAXABLE:
                summary.review_status_overrides_rows += 1
            status = INTEREST_STATUS_TAXABLE
        elif review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
            if status != INTEREST_STATUS_NON_TAXABLE:
                summary.review_status_overrides_rows += 1
            status = INTEREST_STATUS_NON_TAXABLE
        elif review_status_normalized in {"UNKNOWN", "REVIEW-REQUIRED"}:
            status = INTEREST_STATUS_UNKNOWN
        elif review_status_normalized != "":
            status = INTEREST_STATUS_UNKNOWN
            summary.unknown_review_status_rows += 1
            summary.unknown_review_status_values.add(review_status_normalized)
            summary.warnings.append(
                f"row {row_number}: unknown Review Status={review_status_normalized} (interest description={description!r})"
            )

        amount_eur_text = ""
        if status == INTEREST_STATUS_TAXABLE:
            summary.interest_taxable_rows += 1
            if interest_date.year == tax_year:
                amount_eur, _ = _to_eur(
                    amount,
                    currency,
                    interest_date,
                    fx_provider,
                    row_number=row_number,
                )
                amount_eur_text = _fmt(amount_eur, quant=DECIMAL_EIGHT)
                if normalized_type == "Credit Interest":
                    summary.appendix_9_credit_interest_eur += amount_eur
                    summary.appendix_6_credit_interest_eur += amount_eur
                    country_iso, country_english, country_bulgarian = _appendix9_default_country()
                    b = appendix9_bucket(country_iso, country_english, country_bulgarian)
                    b.gross_interest_eur += amount_eur
                    period_key = _extract_period_key_from_description(
                        description,
                        fallback=f"INTEREST_ROW_{row_number}",
                    )
                    _country_component(
                        appendix9_components,
                        country_iso=country_iso,
                        component_key=period_key,
                    ).gross_eur += amount_eur
                elif normalized_type == "IBKR Managed Securities (SYEP) Interest":
                    summary.appendix_6_syep_interest_eur += amount_eur
                else:
                    summary.appendix_6_other_taxable_eur += amount_eur
        elif status == INTEREST_STATUS_NON_TAXABLE:
            summary.interest_non_taxable_rows += 1
        else:
            summary.interest_unknown_rows += 1
            summary.review_required_rows += 1
            normalized_display = normalized_type or "<EMPTY>"
            if normalized_display not in INTEREST_DECLARED_TYPES | {"Debit Interest", "Borrow Fees"}:
                summary.interest_unknown_types.add(normalized_display)
                summary.interest_unknown_descriptions.append(description)
                summary.warnings.append(
                    f"row {row_number}: unknown interest type={normalized_display} (description={description!r})"
                )

        set_interest_extras(
            row_idx,
            {
                "Amount (EUR)": amount_eur_text,
                "Status": status,
            },
        )

    return InterestSectionResult(
        row_extras=interest_row_extras,
        row_base_len=interest_row_base_len,
        components_by_country=appendix9_components,
    )


def extract_interest_withholding_paid_eur(
    rows: list[list[str]],
    *,
    active_headers: dict[int, _ActiveHeader],
) -> tuple[Decimal, bool]:
    section_name = "Mark-to-Market Performance Summary"
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != section_name or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            raise CsvStructureError(f"row {row_number}: {section_name} Data row encountered before {section_name} Header")

        section_label = f"{section_name} header at row {active_header.row_number}"
        asset_idx = _index_for(active_header.headers, "Asset Category", section_name=section_label)
        total_idx = _index_for(active_header.headers, "Mark-to-Market P/L Total", section_name=section_label)
        padded = row[2:] + [""] * (len(active_header.headers) - len(row[2:]))
        asset_category = padded[asset_idx].strip()
        if asset_category != "Withholding on Interest Received":
            continue
        value = _parse_decimal(padded[total_idx], row_number=row_number, field_name="Mark-to-Market P/L Total")
        return abs(value), True
    return ZERO, False
