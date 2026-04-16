from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .appendices.aggregations import (
    _aggregate_appendix8_company_rows_by_country_and_method,
    _build_appendix8_country_debug,
    _build_appendix8_part1_rows,
    _compute_appendix8_company_results,
    _compute_appendix9_country_results,
    _country_component,
    _write_tax_credit_debug_report,
)
from .appendices.csv_output import build_output_rows, validate_output_rows
from .appendices.declaration_text import _build_declaration_text
from .constants import (
    APPENDIX8_LIST_MODE_COMPANY,
    APPENDIX8_LIST_MODE_COUNTRY,
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    DECIMAL_TWO,
    DEFAULT_OUTPUT_DIR,
    DIVIDEND_TAX_RATE,
    FxRateProvider,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTED_SYMBOL,
    ZERO,
)
from .models import (
    AnalysisResult,
    AnalysisSummary,
    Appendix8Part1Row,
    Appendix9CountryTotals,
    CsvStructureError,
    IbkrAnalyzerError,
    InstrumentListing,
    _ActiveHeader,
    _CountryCreditComponent,
)
from .sections.dividends import DividendsSectionResult, process_dividends_section
from .sections.income import _appendix9_default_country
from .sections.instruments import parse_instrument_listings_with_headers
from .sections.interest import (
    InterestSectionResult,
    extract_interest_withholding_paid_eur,
    process_interest_section,
)
from .sections.open_positions import (
    OpenPositionsSectionResult,
    process_open_positions_section,
    run_open_position_reconciliation,
)
from .sections.sanity import _run_sanity_checks
from .sections.tax_withholding import (
    WithholdingSectionResult,
    process_withholding_section,
)
from .sections.trades import (
    TradesSectionResult,
    populate_trade_aggregate_extras,
    process_trades_section,
)
from .shared import _build_active_headers, _default_fx_provider, _fmt, _normalize_report_alias

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ProcessedSections:
    trades: TradesSectionResult
    interest: InterestSectionResult
    dividends: DividendsSectionResult
    withholding: WithholdingSectionResult
    open_positions: OpenPositionsSectionResult


def _validate_analysis_request(
    *,
    tax_year: int,
    tax_exempt_mode: str,
    appendix8_dividend_list_mode: str,
) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise IbkrAnalyzerError(f"invalid tax year: {tax_year}")

    if tax_exempt_mode not in {TAX_MODE_LISTED_SYMBOL, TAX_MODE_EXECUTION_EXCHANGE}:
        raise IbkrAnalyzerError(f"unsupported tax exempt mode: {tax_exempt_mode}")
    if appendix8_dividend_list_mode not in {
        APPENDIX8_LIST_MODE_COMPANY,
        APPENDIX8_LIST_MODE_COUNTRY,
    }:
        raise IbkrAnalyzerError(
            f"unsupported Appendix 8 dividend list mode: {appendix8_dividend_list_mode}"
        )


def _resolve_input_path(input_csv: str | Path) -> Path:
    input_path = Path(input_csv).expanduser().resolve()
    if not input_path.exists():
        raise IbkrAnalyzerError(f"input CSV does not exist: {input_path}")
    return input_path


def _load_csv_rows(input_path: Path) -> list[list[str]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise CsvStructureError("empty CSV input")
    return rows


def _appendix9_bucket(
    summary: AnalysisSummary,
    *,
    country_iso: str,
    country_english: str,
    country_bulgarian: str,
) -> Appendix9CountryTotals:
    bucket = summary.appendix_9_by_country.get(country_iso)
    if bucket is None:
        bucket = Appendix9CountryTotals(
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
        )
        summary.appendix_9_by_country[country_iso] = bucket
    return bucket


def _process_sections(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    summary: AnalysisSummary,
    fx_provider: FxRateProvider,
    tax_year: int,
    tax_exempt_mode: str,
) -> _ProcessedSections:
    trades = process_trades_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,  # type: ignore[arg-type]
    )
    interest = process_interest_section(
        rows=rows,
        active_headers=active_headers,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
    )
    dividends = process_dividends_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
    )
    withholding = process_withholding_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
    )
    open_positions = process_open_positions_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
    )
    return _ProcessedSections(
        trades=trades,
        interest=interest,
        dividends=dividends,
        withholding=withholding,
        open_positions=open_positions,
    )


