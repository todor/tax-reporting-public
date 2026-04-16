from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..constants import (
    ADDED_WITHHOLDING_COLUMNS,
    DECIMAL_EIGHT,
    INTEREST_STATUS_NON_TAXABLE,
    INTEREST_STATUS_TAXABLE,
    INTEREST_STATUS_UNKNOWN,
    REVIEW_STATUS_NON_TAXABLE,
    REVIEW_STATUS_TAXABLE,
)
from ..models import (
    AnalysisSummary,
    Appendix8CompanyTotals,
    Appendix8CountryTotals,
    CsvStructureError,
    InstrumentListing,
    _ActiveHeader,
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
    _parse_optional_decimal,
    _set_existing_section_value,
    _to_eur,
)
from .income import (
    _classify_status_from_description,
    _extract_isin,
    _resolve_country_from_isin,
    _resolve_country_from_text,
    _resolve_dividend_company_name,
)


@dataclass(slots=True)
class WithholdingSectionResult:
    row_extras: dict[int, dict[str, str]]
    row_base_len: dict[int, int]
    row_added_columns: dict[int, list[str]]


@dataclass(slots=True)
class _WithholdingFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int
    country: int | None
    amount_eur: int | None
    isin: int | None
    appendix: int | None
    status: int | None
    review_status: int | None


def _withholding_indexes(active_header: _ActiveHeader) -> _WithholdingFieldIndexes:
    section_name = f"Withholding Tax header at row {active_header.row_number}"
    return _WithholdingFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
        country=_optional_index(active_header.headers, "Country"),
        amount_eur=_optional_index(active_header.headers, "Amount (EUR)"),
        isin=_optional_index(active_header.headers, "ISIN"),
        appendix=_optional_index(active_header.headers, "Appendix"),
        status=_optional_index(active_header.headers, "Status"),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _set_withholding_extras(
    row_extras: dict[int, dict[str, str]],
    *,
    row_idx: int,
    values: dict[str, str],
) -> None:
    existing = row_extras.get(row_idx, {})
    for key, value in values.items():
        existing[key] = value
    row_extras[row_idx] = existing


def _appendix8_country_bucket(
    summary: AnalysisSummary,
    *,
    country_iso: str,
    country_english: str,
    country_bulgarian: str,
) -> Appendix8CountryTotals:
    bucket = summary.appendix_8_by_country.get(country_iso)
    if bucket is None:
        bucket = Appendix8CountryTotals(
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
        )
        summary.appendix_8_by_country[country_iso] = bucket
    return bucket


def _appendix8_company_bucket(
    summary: AnalysisSummary,
    *,
    country_iso: str,
    country_english: str,
    country_bulgarian: str,
    company_name: str,
) -> Appendix8CompanyTotals:
    key = (country_iso, company_name)
    bucket = summary.appendix_8_by_company.get(key)
    if bucket is None:
        bucket = Appendix8CompanyTotals(
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
            company_name=company_name,
        )
        summary.appendix_8_by_company[key] = bucket
    return bucket


def _classify_withholding_appendix(summary: AnalysisSummary, *, description: str) -> str:
    lowered = description.lower()
    if "cash dividend" in lowered:
        summary.withholding_dividend_rows += 1
        return "Appendix 8"
    summary.withholding_non_dividend_rows += 1
    if "credit interest" in lowered:
        return "Appendix 9"
    if "lieu received" in lowered:
        return "Appendix 6"
    return ""


def _resolve_withholding_auto_fields(
    *,
    summary: AnalysisSummary,
    description: str,
    auto_appendix: str,
    currency: str,
    tax_date,
    tax_amount,
    fx_provider,
    row_number: int,
) -> tuple[str, str, Decimal | None, str]:
    auto_country_text = ""
    auto_isin = ""
    auto_amount_eur = None
    auto_amount_eur_text = ""

    if auto_appendix == "Appendix 8":
        isin, isin_error = _extract_isin(description)
        if isin_error is not None or isin is None:
            summary.withholding_country_errors_rows += 1
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: {isin_error or 'missing ISIN'} for withholding description={description!r}"
            )
        else:
            country_info = _resolve_country_from_isin(isin)
            if country_info is None:
                summary.withholding_country_errors_rows += 1
                summary.review_required_rows += 1
                summary.warnings.append(
                    f"row {row_number}: unknown ISIN country code={isin[:2]} for withholding description={description!r}"
                )
            else:
                _, country_english, _ = country_info
                auto_country_text = country_english
                auto_isin = isin
    elif auto_appendix == "Appendix 9":
        auto_country_text = "Ireland"
        auto_isin = ""

    if auto_appendix in {"Appendix 8", "Appendix 9", "Appendix 6"}:
        tax_amount_eur, _ = _to_eur(
            tax_amount,
            currency,
            tax_date,
            fx_provider,
            row_number=row_number,
        )
        auto_amount_eur = tax_amount_eur
        auto_amount_eur_text = _fmt(tax_amount_eur, quant=DECIMAL_EIGHT)

    return auto_country_text, auto_isin, auto_amount_eur, auto_amount_eur_text


