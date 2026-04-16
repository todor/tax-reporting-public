from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..constants import (
    APPENDIX8_COUNTRY_MODE_PAYER_LABEL,
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    ZERO,
)
from ..models import (
    Appendix8CompanyTotals,
    Appendix8ComputedRow,
    Appendix8CountryDebugComputed,
    Appendix8Part1Row,
    Appendix9CountryComputed,
    Appendix9CountryTotals,
    _CountryCreditComponent,
)
from ..shared import _fmt


def _country_component(
    components: dict[str, dict[str, _CountryCreditComponent]],
    *,
    country_iso: str,
    component_key: str,
) -> _CountryCreditComponent:
    country_components = components.get(country_iso)
    if country_components is None:
        country_components = {}
        components[country_iso] = country_components
    component = country_components.get(component_key)
    if component is None:
        component = _CountryCreditComponent()
        country_components[component_key] = component
    return component


def _sum_rowwise_wrong_credit(
    components: dict[str, _CountryCreditComponent],
    *,
    rate: Decimal,
) -> Decimal:
    return sum(
        (min(component.foreign_tax_paid_eur, component.gross_eur * rate) for component in components.values()),
        ZERO,
    )


def _determine_appendix8_method_code(*, foreign_withholding_paid_eur: Decimal | None) -> str:
    if foreign_withholding_paid_eur is None or foreign_withholding_paid_eur <= ZERO:
        return "3"
    return "1"


def _compute_appendix8_company_results(
    *,
    totals_by_company: dict[tuple[str, str], Appendix8CompanyTotals],
    dividend_tax_rate: Decimal,
) -> list[Appendix8ComputedRow]:
    results: list[Appendix8ComputedRow] = []
    for _company_key, totals in sorted(
        totals_by_company.items(),
        key=lambda item: (item[1].country_iso, item[1].company_name),
    ):
        gross = totals.gross_dividend_eur
        foreign_tax = totals.withholding_tax_paid_eur
        bulgarian_tax = gross * dividend_tax_rate
        credit_correct = min(foreign_tax, bulgarian_tax)
        method_code = _determine_appendix8_method_code(
            foreign_withholding_paid_eur=foreign_tax,
        )
        results.append(
            Appendix8ComputedRow(
                payer_name=totals.company_name,
                country_iso=totals.country_iso,
                country_english=totals.country_english,
                country_bulgarian=totals.country_bulgarian,
                method_code=method_code,
                gross_dividend_eur=gross,
                foreign_tax_paid_eur=foreign_tax,
                bulgarian_tax_eur=bulgarian_tax,
                allowable_credit_eur=credit_correct,
                recognized_credit_eur=credit_correct,
                tax_due_bg_eur=bulgarian_tax - credit_correct,
                company_rows_count=1,
            )
        )
    return results


def _aggregate_appendix8_company_rows_by_country_and_method(
    *,
    company_rows: list[Appendix8ComputedRow],
) -> list[Appendix8ComputedRow]:
    buckets: dict[tuple[str, str], Appendix8ComputedRow] = {}
    for row in company_rows:
        key = (row.country_iso, row.method_code)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = Appendix8ComputedRow(
                payer_name=APPENDIX8_COUNTRY_MODE_PAYER_LABEL,
                country_iso=row.country_iso,
                country_english=row.country_english,
                country_bulgarian=row.country_bulgarian,
                method_code=row.method_code,
                company_rows_count=0,
            )
            buckets[key] = bucket
        bucket.gross_dividend_eur += row.gross_dividend_eur
        bucket.foreign_tax_paid_eur += row.foreign_tax_paid_eur
        bucket.bulgarian_tax_eur += row.bulgarian_tax_eur
        bucket.allowable_credit_eur += row.allowable_credit_eur
        bucket.recognized_credit_eur += row.recognized_credit_eur
        bucket.tax_due_bg_eur += row.tax_due_bg_eur
        bucket.company_rows_count += row.company_rows_count
    return sorted(buckets.values(), key=lambda item: (item.country_iso, item.method_code))


def _build_appendix8_country_debug(
    *,
    company_rows: list[Appendix8ComputedRow],
    dividend_tax_rate: Decimal,
) -> dict[str, Appendix8CountryDebugComputed]:
    aggregated: dict[str, Appendix8CountryDebugComputed] = {}
    for row in company_rows:
        current = aggregated.get(row.country_iso)
        if current is None:
            current = Appendix8CountryDebugComputed(
                country_iso=row.country_iso,
                country_english=row.country_english,
                country_bulgarian=row.country_bulgarian,
            )
            aggregated[row.country_iso] = current
        current.aggregated_gross_eur += row.gross_dividend_eur
        current.aggregated_foreign_tax_paid_eur += row.foreign_tax_paid_eur
        current.bulgarian_tax_aggregated_eur += row.bulgarian_tax_eur
        current.credit_correct_eur += row.recognized_credit_eur
        current.tax_due_correct_eur += row.tax_due_bg_eur

    for country_iso, current in aggregated.items():
        wrong_credit = min(
            current.aggregated_foreign_tax_paid_eur,
            current.aggregated_gross_eur * dividend_tax_rate,
        )
        current.credit_wrong_rowwise_eur = wrong_credit
        current.delta_correct_minus_rowwise_eur = current.credit_correct_eur - wrong_credit
        current.tax_due_wrong_rowwise_eur = current.bulgarian_tax_aggregated_eur - wrong_credit
        aggregated[country_iso] = current

    return aggregated