def _apply_interest_withholding_source(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    summary: AnalysisSummary,
    appendix9_components: dict[str, dict[str, _CountryCreditComponent]],
) -> None:
    withholding_paid_eur, withholding_found = extract_interest_withholding_paid_eur(
        rows,
        active_headers=active_headers,
    )
    if withholding_paid_eur > ZERO:
        country_iso, country_english, country_bulgarian = _appendix9_default_country()
        appendix9_bucket = _appendix9_bucket(
            summary,
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
        )
        appendix9_bucket.withholding_tax_paid_eur += withholding_paid_eur
        _country_component(
            appendix9_components,
            country_iso=country_iso,
            component_key="MTM_SOURCE",
        ).foreign_tax_paid_eur += withholding_paid_eur

    summary.appendix_9_withholding_paid_eur = withholding_paid_eur
    summary.appendix_9_withholding_source_found = withholding_found

    summary.appendix_9_credit_interest_eur = sum(
        (bucket.gross_interest_eur for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )
    summary.appendix_9_withholding_paid_eur = sum(
        (bucket.withholding_tax_paid_eur for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )
    if summary.appendix_9_credit_interest_eur > ZERO and not withholding_found:
        summary.review_required_rows += 1
        summary.warnings.append(
            "Mark-to-Market Performance Summary row for 'Withholding on Interest Received' was not found; using 0"
        )


def _compute_appendix_outputs(
    *,
    summary: AnalysisSummary,
    appendix9_components: dict[str, dict[str, _CountryCreditComponent]],
    appendix8_part1_by_country: dict[str, Appendix8Part1Row],
    out_dir: Path,
    normalized_alias: str,
    tax_year: int,
) -> None:
    summary.appendix_8_part1_rows = _build_appendix8_part1_rows(
        totals_by_country=appendix8_part1_by_country,
    )
    summary.open_positions_part1_rows = len(summary.appendix_8_part1_rows)

    summary.appendix_8_company_results = _compute_appendix8_company_results(
        totals_by_company=summary.appendix_8_by_company,
        dividend_tax_rate=summary.dividend_tax_rate,
    )
    if summary.appendix8_dividend_list_mode == APPENDIX8_LIST_MODE_COUNTRY:
        summary.appendix_8_output_rows = _aggregate_appendix8_company_rows_by_country_and_method(
            company_rows=summary.appendix_8_company_results,
        )
    else:
        summary.appendix_8_output_rows = list(summary.appendix_8_company_results)
    summary.appendix_8_country_debug = _build_appendix8_country_debug(
        company_rows=summary.appendix_8_company_results,
        dividend_tax_rate=summary.dividend_tax_rate,
    )
    summary.appendix_9_country_results = _compute_appendix9_country_results(
        totals_by_country=summary.appendix_9_by_country,
        components_by_country=appendix9_components,
        allowable_credit_rate=APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    )
    summary.tax_credit_debug_report_path = str(
        _write_tax_credit_debug_report(
            output_dir=out_dir,
            normalized_alias=normalized_alias,
            tax_year=tax_year,
            appendix8_company_rows=summary.appendix_8_company_results,
            appendix8_country_debug=summary.appendix_8_country_debug,
            appendix8_output_rows=summary.appendix_8_output_rows,
            appendix8_list_mode=summary.appendix8_dividend_list_mode,
            appendix9_results=summary.appendix_9_country_results,
        )
    )
    summary.appendix_6_code_603_eur = (
        summary.appendix_6_credit_interest_eur
        + summary.appendix_6_syep_interest_eur
        + summary.appendix_6_other_taxable_eur
        + summary.appendix_6_lieu_received_eur
    )


def _output_paths(*, out_dir: Path, normalized_alias: str, tax_year: int) -> tuple[Path, Path]:
    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    output_csv_path = out_dir / f"ibkr_activity{alias_suffix}_modified_{tax_year}.csv"
    declaration_txt_path = out_dir / f"ibkr_activity{alias_suffix}_declaration_{tax_year}.txt"
    return output_csv_path, declaration_txt_path


def _apply_sanity_to_summary(summary: AnalysisSummary, *, sanity) -> None:
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


def analyze_ibkr_activity_statement(
    *,
    input_csv: str | Path,
    tax_year: int,
    tax_exempt_mode: Literal["listed_symbol", "execution_exchange"],
    appendix8_dividend_list_mode: Literal["company", "country"] = APPENDIX8_LIST_MODE_COMPANY,
    report_alias: str | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    fx_rate_provider: FxRateProvider | None = None,
) -> AnalysisResult:
    _validate_analysis_request(
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,
        appendix8_dividend_list_mode=appendix8_dividend_list_mode,
    )

    input_path = _resolve_input_path(input_csv)
    normalized_alias = _normalize_report_alias(report_alias)
    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fx_provider = fx_rate_provider if fx_rate_provider is not None else _default_fx_provider(cache_dir)
    rows = _load_csv_rows(input_path)

    active_headers, seen_headers = _build_active_headers(rows)
    listings = parse_instrument_listings_with_headers(
        rows,
        active_headers=active_headers,
        seen_headers=seen_headers,
    )
    trades_row_extras: dict[int, list[str]] = {}
    trades_row_base_len: dict[int, int] = {}
    interest_row_extras: dict[int, list[str]] = {}
    interest_row_base_len: dict[int, int] = {}
    dividends_row_extras: dict[int, dict[str, str]] = {}
    dividends_row_base_len: dict[int, int] = {}
    withholding_row_extras: dict[int, dict[str, str]] = {}
    withholding_row_base_len: dict[int, int] = {}
    open_positions_row_extras: dict[int, dict[str, str]] = {}
    open_positions_row_base_len: dict[int, int] = {}
    dividends_row_added_columns: dict[int, list[str]] = {}
    withholding_row_added_columns: dict[int, list[str]] = {}
    open_positions_row_added_columns: dict[int, list[str]] = {}
    summary = AnalysisSummary(
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,
        dividend_tax_rate=DIVIDEND_TAX_RATE,
        appendix8_dividend_list_mode=appendix8_dividend_list_mode,
    )

    reconciliation_warnings = run_open_position_reconciliation(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
    )
    summary.review_required_rows += len(reconciliation_warnings)
    summary.warnings.extend(reconciliation_warnings)

    processed = _process_sections(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,
    )
    appendix9_components = processed.interest.components_by_country

    _apply_interest_withholding_source(
        rows=rows,
        active_headers=active_headers,
        summary=summary,
        appendix9_components=appendix9_components,
    )
    _compute_appendix_outputs(
        summary=summary,
        appendix9_components=appendix9_components,
        appendix8_part1_by_country=processed.open_positions.part1_by_country,
        out_dir=out_dir,
        normalized_alias=normalized_alias,
        tax_year=tax_year,
    )

    populate_trade_aggregate_extras(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        trades_row_extras=processed.trades.row_extras,
    )

    output_rows = build_output_rows(
        rows=rows,
        active_headers=active_headers,
        trades_row_extras=processed.trades.row_extras,
        trades_row_base_len=processed.trades.row_base_len,
        interest_row_extras=processed.interest.row_extras,
        interest_row_base_len=processed.interest.row_base_len,
        dividends_row_extras=processed.dividends.row_extras,
        dividends_row_base_len=processed.dividends.row_base_len,
        dividends_row_added_columns=processed.dividends.row_added_columns,
        withholding_row_extras=processed.withholding.row_extras,
        withholding_row_base_len=processed.withholding.row_base_len,
        withholding_row_added_columns=processed.withholding.row_added_columns,
        open_positions_row_extras=processed.open_positions.row_extras,
        open_positions_row_base_len=processed.open_positions.row_base_len,
        open_positions_row_added_columns=processed.open_positions.row_added_columns,
    )
    validate_output_rows(
        output_rows=output_rows,
        active_headers=active_headers,
        trades_row_base_len=processed.trades.row_base_len,
        interest_row_base_len=processed.interest.row_base_len,
        dividends_row_base_len=processed.dividends.row_base_len,
        dividends_row_added_columns=processed.dividends.row_added_columns,
        withholding_row_base_len=processed.withholding.row_base_len,
        withholding_row_added_columns=processed.withholding.row_added_columns,
        open_positions_row_base_len=processed.open_positions.row_base_len,
        open_positions_row_added_columns=processed.open_positions.row_added_columns,
    )

    output_csv_path, declaration_txt_path = _output_paths(
        out_dir=out_dir,
        normalized_alias=normalized_alias,
        tax_year=tax_year,
    )

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
    _apply_sanity_to_summary(summary, sanity=sanity)

    result = AnalysisResult(
        input_csv_path=input_path,
        output_csv_path=output_csv_path,
        declaration_txt_path=declaration_txt_path,
        report_alias=normalized_alias,
        summary=summary,
    )

    declaration_txt_path.write_text(
        _build_declaration_text(
            result,
            appendix9_allowable_credit_rate=APPENDIX_9_ALLOWABLE_CREDIT_RATE,
        ),
        encoding="utf-8",
    )
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
        "--appendix8-dividend-list-mode",
        choices=[APPENDIX8_LIST_MODE_COMPANY, APPENDIX8_LIST_MODE_COUNTRY],
        default=APPENDIX8_LIST_MODE_COMPANY,
        help="Appendix 8 dividend listing mode (default: company)",
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
            appendix8_dividend_list_mode=args.appendix8_dividend_list_mode,
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
    print(f"interest_processed_rows: {summary.interest_processed_rows}")
    print(f"interest_total_rows_skipped: {summary.interest_total_rows_skipped}")
    print(f"interest_taxable_rows: {summary.interest_taxable_rows}")
    print(f"interest_non_taxable_rows: {summary.interest_non_taxable_rows}")
    print(f"interest_unknown_rows: {summary.interest_unknown_rows}")
    if summary.interest_unknown_types:
        print(f"interest_unknown_types: {', '.join(sorted(summary.interest_unknown_types))}")
    print(f"dividends_processed_rows: {summary.dividends_processed_rows}")
    print(f"dividends_total_rows_skipped: {summary.dividends_total_rows_skipped}")
    print(f"dividends_cash_rows: {summary.dividends_cash_rows}")
    print(f"dividends_lieu_rows: {summary.dividends_lieu_rows}")
    print(f"dividends_unknown_rows: {summary.dividends_unknown_rows}")
    print(f"withholding_processed_rows: {summary.withholding_processed_rows}")
    print(f"withholding_total_rows_skipped: {summary.withholding_total_rows_skipped}")
    print(f"withholding_dividend_rows: {summary.withholding_dividend_rows}")
    print(f"withholding_non_dividend_rows: {summary.withholding_non_dividend_rows}")
    print(f"open_positions_summary_rows: {summary.open_positions_summary_rows}")
    print(f"appendix_8_part1_rows: {summary.open_positions_part1_rows}")
    print(f"dividend_tax_rate: {_fmt(summary.dividend_tax_rate)}")
    print(f"appendix8_dividend_list_mode: {summary.appendix8_dividend_list_mode}")
    print(f"appendix_5_profit_eur: {_fmt(summary.appendix_5.wins_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_5_loss_eur: {_fmt(summary.appendix_5.losses_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_13_profit_eur: {_fmt(summary.appendix_13.wins_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_13_loss_eur: {_fmt(summary.appendix_13.losses_eur, quant=DECIMAL_TWO)}")
    print(f"review_profit_eur: {_fmt(summary.review.wins_eur, quant=DECIMAL_TWO)}")
    print(f"review_loss_eur: {_fmt(summary.review.losses_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_6_code_603_eur: {_fmt(summary.appendix_6_code_603_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_9_credit_interest_eur: {_fmt(summary.appendix_9_credit_interest_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_9_withholding_paid_eur: {_fmt(summary.appendix_9_withholding_paid_eur, quant=DECIMAL_TWO)}")
    print("SANITY CHECKS PASSED" if summary.sanity_passed else "SANITY CHECKS FAILED")
    print(f"sanity_checks_passed: {'YES' if summary.sanity_passed else 'NO'}")
    print(f"sanity_checked_trade_rows: {summary.sanity_checked_closing_trades}")
    print(f"sanity_checked_closedlots: {summary.sanity_checked_closedlots}")
    print(f"sanity_checked_subtotals: {summary.sanity_checked_subtotals}")
    print(f"sanity_checked_totals: {summary.sanity_checked_totals}")
    print(f"sanity_forex_ignored_rows: {summary.sanity_forex_ignored_rows}")
    print(f"Modified CSV: {result.output_csv_path}")
    print(f"Declaration TXT: {result.declaration_txt_path}")
    if summary.tax_credit_debug_report_path:
        print(f"Tax credit debug report: {summary.tax_credit_debug_report_path}")
    print(f"Sanity-check debug artifacts written to: {summary.sanity_debug_artifacts_dir}")
    print("These are verification artifacts, not production tax outputs.")
    if summary.warnings:
        print("Warnings:")
        for warning in summary.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