def _apply_withholding_review_status(
    summary: AnalysisSummary,
    *,
    auto_status: str,
    review_status_normalized: str,
    row_number: int,
    description: str,
) -> str:
    effective_status = auto_status
    if review_status_normalized == REVIEW_STATUS_TAXABLE:
        summary.review_status_overrides_rows += 1
        return INTEREST_STATUS_TAXABLE
    if review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
        summary.review_status_overrides_rows += 1
        return INTEREST_STATUS_NON_TAXABLE
    if review_status_normalized == "":
        return effective_status

    summary.unknown_review_status_rows += 1
    summary.review_required_rows += 1
    summary.unknown_review_status_values.add(review_status_normalized)
    summary.warnings.append(
        f"row {row_number}: unknown Review Status={review_status_normalized} (withholding description={description!r})"
    )
    return INTEREST_STATUS_UNKNOWN


def _set_withholding_existing_values(
    *,
    rows: list[list[str]],
    row_idx: int,
    active_withholding_header: _ActiveHeader,
    field_idx: _WithholdingFieldIndexes,
    effective_country_text: str,
    effective_amount_eur_text: str,
    effective_isin: str,
    effective_appendix: str,
    effective_status: str,
) -> None:
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_withholding_header,
        field_idx=field_idx.country,
        value=effective_country_text,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_withholding_header,
        field_idx=field_idx.amount_eur,
        value=effective_amount_eur_text,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_withholding_header,
        field_idx=field_idx.isin,
        value=effective_isin,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_withholding_header,
        field_idx=field_idx.appendix,
        value=effective_appendix,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_withholding_header,
        field_idx=field_idx.status,
        value=effective_status,
        only_if_empty=False,
    )


def _apply_taxable_withholding_totals(
    *,
    summary: AnalysisSummary,
    listings: dict[str, InstrumentListing],
    row_number: int,
    tax_year: int,
    description: str,
    tax_date,
    effective_status: str,
    effective_appendix: str,
    effective_country_text: str,
    effective_amount_eur: Decimal | None,
) -> None:
    is_taxable = effective_status == INTEREST_STATUS_TAXABLE
    if tax_date.year != tax_year or not is_taxable or effective_amount_eur is None:
        return

    if effective_appendix == "Appendix 8":
        if effective_country_text == "":
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: taxable withholding row is missing Country (description={description!r})"
            )
            return

        country_iso, country_english, country_bulgarian = _resolve_country_from_text(effective_country_text)
        company_name, company_error = _resolve_dividend_company_name(
            description=description,
            listings=listings,
        )
        if company_name is None:
            company_name = f"UNKNOWN_PAYER_ROW_{row_number}"
        if company_error is not None:
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: withholding company mapping requires review "
                f"(description={description!r}, resolved_company={company_name!r}, reason={company_error})"
            )
        _appendix8_country_bucket(
            summary,
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
        ).withholding_tax_paid_eur += abs(effective_amount_eur)
        _appendix8_company_bucket(
            summary,
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
            company_name=company_name,
        ).withholding_tax_paid_eur += abs(effective_amount_eur)
        return

    if effective_appendix == "Appendix 9":
        # Appendix 9 paid foreign tax source of truth is Mark-to-Market
        # ("Withholding on Interest Received"). Appendix 9 rows in this
        # section stay informational/enriched and do not drive totals.
        return

    summary.review_required_rows += 1
    summary.warnings.append(
        f"row {row_number}: taxable withholding row has unknown Appendix value={effective_appendix!r}"
    )