def _build_appendix8_part1_rows(
    *,
    totals_by_country: dict[str, Appendix8Part1Row],
) -> list[Appendix8Part1Row]:
    return sorted(
        totals_by_country.values(),
        key=lambda item: item.country_iso,
    )


def _compute_appendix9_country_results(
    *,
    totals_by_country: dict[str, Appendix9CountryTotals],
    components_by_country: dict[str, dict[str, _CountryCreditComponent]],
    allowable_credit_rate: Decimal = APPENDIX_9_ALLOWABLE_CREDIT_RATE,
) -> dict[str, Appendix9CountryComputed]:
    results: dict[str, Appendix9CountryComputed] = {}
    for country_iso, totals in totals_by_country.items():
        gross = totals.gross_interest_eur
        foreign_tax = totals.withholding_tax_paid_eur
        allowable_credit = gross * allowable_credit_rate
        recognized_credit = min(foreign_tax, allowable_credit)
        rowwise_components = components_by_country.get(country_iso, {})
        recognized_wrong_rowwise = _sum_rowwise_wrong_credit(
            rowwise_components,
            rate=allowable_credit_rate,
        )
        results[country_iso] = Appendix9CountryComputed(
            country_iso=country_iso,
            country_english=totals.country_english,
            country_bulgarian=totals.country_bulgarian,
            aggregated_gross_eur=gross,
            aggregated_foreign_tax_paid_eur=foreign_tax,
            allowable_credit_aggregated_eur=allowable_credit,
            recognized_credit_correct_eur=recognized_credit,
            recognized_credit_wrong_rowwise_eur=recognized_wrong_rowwise,
            delta_correct_minus_rowwise_eur=recognized_credit - recognized_wrong_rowwise,
        )
    return results


def _write_tax_credit_debug_report(
    *,
    output_dir: Path,
    normalized_alias: str,
    tax_year: int,
    appendix8_company_rows: list[Appendix8ComputedRow],
    appendix8_country_debug: dict[str, Appendix8CountryDebugComputed],
    appendix8_output_rows: list[Appendix8ComputedRow],
    appendix8_list_mode: str,
    appendix9_results: dict[str, Appendix9CountryComputed],
) -> Path:
    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    debug_dir = output_dir / "_tax_credit_debug" / f"ibkr_activity{alias_suffix}_{tax_year}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    report_path = debug_dir / "tax_credit_country_debug.json"

    payload = {
        "note": "Debug diagnostics only. Not declaration-ready output.",
        "appendix_8_list_mode": appendix8_list_mode,
        "appendix_8_company_rows": [
            {
                "payer_name": item.payer_name,
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "method_code": item.method_code,
                "gross_dividend": _fmt(item.gross_dividend_eur),
                "foreign_tax_paid": _fmt(item.foreign_tax_paid_eur),
                "bulgarian_tax": _fmt(item.bulgarian_tax_eur),
                "allowable_credit": _fmt(item.allowable_credit_eur),
                "recognized_credit": _fmt(item.recognized_credit_eur),
                "tax_due_bg": _fmt(item.tax_due_bg_eur),
            }
            for item in appendix8_company_rows
        ],
        "appendix_8_country_debug": [
            {
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "aggregated_gross": _fmt(item.aggregated_gross_eur),
                "aggregated_foreign_tax_paid": _fmt(item.aggregated_foreign_tax_paid_eur),
                "bulgarian_tax_aggregated": _fmt(item.bulgarian_tax_aggregated_eur),
                "recognized_credit_sum_company": _fmt(item.credit_correct_eur),
                "recognized_credit_wrong_country_recomputed": _fmt(item.credit_wrong_rowwise_eur),
                "delta_correct_minus_wrong_country_recomputed": _fmt(item.delta_correct_minus_rowwise_eur),
                "tax_due_sum_company": _fmt(item.tax_due_correct_eur),
                "tax_due_wrong_country_recomputed": _fmt(item.tax_due_wrong_rowwise_eur),
            }
            for item in sorted(appendix8_country_debug.values(), key=lambda value: value.country_iso)
        ],
        "appendix_8_output_rows": [
            {
                "payer_name": item.payer_name,
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "method_code": item.method_code,
                "gross_dividend": _fmt(item.gross_dividend_eur),
                "foreign_tax_paid": _fmt(item.foreign_tax_paid_eur),
                "allowable_credit": _fmt(item.allowable_credit_eur),
                "recognized_credit": _fmt(item.recognized_credit_eur),
                "tax_due_bg": _fmt(item.tax_due_bg_eur),
                "company_rows_count": item.company_rows_count,
            }
            for item in appendix8_output_rows
        ],
        "appendix_9": [
            {
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "aggregated_gross": _fmt(item.aggregated_gross_eur),
                "aggregated_foreign_tax_paid": _fmt(item.aggregated_foreign_tax_paid_eur),
                "allowable_credit_aggregated": _fmt(item.allowable_credit_aggregated_eur),
                "recognized_credit_correct": _fmt(item.recognized_credit_correct_eur),
                "recognized_credit_wrong_rowwise": _fmt(item.recognized_credit_wrong_rowwise_eur),
                "delta_correct_minus_rowwise": _fmt(item.delta_correct_minus_rowwise_eur),
            }
            for item in sorted(appendix9_results.values(), key=lambda value: value.country_iso)
        ],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


__all__ = [name for name in globals() if not name.startswith("__")]
