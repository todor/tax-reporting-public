from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..constants import (
    ADDED_DIVIDENDS_COLUMNS,
    DECIMAL_EIGHT,
    DIVIDEND_APPENDIX_6,
    DIVIDEND_APPENDIX_8,
    DIVIDEND_APPENDIX_UNKNOWN,
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
    _classify_dividend_description,
    _classify_status_from_description,
    _extract_isin,
    _resolve_country_from_isin,
    _resolve_country_from_text,
    _resolve_dividend_company_name,
)


@dataclass(slots=True)
class DividendsSectionResult:
    row_extras: dict[int, dict[str, str]]
    row_base_len: dict[int, int]
    row_added_columns: dict[int, list[str]]


@dataclass(slots=True)
class _DividendsFieldIndexes:
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


def _dividends_indexes(active_header: _ActiveHeader) -> _DividendsFieldIndexes:
    section_name = f"Dividends header at row {active_header.row_number}"
    return _DividendsFieldIndexes(
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


def _set_dividends_extras(
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


def _apply_review_status_override(
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
    summary.unknown_review_status_values.add(review_status_normalized)
    summary.warnings.append(
        f"row {row_number}: unknown Review Status={review_status_normalized} (dividend description={description!r})"
    )
    summary.review_required_rows += 1
    return INTEREST_STATUS_UNKNOWN


def _resolve_auto_dividend_fields(
    *,
    summary: AnalysisSummary,
    auto_appendix: str,
    description: str,
    amount,
    currency: str,
    dividend_date,
    fx_provider,
    row_number: int,
) -> tuple[str, str, Decimal | None, str]:
    auto_isin = ""
    auto_country_english = ""
    auto_amount_eur = None
    auto_amount_eur_text = ""

    if auto_appendix == DIVIDEND_APPENDIX_UNKNOWN:
        return auto_isin, auto_country_english, auto_amount_eur, auto_amount_eur_text

    auto_isin_value, auto_isin_error = _extract_isin(description)
    if auto_isin_error is not None or auto_isin_value is None:
        summary.dividends_country_errors_rows += 1
        summary.review_required_rows += 1
        summary.warnings.append(
            f"row {row_number}: {auto_isin_error or 'missing ISIN'} for dividend description={description!r}"
        )
        return auto_isin, auto_country_english, auto_amount_eur, auto_amount_eur_text

    auto_isin = auto_isin_value
    auto_country_info = _resolve_country_from_isin(auto_isin_value)
    if auto_country_info is None:
        summary.dividends_country_errors_rows += 1
        summary.review_required_rows += 1
        summary.warnings.append(
            f"row {row_number}: unknown ISIN country code={auto_isin_value[:2]} for dividend description={description!r}"
        )
        return auto_isin, auto_country_english, auto_amount_eur, auto_amount_eur_text

    _, auto_country_english, _ = auto_country_info
    auto_amount_eur, _ = _to_eur(
        amount,
        currency,
        dividend_date,
        fx_provider,
        row_number=row_number,
    )
    auto_amount_eur_text = _fmt(auto_amount_eur, quant=DECIMAL_EIGHT)
    return auto_isin, auto_country_english, auto_amount_eur, auto_amount_eur_text


def _effective_appendix(
    *,
    manual_appendix: str,
    auto_appendix: str,
    effective_status: str,
    description: str,
) -> str:
    if manual_appendix:
        return manual_appendix
    if auto_appendix != DIVIDEND_APPENDIX_UNKNOWN:
        return auto_appendix
    if effective_status != INTEREST_STATUS_TAXABLE:
        return auto_appendix
    if "lieu received" in description.lower():
        return DIVIDEND_APPENDIX_6
    return DIVIDEND_APPENDIX_8


def _apply_taxable_dividend_totals(
    *,
    summary: AnalysisSummary,
    listings: dict[str, InstrumentListing],
    row_number: int,
    tax_year: int,
    dividend_date,
    description: str,
    effective_status: str,
    effective_appendix: str,
    effective_country_text: str,
    effective_amount_eur: Decimal | None,
) -> None:
    is_taxable = effective_status == INTEREST_STATUS_TAXABLE
    if not is_taxable or dividend_date.year != tax_year or effective_amount_eur is None:
        return

    if effective_appendix == DIVIDEND_APPENDIX_8:
        if effective_country_text == "":
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: taxable dividend row is missing Country (description={description!r})"
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
                f"row {row_number}: dividend company mapping requires review "
                f"(description={description!r}, resolved_company={company_name!r}, reason={company_error})"
            )
        summary.dividends_cash_rows += 1
        _appendix8_country_bucket(
            summary,
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
        ).gross_dividend_eur += effective_amount_eur
        _appendix8_company_bucket(
            summary,
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
            company_name=company_name,
        ).gross_dividend_eur += effective_amount_eur
        return

    if effective_appendix == DIVIDEND_APPENDIX_6:
        summary.dividends_lieu_rows += 1
        summary.appendix_6_lieu_received_eur += effective_amount_eur


def _set_dividends_existing_values(
    *,
    rows: list[list[str]],
    row_idx: int,
    active_dividends_header: _ActiveHeader,
    field_idx: _DividendsFieldIndexes,
    effective_country_text: str,
    effective_amount_eur_text: str,
    effective_isin: str,
    effective_appendix: str,
    effective_status: str,
) -> None:
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_dividends_header,
        field_idx=field_idx.country,
        value=effective_country_text,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_dividends_header,
        field_idx=field_idx.amount_eur,
        value=effective_amount_eur_text,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_dividends_header,
        field_idx=field_idx.isin,
        value=effective_isin,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_dividends_header,
        field_idx=field_idx.appendix,
        value=effective_appendix,
        only_if_empty=True,
    )
    _set_existing_section_value(
        rows=rows,
        row_idx=row_idx,
        active_header=active_dividends_header,
        field_idx=field_idx.status,
        value=effective_status,
        only_if_empty=False,
    )


def process_dividends_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    summary: AnalysisSummary,
    fx_provider,
    tax_year: int,
) -> DividendsSectionResult:
    row_extras: dict[int, dict[str, str]] = {}
    row_base_len: dict[int, int] = {}
    row_added_columns: dict[int, list[str]] = {}

    current_dividends_header: _ActiveHeader | None = None
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Dividends":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_dividends_header = _activate_header("Dividends", row, row_number=row_number)
            row_base_len[row_idx] = 2 + len(current_dividends_header.headers)
            row_added_columns[row_idx] = [
                col for col in ADDED_DIVIDENDS_COLUMNS if col not in current_dividends_header.headers
            ]
            continue

        if current_dividends_header is None:
            raise CsvStructureError(f"row {row_number}: Dividends row encountered before Dividends Header")
        row_base_len[row_idx] = 2 + len(current_dividends_header.headers)
        if row_type != "Data":
            continue

        active_dividends_header = active_headers.get(row_idx)
        if active_dividends_header is None:
            raise CsvStructureError(f"row {row_number}: Dividends Data row encountered before Dividends Header")
        current_dividends_header = active_dividends_header
        row_base_len[row_idx] = 2 + len(active_dividends_header.headers)
        row_added_columns[row_idx] = [
            col for col in ADDED_DIVIDENDS_COLUMNS if col not in active_dividends_header.headers
        ]

        field_idx = _dividends_indexes(active_dividends_header)
        padded = row + [""] * (row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_dividends_header.headers)]
        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            summary.dividends_total_rows_skipped += 1
            continue

        summary.dividends_processed_rows += 1
        dividend_date = _parse_interest_date(data[field_idx.date], row_number=row_number)
        description = data[field_idx.description].strip()
        amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        auto_appendix = _classify_dividend_description(description)
        auto_status = _classify_status_from_description(description)
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)
        manual_country = data[field_idx.country].strip() if field_idx.country is not None else ""
        manual_amount_eur_text = data[field_idx.amount_eur].strip() if field_idx.amount_eur is not None else ""
        manual_amount_eur = _parse_optional_decimal(
            manual_amount_eur_text,
            row_number=row_number,
            field_name="Amount (EUR)",
        )
        manual_isin = data[field_idx.isin].strip() if field_idx.isin is not None else ""
        manual_appendix = data[field_idx.appendix].strip() if field_idx.appendix is not None else ""

        effective_status = _apply_review_status_override(
            summary,
            auto_status=auto_status,
            review_status_normalized=review_status_normalized,
            row_number=row_number,
            description=description,
        )
        auto_isin, auto_country_english, auto_amount_eur, auto_amount_eur_text = _resolve_auto_dividend_fields(
            summary=summary,
            auto_appendix=auto_appendix,
            description=description,
            amount=amount,
            currency=currency,
            dividend_date=dividend_date,
            fx_provider=fx_provider,
            row_number=row_number,
        )

        effective_appendix = _effective_appendix(
            manual_appendix=manual_appendix,
            auto_appendix=auto_appendix,
            effective_status=effective_status,
            description=description,
        )

        effective_country_text = manual_country if manual_country else auto_country_english
        effective_amount_eur = manual_amount_eur if manual_amount_eur is not None else auto_amount_eur
        effective_amount_eur_text = manual_amount_eur_text if manual_amount_eur_text else auto_amount_eur_text
        effective_isin = manual_isin if manual_isin else auto_isin

        if effective_status == INTEREST_STATUS_UNKNOWN:
            summary.dividends_unknown_rows += 1
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: unknown dividend description requires manual review (description={description!r})"
            )

        _apply_taxable_dividend_totals(
            summary=summary,
            listings=listings,
            row_number=row_number,
            tax_year=tax_year,
            dividend_date=dividend_date,
            description=description,
            effective_status=effective_status,
            effective_appendix=effective_appendix,
            effective_country_text=effective_country_text,
            effective_amount_eur=effective_amount_eur,
        )
        _set_dividends_existing_values(
            rows=rows,
            row_idx=row_idx,
            active_dividends_header=active_dividends_header,
            field_idx=field_idx,
            effective_country_text=effective_country_text,
            effective_amount_eur_text=effective_amount_eur_text,
            effective_isin=effective_isin,
            effective_appendix=effective_appendix,
            effective_status=effective_status,
        )

        _set_dividends_extras(
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

    return DividendsSectionResult(
        row_extras=row_extras,
        row_base_len=row_base_len,
        row_added_columns=row_added_columns,
    )