def process_withholding_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    summary: AnalysisSummary,
    fx_provider,
    tax_year: int,
) -> WithholdingSectionResult:
    row_extras: dict[int, dict[str, str]] = {}
    row_base_len: dict[int, int] = {}
    row_added_columns: dict[int, list[str]] = {}

    current_withholding_header: _ActiveHeader | None = None
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Withholding Tax":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_withholding_header = _activate_header("Withholding Tax", row, row_number=row_number)
            row_base_len[row_idx] = 2 + len(current_withholding_header.headers)
            row_added_columns[row_idx] = [
                col for col in ADDED_WITHHOLDING_COLUMNS if col not in current_withholding_header.headers
            ]
            continue

        if current_withholding_header is None:
            raise CsvStructureError(f"row {row_number}: Withholding Tax row encountered before Withholding Tax Header")
        row_base_len[row_idx] = 2 + len(current_withholding_header.headers)
        if row_type != "Data":
            continue

        active_withholding_header = active_headers.get(row_idx)
        if active_withholding_header is None:
            raise CsvStructureError(f"row {row_number}: Withholding Tax Data row encountered before Withholding Tax Header")
        current_withholding_header = active_withholding_header
        row_base_len[row_idx] = 2 + len(active_withholding_header.headers)
        row_added_columns[row_idx] = [
            col for col in ADDED_WITHHOLDING_COLUMNS if col not in active_withholding_header.headers
        ]

        field_idx = _withholding_indexes(active_withholding_header)
        padded = row + [""] * (row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_withholding_header.headers)]
        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            summary.withholding_total_rows_skipped += 1
            continue

        summary.withholding_processed_rows += 1
        description = data[field_idx.description].strip()
        auto_status = _classify_status_from_description(description)
        manual_country = data[field_idx.country].strip() if field_idx.country is not None else ""
        manual_amount_eur_text = data[field_idx.amount_eur].strip() if field_idx.amount_eur is not None else ""
        manual_amount_eur = _parse_optional_decimal(
            manual_amount_eur_text,
            row_number=row_number,
            field_name="Amount (EUR)",
        )
        manual_isin = data[field_idx.isin].strip() if field_idx.isin is not None else ""
        manual_appendix = data[field_idx.appendix].strip() if field_idx.appendix is not None else ""
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)

        auto_appendix = _classify_withholding_appendix(summary, description=description)
        tax_date = _parse_interest_date(data[field_idx.date], row_number=row_number)
        tax_amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        auto_country_text, auto_isin, auto_amount_eur, auto_amount_eur_text = _resolve_withholding_auto_fields(
            summary=summary,
            description=description,
            auto_appendix=auto_appendix,
            currency=currency,
            tax_date=tax_date,
            tax_amount=tax_amount,
            fx_provider=fx_provider,
            row_number=row_number,
        )
        effective_status = _apply_withholding_review_status(
            summary,
            auto_status=auto_status,
            review_status_normalized=review_status_normalized,
            row_number=row_number,
            description=description,
        )

        effective_appendix = manual_appendix if manual_appendix else auto_appendix
        effective_country_text = manual_country if manual_country else auto_country_text
        effective_amount_eur = manual_amount_eur if manual_amount_eur is not None else auto_amount_eur
        effective_amount_eur_text = manual_amount_eur_text if manual_amount_eur_text else auto_amount_eur_text
        effective_isin = manual_isin if manual_isin else auto_isin

        if effective_status == INTEREST_STATUS_UNKNOWN:
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: UNKNOWN withholding status requires manual review (description={description!r})"
            )
        _set_withholding_existing_values(
            rows=rows,
            row_idx=row_idx,
            active_withholding_header=active_withholding_header,
            field_idx=field_idx,
            effective_country_text=effective_country_text,
            effective_amount_eur_text=effective_amount_eur_text,
            effective_isin=effective_isin,
            effective_appendix=effective_appendix,
            effective_status=effective_status,
        )

        _set_withholding_extras(
            row_extras,
            row_idx=row_idx,
            values={
                "Country": effective_country_text,
                "Amount (EUR)": effective_amount_eur_text,
                "ISIN": effective_isin,
                "Appendix": effective_appendix,
                "Status": effective_status,
                "Review Status": review_status_raw,
            },
        )

        _apply_taxable_withholding_totals(
            summary=summary,
            listings=listings,
            row_number=row_number,
            tax_year=tax_year,
            description=description,
            tax_date=tax_date,
            effective_status=effective_status,
            effective_appendix=effective_appendix,
            effective_country_text=effective_country_text,
            effective_amount_eur=effective_amount_eur,
        )

    return WithholdingSectionResult(
        row_extras=row_extras,
        row_base_len=row_base_len,
        row_added_columns=row_added_columns,
    )
