from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from integrations.shared.contracts import UserFacingTaxError
from integrations.shared.csv_numbers import (
    CSV_DECIMAL_SEPARATOR_MODES,
    CsvDecimalDetector,
    CsvDecimalFormatInfo,
    CsvDecimalSeparatorMode,
    reset_current_csv_decimal_separator,
    set_current_csv_decimal_separator,
    try_parse_csv_decimal,
)
from integrations.shared.rendering.display_currency import build_render_context

from .appendices.aggregations import (
    _aggregate_appendix8_company_rows_by_country_and_method,
    _build_appendix8_country_debug,
    _build_appendix8_part1_rows,
    _compute_appendix8_company_results,
    _compute_appendix9_country_results,
    _write_tax_credit_debug_report,
)
from .appendices.csv_output import build_output_rows, validate_output_rows
from .appendices.declaration_text import _build_declaration_text
from .constants import (
    APPENDIX8_LIST_MODE_COMPANY,
    APPENDIX8_LIST_MODE_COUNTRY,
    APPENDIX_9_ALLOWABLE_CREDIT_RATE,
    CFD_ASSET_CATEGORY,
    DEFAULT_OUTPUT_DIR,
    DIVIDEND_TAX_RATE,
    FxRateProvider,
    FOREX_ASSET_CATEGORY,
    NEGATIVE_PIL_MODE_POSITION_AWARE,
    NEGATIVE_PIL_MODES,
    OPTION_ASSET_CATEGORY,
    SUPPORTED_ASSET_CATEGORIES,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTING_EXCHANGE,
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
from .sections.fees import FeesSectionResult, process_fees_section
from .sections.futures import FuturesMtmSectionResult, process_futures_mtm_section
from .sections.instruments import (
    _exchange_classification_mode_label,
    _normalize_exchange,
    parse_instrument_listings_with_headers,
)
from .sections.interest import (
    InterestSectionResult,
    process_interest_section,
)
from .sections.negative_pil import build_negative_pil_exposure_index
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
from .shared import (
    IbkrReportDateFormat,
    _build_active_headers,
    _code_has_closing_token,
    _default_fx_provider,
    _infer_ibkr_report_date_format,
    _normalize_data_discriminator,
    _normalize_report_alias,
    _optional_index,
)
from .spb8 import extract_ibkr_spb8_rows

SUPPORTED_IBKR_SECTIONS = {
    "Account Information",
    "Borrow Fee Details",
    "Cash Report",
    "Change in Dividend Accruals",
    "Change in NAV",
    "Codes",
    "Deposits & Withdrawals",
    "Dividends",
    "Fees",
    "Financial Instrument Information",
    "Forex Balances",
    "Interest",
    "Interest Accruals",
    "Mark-to-Market Performance Summary",
    "Net Asset Value",
    "Net Stock Position Summary",
    "Notes/Legal Notes",
    "Open Positions",
    "Realized & Unrealized Performance Summary",
    "Statement",
    "Stock Yield Enhancement Program Securities Lent Activity",
    "Stock Yield Enhancement Program Securities Lent Interest Details",
    "Total P/L for Statement Period",
    "Trades",
    "Transfers",
    "Withholding Tax",
}
CORPORATE_ACTIONS_SECTION = "Corporate Actions"


@dataclass(slots=True)
class _ProcessedSections:
    trades: TradesSectionResult
    futures_mtm: FuturesMtmSectionResult
    fees: FeesSectionResult
    interest: InterestSectionResult
    dividends: DividendsSectionResult
    withholding: WithholdingSectionResult
    open_positions: OpenPositionsSectionResult


def _validate_analysis_request(
    *,
    tax_year: int,
    tax_exempt_mode: str,
    appendix8_dividend_list_mode: str,
    negative_pil_mode: str,
    csv_decimal_separator: str,
) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise IbkrAnalyzerError(f"invalid tax year: {tax_year}")

    if tax_exempt_mode not in {TAX_MODE_LISTING_EXCHANGE, TAX_MODE_EXECUTION_EXCHANGE}:
        raise IbkrAnalyzerError(f"unsupported tax exempt mode: {tax_exempt_mode}")
    if appendix8_dividend_list_mode not in {
        APPENDIX8_LIST_MODE_COMPANY,
        APPENDIX8_LIST_MODE_COUNTRY,
    }:
        raise IbkrAnalyzerError(
            f"unsupported Appendix 8 dividend list mode: {appendix8_dividend_list_mode}"
        )
    if negative_pil_mode not in NEGATIVE_PIL_MODES:
        raise IbkrAnalyzerError(f"unsupported negative PIL mode: {negative_pil_mode}")
    if csv_decimal_separator not in CSV_DECIMAL_SEPARATOR_MODES:
        raise IbkrAnalyzerError(f"unsupported CSV decimal separator mode: {csv_decimal_separator}")


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


def _resolve_csv_decimal_info(
    rows: list[list[str]],
    *,
    input_path: Path,
    csv_decimal_separator: CsvDecimalSeparatorMode,
) -> CsvDecimalFormatInfo:
    active_headers, _seen_headers = _build_active_headers(rows)
    detector = CsvDecimalDetector(analyzer_alias="ibkr", input_path=input_path)
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        active_header = active_headers.get(row_idx)
        if active_header is None:
            for column_idx, value in enumerate(row, start=1):
                detector.observe(value, row_number=row_number, column_name=f"column {column_idx}")
            continue
        for offset, value in enumerate(row[2:], start=0):
            column_name = active_header.headers[offset] if offset < len(active_header.headers) else f"column {offset + 3}"
            detector.observe(value, row_number=row_number, column_name=column_name)
    return detector.resolve(csv_decimal_separator)


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
    eu_regulated_exchange_overrides: set[str],
    closed_world_mode: bool,
    report_date_format: IbkrReportDateFormat,
    net_cfd_financing: bool,
    negative_pil_mode: str,
) -> _ProcessedSections:
    trades = process_trades_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,  # type: ignore[arg-type]
        eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
        closed_world_mode=closed_world_mode,
        report_date_format=report_date_format,
    )
    futures_mtm = process_futures_mtm_section(
        rows=rows,
        active_headers=active_headers,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
    )
    fees = process_fees_section(
        rows=rows,
        active_headers=active_headers,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        report_date_format=report_date_format,
        net_cfd_financing=net_cfd_financing,
    )
    interest = process_interest_section(
        rows=rows,
        active_headers=active_headers,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        report_date_format=report_date_format,
    )
    negative_pil_exposures = build_negative_pil_exposure_index(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        tax_year=tax_year,
        report_date_format=report_date_format,
    )
    summary.negative_pil_closed_exposure_ranges = sum(
        1
        for candidate in [*negative_pil_exposures.security_ranges, *negative_pil_exposures.cfd_ranges]
        if candidate.end is not None
    )
    dividends = process_dividends_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        report_date_format=report_date_format,
        negative_pil_mode=negative_pil_mode,
        negative_pil_exposures=negative_pil_exposures,
    )
    withholding = process_withholding_section(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        summary=summary,
        fx_provider=fx_provider,
        tax_year=tax_year,
        report_date_format=report_date_format,
        appendix9_components=interest.components_by_country,
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
        futures_mtm=futures_mtm,
        fees=fees,
        interest=interest,
        dividends=dividends,
        withholding=withholding,
        open_positions=open_positions,
    )


