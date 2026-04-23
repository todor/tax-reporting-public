from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .constants import APPENDIX8_LIST_MODE_COMPANY, DIVIDEND_TAX_RATE, ZERO

class IbkrAnalyzerError(Exception):
    """Base error for IBKR analyzer failures."""


class CsvStructureError(IbkrAnalyzerError):
    """Raised when required sections/columns are missing."""


class FxConversionError(IbkrAnalyzerError):
    """Raised when FX conversion cannot be performed."""


@dataclass(slots=True)
class InstrumentListing:
    symbol: str
    canonical_symbol: str
    listing_exchange: str
    listing_exchange_normalized: str
    listing_exchange_class: str
    is_eu_listed: bool
    description: str
    isin: str


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
class Appendix8CountryTotals:
    country_iso: str
    country_english: str
    country_bulgarian: str
    gross_dividend_eur: Decimal = ZERO
    withholding_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8CompanyTotals:
    country_iso: str
    country_english: str
    country_bulgarian: str
    company_name: str
    gross_dividend_eur: Decimal = ZERO
    withholding_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8ComputedRow:
    payer_name: str
    country_iso: str
    country_english: str
    country_bulgarian: str
    method_code: str
    gross_dividend_eur: Decimal = ZERO
    foreign_tax_paid_eur: Decimal = ZERO
    bulgarian_tax_eur: Decimal = ZERO
    allowable_credit_eur: Decimal = ZERO
    recognized_credit_eur: Decimal = ZERO
    tax_due_bg_eur: Decimal = ZERO
    company_rows_count: int = 1


@dataclass(slots=True)
class Appendix8CountryDebugComputed:
    country_iso: str
    country_english: str
    country_bulgarian: str
    aggregated_gross_eur: Decimal = ZERO
    aggregated_foreign_tax_paid_eur: Decimal = ZERO
    bulgarian_tax_aggregated_eur: Decimal = ZERO
    credit_correct_eur: Decimal = ZERO
    credit_wrong_rowwise_eur: Decimal = ZERO
    delta_correct_minus_rowwise_eur: Decimal = ZERO
    tax_due_correct_eur: Decimal = ZERO
    tax_due_wrong_rowwise_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8Part1Row:
    country_iso: str
    country_english: str
    country_bulgarian: str
    quantity: Decimal = ZERO
    acquisition_date: date = date.min
    cost_basis_original: Decimal = ZERO
    cost_basis_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix9CountryTotals:
    country_iso: str
    country_english: str
    country_bulgarian: str
    gross_interest_eur: Decimal = ZERO
    withholding_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix9CountryComputed:
    country_iso: str
    country_english: str
    country_bulgarian: str
    aggregated_gross_eur: Decimal = ZERO
    aggregated_foreign_tax_paid_eur: Decimal = ZERO
    allowable_credit_aggregated_eur: Decimal = ZERO
    recognized_credit_correct_eur: Decimal = ZERO
    recognized_credit_wrong_rowwise_eur: Decimal = ZERO
    delta_correct_minus_rowwise_eur: Decimal = ZERO


@dataclass(slots=True)
class _CountryCreditComponent:
    gross_eur: Decimal = ZERO
    foreign_tax_paid_eur: Decimal = ZERO


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
    forex_non_taxable_ignored_rows: int = 0
    forex_review_required_rows: int = 0
    forex_ignored_abs_proceeds_eur: Decimal = ZERO
    ignored_non_closing_trade_rows: int = 0
    review_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    exchange_classification_mode: str = ""
    cli_eu_regulated_overrides: set[str] = field(default_factory=set)
    encountered_eu_regulated_exchanges: set[str] = field(default_factory=set)
    encountered_eu_non_regulated_exchanges: set[str] = field(default_factory=set)
    encountered_non_eu_exchanges: set[str] = field(default_factory=set)
    encountered_unmapped_exchanges: set[str] = field(default_factory=set)
    encountered_invalid_exchange_values: set[str] = field(default_factory=set)
    exchanges_used: set[str] = field(default_factory=set)
    review_exchanges: set[str] = field(default_factory=set)
    review_entries: list[ReviewEntry] = field(default_factory=list)
    review_required_rows: int = 0
    review_status_overrides_rows: int = 0
    unknown_review_status_rows: int = 0
    unknown_review_status_values: set[str] = field(default_factory=set)
    interest_processed_rows: int = 0
    interest_total_rows_skipped: int = 0
    interest_taxable_rows: int = 0
    interest_non_taxable_rows: int = 0
    interest_unknown_rows: int = 0
    interest_unknown_types: set[str] = field(default_factory=set)
    interest_unknown_descriptions: list[str] = field(default_factory=list)
    appendix_6_code_603_eur: Decimal = ZERO
    appendix_6_credit_interest_eur: Decimal = ZERO
    appendix_6_syep_interest_eur: Decimal = ZERO
    appendix_6_other_taxable_eur: Decimal = ZERO
    appendix_9_credit_interest_eur: Decimal = ZERO
    appendix_9_withholding_paid_eur: Decimal = ZERO
    appendix_9_withholding_source_found: bool = False
    appendix_9_by_country: dict[str, Appendix9CountryTotals] = field(default_factory=dict)
    appendix_9_country_results: dict[str, Appendix9CountryComputed] = field(default_factory=dict)
    appendix_6_lieu_received_eur: Decimal = ZERO
    dividend_tax_rate: Decimal = DIVIDEND_TAX_RATE
    dividends_processed_rows: int = 0
    dividends_total_rows_skipped: int = 0
    dividends_cash_rows: int = 0
    dividends_lieu_rows: int = 0
    dividends_unknown_rows: int = 0
    dividends_country_errors_rows: int = 0
    withholding_processed_rows: int = 0
    withholding_total_rows_skipped: int = 0
    withholding_dividend_rows: int = 0
    withholding_non_dividend_rows: int = 0
    withholding_country_errors_rows: int = 0
    appendix8_dividend_list_mode: str = APPENDIX8_LIST_MODE_COMPANY
    appendix_8_by_country: dict[str, Appendix8CountryTotals] = field(default_factory=dict)
    appendix_8_by_company: dict[tuple[str, str], Appendix8CompanyTotals] = field(default_factory=dict)
    appendix_8_company_results: list[Appendix8ComputedRow] = field(default_factory=list)
    appendix_8_output_rows: list[Appendix8ComputedRow] = field(default_factory=list)
    appendix_8_country_debug: dict[str, Appendix8CountryDebugComputed] = field(default_factory=dict)
    appendix_8_part1_rows: list[Appendix8Part1Row] = field(default_factory=list)
    open_positions_summary_rows: int = 0
    open_positions_part1_rows: int = 0
    tax_credit_debug_report_path: str = ""
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


__all__ = [name for name in globals() if not name.startswith("__")]