def _normalize_cli_eu_regulated_exchanges(raw_values: list[str] | None) -> set[str]:
    normalized: set[str] = set()
    for raw in raw_values or []:
        for token in raw.split(","):
            candidate = token.strip()
            if candidate == "":
                continue
            normalized_exchange = _normalize_exchange(candidate)
            if normalized_exchange == "":
                raise IbkrAnalyzerError(
                    "invalid --eu-regulated-exchange value: "
                    f"{candidate!r}"
                )
            normalized.add(normalized_exchange)
    return normalized


def _finalize_interest_withholding_totals(
    *,
    summary: AnalysisSummary,
) -> None:
    detail_withholding_paid_eur = sum(
        (bucket.withholding_tax_paid_eur for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )
    detail_withholding_found = summary.appendix_9_positive_withholding_rows > 0 or detail_withholding_paid_eur != ZERO

    summary.appendix_9_withholding_detail_source_found = detail_withholding_found
    summary.appendix_9_withholding_detail_paid_eur = detail_withholding_paid_eur

    if detail_withholding_found:
        summary.appendix_9_non_positive_net_buckets = sum(
            1 for bucket in summary.appendix_9_by_country.values() if bucket.withholding_tax_paid_eur <= ZERO
        )

    summary.appendix_9_withholding_source_found = detail_withholding_found

    summary.appendix_9_credit_interest_eur = sum(
        (bucket.gross_interest_eur for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )
    summary.appendix_9_withholding_paid_eur = sum(
        (max(ZERO, bucket.withholding_tax_paid_eur) for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )
    if summary.appendix_9_credit_interest_eur > ZERO and not summary.appendix_9_withholding_source_found:
        summary.review_required_rows += 1
        summary.warnings.append(
            "Withholding Tax detail rows containing 'interest' were not found for Appendix 9; using 0"
        )


def _compute_appendix_outputs(
    *,
    summary: AnalysisSummary,
    appendix9_components: dict[str, dict[str, _CountryCreditComponent]],
    appendix8_part1_by_country_currency: dict[tuple[str, str], Appendix8Part1Row],
    out_dir: Path,
    normalized_alias: str,
    tax_year: int,
) -> None:
    summary.appendix_8_part1_rows = _build_appendix8_part1_rows(
        totals_by_country_currency=appendix8_part1_by_country_currency,
    )
    summary.open_positions_part1_rows = len(summary.appendix_8_part1_rows)

    summary.appendix_8_company_results = _compute_appendix8_company_results(
        totals_by_company=summary.appendix_8_by_company,
        dividend_tax_rate=summary.dividend_tax_rate,
    )
    if summary.withholding_positive_dividend_rows > 0:
        summary.withholding_non_positive_net_buckets = sum(
            1 for totals in summary.appendix_8_by_company.values() if totals.withholding_tax_paid_eur <= ZERO
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
    summary.appendix_6_code_606_eur = (
        summary.appendix_6_positive_pil_eur
        + summary.appendix_6_positive_cfd_financing_eur
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


def _extract_statement_account(rows: list[list[str]], *, fallback: str) -> str:
    for row in rows:
        if len(row) >= 4 and row[0] == "Statement" and row[1] == "Data" and row[2].strip() == "Account":
            account = row[3].strip()
            if account:
                return account
    return fallback or "ibkr"


def _expected_statement_period_text(tax_year: int) -> str:
    return f"January 1, {tax_year} - December 31, {tax_year}"


def _statement_period_text(rows: list[list[str]]) -> str:
    for row in rows:
        if len(row) >= 4 and row[0] == "Statement" and row[1] == "Data" and row[2].strip() == "Period":
            period_cells = row[3:]
            while period_cells and period_cells[-1].strip() == "":
                period_cells = period_cells[:-1]
            return ",".join(period_cells).strip()
    return ""


def _validate_statement_period(rows: list[list[str]], *, tax_year: int) -> None:
    expected = _expected_statement_period_text(tax_year)
    period_text = _statement_period_text(rows)
    if period_text == "":
        raise IbkrAnalyzerError(
            "IBKR statement period is missing; expected Statement,Data,Period,"
            f"{expected!r}. The analyzer requires a full-year Activity Statement."
        )
    try:
        start_text, end_text = period_text.split(" - ", 1)
        start_date = datetime.strptime(start_text.strip(), "%B %d, %Y").date()
        end_date = datetime.strptime(end_text.strip(), "%B %d, %Y").date()
    except ValueError as exc:
        raise IbkrAnalyzerError(
            f"IBKR statement period is malformed: {period_text!r}; expected {expected!r}."
        ) from exc
    if period_text != expected or start_date.year != tax_year or end_date.year != tax_year:
        raise IbkrAnalyzerError(
            f"IBKR statement period must cover exactly {expected!r}; found {period_text!r}."
        )


def _ibkr_base_currency(rows: list[list[str]]) -> str:
    active_header: list[str] | None = None
    for row in rows:
        if len(row) < 2 or row[0] != "Account Information":
            continue
        if row[1] == "Header":
            active_header = [cell.strip() for cell in row[2:]]
            continue
        if row[1] != "Data":
            continue

        values = row[2:]
        if active_header:
            field_idx = _optional_index(active_header, "Field Name")
            value_idx = _optional_index(active_header, "Field Value")
            if field_idx is not None and value_idx is not None:
                padded = values + [""] * max(0, len(active_header) - len(values))
                if padded[field_idx].strip() == "Base Currency":
                    return padded[value_idx].strip()
                continue

        if len(row) >= 4 and row[2].strip() == "Base Currency":
            return row[3].strip()
    return ""


def _validate_base_currency(rows: list[list[str]]) -> None:
    base_currency = _ibkr_base_currency(rows)
    if base_currency == "EUR":
        return
    display_currency = base_currency or "<missing>"
    raise UserFacingTaxError(
        code="IBKR_UNSUPPORTED_BASE_CURRENCY",
        params={"base_currency": display_currency},
        technical_message_en=(
            f"ERROR: Unsupported IBKR base currency {display_currency!r}. "
            "Currently only EUR base currency accounts are supported for Bulgarian tax reporting."
        ),
    )


def _section_names(rows: list[list[str]]) -> set[str]:
    return {row[0].strip() for row in rows if row and row[0].strip()}


def _has_corporate_actions(rows: list[list[str]]) -> bool:
    return CORPORATE_ACTIONS_SECTION in _section_names(rows)


def _unsupported_section_warning(rows: list[list[str]]) -> str:
    unsupported = sorted(_section_names(rows) - SUPPORTED_IBKR_SECTIONS - {CORPORATE_ACTIONS_SECTION})
    if not unsupported:
        return ""
    sections = ", ".join(f"[{section}]" for section in unsupported)
    return (
        "⚠️ Открити са секции в IBKR Activity Statement CSV, които анализаторът все още не обработва: "
        f"{sections}. Прегледайте ги ръчно, ако могат да имат влияние върху данъците или СПБ-8. "
        "Ако липсват стойности в SPB-8 input файла, попълнете ги ръчно."
    )


def _validate_required_closedlot_rows(
    rows: list[list[str]],
    *,
    active_headers: dict[int, _ActiveHeader],
) -> None:
    closing_trade_rows: list[int] = []
    realized_summary_rows: list[int] = []
    closedlot_rows: list[int] = []

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades":
            continue
        row_type = row[1]
        if row_type == "Header":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            raise CsvStructureError(f"row {row_number}: Trades row encountered before Trades Header")

        padded = row + [""] * max(0, 2 + len(active_header.headers) - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        discriminator_idx = _optional_index(active_header.headers, "DataDiscriminator")
        asset_idx = _optional_index(active_header.headers, "Asset Category")
        if asset_idx is None:
            continue
        asset_category = data[asset_idx].strip()
        if (
            asset_category == FOREX_ASSET_CATEGORY
            or (
                asset_category not in SUPPORTED_ASSET_CATEGORIES
                and asset_category not in {CFD_ASSET_CATEGORY, OPTION_ASSET_CATEGORY}
            )
        ):
            continue

        if row_type == "Data":
            if discriminator_idx is None:
                continue
            discriminator = _normalize_data_discriminator(data[discriminator_idx])
            if discriminator == "closedlot":
                closedlot_rows.append(row_number)
                continue
            if discriminator not in {"trade", "order"}:
                continue
            code_idx = _optional_index(active_header.headers, "Code")
            if code_idx is not None and _code_has_closing_token(data[code_idx]):
                closing_trade_rows.append(row_number)
            continue

        if row_type in {"SubTotal", "Total"} and _has_realized_disposal_amount(data, active_header):
            realized_summary_rows.append(row_number)

    if (closing_trade_rows or realized_summary_rows) and not closedlot_rows:
        preview = ", ".join(str(row) for row in closing_trade_rows[:10])
        if len(closing_trade_rows) > 10:
            preview += ", ..."
        summary_preview = ", ".join(str(row) for row in realized_summary_rows[:10])
        if len(realized_summary_rows) > 10:
            summary_preview += ", ..."
        raise UserFacingTaxError(
            code="IBKR_INCOMPLETE_CLOSED_LOTS",
            params={
                "closing_trade_rows": closing_trade_rows,
                "closing_trade_count": len(closing_trade_rows),
                "realized_summary_rows": realized_summary_rows,
                "realized_summary_count": len(realized_summary_rows),
                "closedlot_count": len(closedlot_rows),
            },
            technical_message_en=(
                "IBKR Activity Statement contains realized disposal activity but no Trades/Data/ClosedLot rows. "
                f"closing_trade_count={len(closing_trade_rows)} closing_trade_rows=[{preview}] "
                f"realized_summary_count={len(realized_summary_rows)} realized_summary_rows=[{summary_preview}]"
            ),
        )


def _has_realized_disposal_amount(data: list[str], active_header: _ActiveHeader) -> bool:
    realized_idx = _optional_index(active_header.headers, "Realized P/L")
    if realized_idx is not None and _is_nonzero_decimal_text(data[realized_idx]):
        return True

    quantity_idx = _optional_index(active_header.headers, "Quantity")
    proceeds_idx = _optional_index(active_header.headers, "Proceeds", "Notional Value")
    basis_idx = _optional_index(active_header.headers, "Basis")
    return (
        quantity_idx is not None
        and proceeds_idx is not None
        and basis_idx is not None
        and _is_zero_decimal_text(data[quantity_idx])
        and _is_nonzero_decimal_text(data[proceeds_idx])
        and _is_nonzero_decimal_text(data[basis_idx])
    )


def _is_zero_decimal_text(raw: str) -> bool:
    parsed = try_parse_csv_decimal(raw)
    return parsed == ZERO if parsed is not None else False


def _is_nonzero_decimal_text(raw: str) -> bool:
    parsed = try_parse_csv_decimal(raw)
    return parsed != ZERO if parsed is not None else False


def analyze_ibkr_activity_statement(
    *,
    input_csv: str | Path,
    tax_year: int,
    tax_exempt_mode: Literal["listing_exchange", "execution_exchange"],
    appendix8_dividend_list_mode: Literal["company", "country"] = APPENDIX8_LIST_MODE_COMPANY,
    report_alias: str | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    display_currency: str = "EUR",
    eu_regulated_exchanges: list[str] | None = None,
    closed_world: bool = False,
    skip_period_validation: bool = False,
    net_cfd_financing: bool = True,
    negative_pil_mode: str = NEGATIVE_PIL_MODE_POSITION_AWARE,
    csv_decimal_separator: CsvDecimalSeparatorMode = "auto",
    fx_rate_provider: FxRateProvider | None = None,
) -> AnalysisResult:
    _validate_analysis_request(
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,
        appendix8_dividend_list_mode=appendix8_dividend_list_mode,
        negative_pil_mode=negative_pil_mode,
        csv_decimal_separator=csv_decimal_separator,
    )

    input_path = _resolve_input_path(input_csv)
    normalized_alias = _normalize_report_alias(report_alias)
    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fx_provider = fx_rate_provider if fx_rate_provider is not None else _default_fx_provider(cache_dir)
    rows = _load_csv_rows(input_path)
    csv_decimal_info = _resolve_csv_decimal_info(
        rows,
        input_path=input_path,
        csv_decimal_separator=csv_decimal_separator,
    )
    csv_decimal_token = set_current_csv_decimal_separator(csv_decimal_info.separator)
    try:
        _validate_base_currency(rows)
        eu_regulated_exchange_overrides = _normalize_cli_eu_regulated_exchanges(eu_regulated_exchanges)
        closed_world_mode = closed_world or bool(eu_regulated_exchange_overrides)

        summary = AnalysisSummary(
            tax_year=tax_year,
            tax_exempt_mode=tax_exempt_mode,
            dividend_tax_rate=DIVIDEND_TAX_RATE,
            appendix8_dividend_list_mode=appendix8_dividend_list_mode,
            net_cfd_financing=net_cfd_financing,
            negative_pil_mode=negative_pil_mode,
        )
        if skip_period_validation:
            summary.warnings.append(
                "IBKR statement period validation was skipped; results may be incomplete or wrong."
            )
        else:
            _validate_statement_period(rows, tax_year=tax_year)
        unsupported_section_warning = _unsupported_section_warning(rows)
        if unsupported_section_warning:
            summary.warnings.append(unsupported_section_warning)
        summary.spb8_corporate_actions_present = _has_corporate_actions(rows)
        summary.exchange_classification_mode = _exchange_classification_mode_label(
            eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
            force_closed_world=closed_world_mode,
        )
        summary.cli_eu_regulated_overrides = set(eu_regulated_exchange_overrides)

        active_headers, seen_headers = _build_active_headers(rows)
        _validate_required_closedlot_rows(rows, active_headers=active_headers)
        report_date_format = _infer_ibkr_report_date_format(rows, active_headers)
        summary.report_date_format_label = report_date_format.label
        summary.report_date_format_reason = report_date_format.reason
        summary.closedlot_date_format_label = report_date_format.label
        summary.closedlot_date_format_reason = report_date_format.reason
        listings = parse_instrument_listings_with_headers(
            rows,
            active_headers=active_headers,
            seen_headers=seen_headers,
            summary=summary,
            eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
            closed_world_mode=closed_world_mode,
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
            eu_regulated_exchange_overrides=eu_regulated_exchange_overrides,
            closed_world_mode=closed_world_mode,
            report_date_format=report_date_format,
            net_cfd_financing=net_cfd_financing,
            negative_pil_mode=negative_pil_mode,
        )
        appendix9_components = processed.interest.components_by_country

        _finalize_interest_withholding_totals(
            summary=summary,
        )
        _compute_appendix_outputs(
            summary=summary,
            appendix9_components=appendix9_components,
            appendix8_part1_by_country_currency=processed.open_positions.part1_by_country_currency,
            out_dir=out_dir,
            normalized_alias=normalized_alias,
            tax_year=tax_year,
        )
        spb8 = extract_ibkr_spb8_rows(
            rows=rows,
            active_headers=active_headers,
            listings=listings,
            account_name=_extract_statement_account(rows, fallback=normalized_alias or input_path.stem),
            corporate_actions_present=summary.spb8_corporate_actions_present,
        )
        summary.spb8_rows = spb8.rows
        summary.spb8_notes = spb8.warnings
        if summary.cfd_trade_rows > 0 or summary.cfd_open_position_rows > 0:
            summary.spb8_notes.append(
                "CFD позициите не се включват в СПБ-8, защото са деривативни/synthetic експозиции, а не реални ценни книжа с ISIN."
            )
        if summary.futures_mtm_rows > 0:
            summary.spb8_notes.append(
                "IBKR фючърсите не се включват като ценни книжа в СПБ-8, защото са "
                "деривативни/парично сетълнати договори, а не реално притежавани ценни книжа "
                "с ISIN. IBKR паричните средства/сметки се разглеждат отделно по правилата за СПБ-8."
            )
        if (
            summary.option_closedlot_rows > 0
            or summary.option_open_position_rows > 0
            or summary.option_exercise_assignment_without_closedlot_rows > 0
            or summary.option_unhandled_trade_rows > 0
        ):
            summary.spb8_notes.append("Опциите не се включват в СПБ-8 като притежавани ценни книжа.")

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
            fees_row_extras=processed.fees.row_extras,
            fees_row_base_len=processed.fees.row_base_len,
            fees_row_added_columns=processed.fees.row_added_columns,
            futures_mtm_row_extras=processed.futures_mtm.row_extras,
            futures_mtm_row_base_len=processed.futures_mtm.row_base_len,
            futures_mtm_row_added_columns=processed.futures_mtm.row_added_columns,
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
            fees_row_base_len=processed.fees.row_base_len,
            fees_row_added_columns=processed.fees.row_added_columns,
            futures_mtm_row_base_len=processed.futures_mtm.row_base_len,
            futures_mtm_row_added_columns=processed.futures_mtm.row_added_columns,
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
            csv_decimal_info=csv_decimal_info,
        )
        render_context = build_render_context(
            tax_year=tax_year,
            display_currency=display_currency,
            cache_dir=cache_dir,
        )

        declaration_txt_path.write_text(
            _build_declaration_text(
                result,
                appendix9_allowable_credit_rate=APPENDIX_9_ALLOWABLE_CREDIT_RATE,
                money_context=render_context.money_context,
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
    finally:
        reset_current_csv_decimal_separator(csv_decimal_token)
