from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from integrations.fund.shared.fund_ir_models import FundAnalysisSummary
from integrations.ibkr.appendices.declaration_text import (
    _build_manual_check_reasons,
    analysis_settings_main_report_notes,
    cfd_pil_policy_audit_lines,
    cfd_pil_policy_notes,
    futures_policy_audit_lines,
    options_policy_audit_lines,
)
from integrations.ibkr.models import AnalysisSummary as IbkrAnalysisSummary
from integrations.p2p.shared.appendix6_models import P2PAppendix6Result
from integrations.p2p.shared.appendix6_renderer import (
    _fmt_informative_value,
    _is_informative_value_empty_or_zero,
    _split_label_and_currency,
    _translate_info_label_bg,
    _translate_tax_message_bg,
)

from .contracts import AnalysisDiagnostic, AppendixRecord, GeneratedArtifact, MainReportNote, TaxAnalysisResult
from .reporting import normalize_diagnostics


_ROW_RE = re.compile(r"row (?P<row>\d+)")
_P2P_YEAR_RE = re.compile(
    r"reporting year in PDF \((?P<report_year>[^)]+)\) differs from requested tax year \((?P<tax_year>[^)]+)\)"
)
_ANALYZER_ASSUMPTIONS_SECTION = "Анализаторни допускания и проверки"


def _output_paths_to_path_map(paths: dict[str, str | Path]) -> dict[str, Path]:
    return {
        key: (value if isinstance(value, Path) else Path(value)).expanduser().resolve()
        for key, value in paths.items()
    }


_ROW_LEVEL_AUDIT_OUTPUT_KEYS = frozenset({"modified_csv", "detailed_csv", "enriched_ir_csv"})


def _generated_artifacts_from_output_paths(paths: dict[str, Path]) -> list[GeneratedArtifact]:
    return [
        GeneratedArtifact(
            artifact_type="row_level_audit_csv",
            label="row-level audit CSV",
            path=path,
            show_in_main=True,
            show_in_diagnostics=True,
        )
        for key, path in sorted(paths.items())
        if key in _ROW_LEVEL_AUDIT_OUTPUT_KEYS
    ]


def _row_from_message(message: str) -> str:
    match = _ROW_RE.search(message)
    return match.group("row") if match else ""


def _diagnostic(
    *,
    severity: str,
    analyzer_alias: str,
    code: str,
    message: str,
    params: dict[str, object] | None = None,
    technical_message_en: str | None = None,
) -> AnalysisDiagnostic:
    return AnalysisDiagnostic(
        severity=severity,  # type: ignore[arg-type]
        analyzer_alias=analyzer_alias,
        code=code,
        message=message,
        params=params or {},
        technical_message_en=technical_message_en,
    )


def _crypto_warning_diagnostic(*, analyzer_alias: str, warning: str) -> AnalysisDiagnostic:
    row = _row_from_message(warning)
    params: dict[str, object] = {"items": [{"row": row, "raw_detail": warning}]} if row else {"raw_detail": warning}
    if "unsupported Transaction Type" in warning or "unsupported Kraken combination" in warning:
        return _diagnostic(
            severity="MANUAL_REVIEW",
            analyzer_alias=analyzer_alias,
            code="CRYPTO_UNSUPPORTED_TRANSACTION_TYPE",
            message="Unsupported crypto transaction types were excluded from tax calculations.",
            params=params,
        )
    if "missing Cost Basis" in warning or "invalid Cost Basis" in warning or "Cost Basis" in warning:
        return _diagnostic(
            severity="MANUAL_REVIEW",
            analyzer_alias=analyzer_alias,
            code="CRYPTO_MISSING_COST_BASIS",
            message="Crypto rows require cost-basis review.",
            params=params,
        )
    if "Review Status" in warning:
        return _diagnostic(
            severity="MANUAL_REVIEW",
            analyzer_alias=analyzer_alias,
            code="CRYPTO_ROW_REQUIRES_REVIEW",
            message="Crypto rows require manual review.",
            params=params,
        )
    return _diagnostic(
        severity="WARNING",
        analyzer_alias=analyzer_alias,
        code="CRYPTO_ROW_REQUIRES_REVIEW",
        message="Crypto rows require review.",
        params=params,
    )


def _crypto_summary_diagnostics(*, analyzer_alias: str, summary: IrAnalysisSummary) -> list[AnalysisDiagnostic]:
    diagnostics = [_crypto_warning_diagnostic(analyzer_alias=analyzer_alias, warning=warning) for warning in summary.warnings]
    if not diagnostics and summary.unsupported_transaction_rows > 0:
        diagnostics.append(
            _diagnostic(
                severity="MANUAL_REVIEW",
                analyzer_alias=analyzer_alias,
                code="CRYPTO_UNSUPPORTED_TRANSACTION_TYPE",
                message="Unsupported crypto transaction types were excluded from tax calculations.",
                params={
                    "count": summary.unsupported_transaction_rows,
                    "transaction_types": sorted(summary.unknown_transaction_types),
                },
            )
        )
    if not any(item.code == "CRYPTO_ROW_REQUIRES_REVIEW" for item in diagnostics) and summary.invalid_send_review_rows > 0:
        diagnostics.append(
            _diagnostic(
                severity="MANUAL_REVIEW",
                analyzer_alias=analyzer_alias,
                code="CRYPTO_ROW_REQUIRES_REVIEW",
                message="Crypto rows require manual review.",
                params={
                    "count": summary.invalid_send_review_rows,
                    "review_statuses": sorted(summary.unknown_send_review_statuses),
                },
            )
        )
    return diagnostics


def _fund_warning_diagnostic(*, analyzer_alias: str, warning: str) -> AnalysisDiagnostic:
    row = _row_from_message(warning)
    params: dict[str, object] = {"items": [{"row": row, "raw_detail": warning}]} if row else {"raw_detail": warning}
    if "unsupported Type=" in warning or "unsupported" in warning:
        return _diagnostic(
            severity="MANUAL_REVIEW",
            analyzer_alias=analyzer_alias,
            code="FUND_UNSUPPORTED_ROW_TYPE",
            message="Unsupported fund rows were excluded from tax calculations.",
            params=params,
        )
    if "FX fallback" in warning or "fallback" in warning:
        return _diagnostic(
            severity="WARNING",
            analyzer_alias=analyzer_alias,
            code="FUND_FX_LOOKUP_FALLBACK",
            message="FX fallback was used for fund rows.",
            params=params,
        )
    return _diagnostic(
        severity="MANUAL_REVIEW",
        analyzer_alias=analyzer_alias,
        code="FUND_ROW_REQUIRES_REVIEW",
        message="Fund rows require manual review.",
        params=params,
    )


def _fund_summary_diagnostics(*, analyzer_alias: str, summary: FundAnalysisSummary) -> list[AnalysisDiagnostic]:
    diagnostics = [_fund_warning_diagnostic(analyzer_alias=analyzer_alias, warning=warning) for warning in summary.warnings]
    if not diagnostics and summary.unsupported_transaction_rows > 0:
        diagnostics.append(
            _diagnostic(
                severity="MANUAL_REVIEW",
                analyzer_alias=analyzer_alias,
                code="FUND_UNSUPPORTED_ROW_TYPE",
                message="Unsupported fund rows were excluded from tax calculations.",
                params={
                    "count": summary.unsupported_transaction_rows,
                    "transaction_types": sorted(summary.unknown_transaction_types),
                },
            )
        )
    return diagnostics


def _p2p_warning_diagnostic(*, analyzer_alias: str, warning: str) -> AnalysisDiagnostic:
    year_match = _P2P_YEAR_RE.search(warning)
    if year_match:
        return _diagnostic(
            severity="MANUAL_REVIEW",
            analyzer_alias=analyzer_alias,
            code="P2P_REPORTING_YEAR_MISMATCH",
            message="P2P report year differs from requested tax year.",
            params=year_match.groupdict(),
        )
    if "Appendix total row mismatch vs parsed detail rows" in warning:
        return _diagnostic(
            severity="WARNING",
            analyzer_alias=analyzer_alias,
            code="P2P_TOTAL_ROW_MISMATCH",
            message="P2P appendix total row does not match parsed detail rows.",
            params={"raw_detail": warning},
        )
    if "secondary market" in warning.lower():
        return _diagnostic(
            severity="MANUAL_REVIEW",
            analyzer_alias=analyzer_alias,
            code="P2P_SECONDARY_MARKET_REVIEW_REQUIRED",
            message="Secondary market amount requires manual review.",
            params={"raw_detail": warning},
        )
    if "negative" in warning and "not included" in warning:
        return _diagnostic(
            severity="WARNING",
            analyzer_alias=analyzer_alias,
            code="P2P_AMOUNT_OMITTED",
            message="P2P amount was omitted from Appendix 6 because it was negative or unsupported.",
            params={"raw_detail": warning},
        )
    if "normalized as a negative value" in warning:
        return _diagnostic(
            severity="WARNING",
            analyzer_alias=analyzer_alias,
            code="P2P_AMOUNT_NORMALIZED",
            message="P2P amount sign was normalized.",
            params={"raw_detail": warning},
        )
    return _diagnostic(
        severity="MANUAL_REVIEW",
        analyzer_alias=analyzer_alias,
        code="P2P_ROW_REQUIRES_REVIEW",
        message="P2P row requires manual review.",
        params={"raw_detail": warning},
    )


def _p2p_info_diagnostic(*, analyzer_alias: str, note: str) -> AnalysisDiagnostic:
    code = "P2P_AMOUNT_OMITTED" if "omitted" in note.lower() else "P2P_PROCESSING_INFO"
    message = (
        "P2P amount was omitted from Appendix 6 because it was not positive or not mappable."
        if code == "P2P_AMOUNT_OMITTED"
        else "P2P processing information."
    )
    return _diagnostic(
        severity="INFO",
        analyzer_alias=analyzer_alias,
        code=code,
        message=message,
        params={"raw_detail": note},
    )


def _binance_futures_warning_diagnostic(*, analyzer_alias: str, warning: str) -> AnalysisDiagnostic:
    row = _row_from_message(warning)
    params: dict[str, object] = {"items": [{"row": row, "raw_detail": warning}]} if row else {"raw_detail": warning}
    lowered = warning.lower()
    if "funding fee" in lowered:
        code = "BINANCE_FUTURES_FUNDING_FEE_REVIEW_REQUIRED"
        message = "Binance futures funding fee row requires review."
    elif "unsupported" in lowered and "income" in lowered:
        code = "BINANCE_FUTURES_UNSUPPORTED_INCOME_TYPE"
        message = "Unsupported income type in Binance futures report."
    else:
        code = "BINANCE_FUTURES_UNSUPPORTED_INCOME_TYPE"
        message = "Binance futures row requires review."
    return _diagnostic(
        severity="WARNING",
        analyzer_alias=analyzer_alias,
        code=code,
        message=message,
        params=params,
    )


def _crypto_main_report_notes(*, analyzer_alias: str, input_path: Path, summary: IrAnalysisSummary) -> list[MainReportNote]:
    state_text = (
        'няма подадено начално състояние; отчетът се третира като "since inception".'
        if summary.opening_state_year_end is None
        else f"използвано е начално състояние към края на {summary.opening_state_year_end}."
    )
    lines = [
        f"{analyzer_alias} — {input_path.name}: Начално състояние: {state_text}",
        (
            f"{analyzer_alias} — {input_path.name}: Редове, включени в декларацията за данъчната година: "
            f"{summary.rows_included_in_tax_year}."
        ),
    ]
    if summary.rows_ignored_before_or_equal_opening_state_year or summary.rows_ignored_after_tax_year:
        lines.append(
            f"{analyzer_alias} — {input_path.name}: Игнорирани редове извън обхвата: "
            f"{summary.rows_ignored_before_or_equal_opening_state_year} преди/до началното състояние, "
            f"{summary.rows_ignored_after_tax_year} след данъчната година."
        )
    if summary.manual_check_overrides_rows:
        lines.append(
            f"{analyzer_alias} — {input_path.name}: Ръчни Review Status overrides: "
            f"{summary.manual_check_overrides_rows}."
        )
    return [
        MainReportNote(
            section_title=_ANALYZER_ASSUMPTIONS_SECTION,
            text=line,
            analyzer_alias=analyzer_alias,
            source_path=input_path,
            category="setting",
        )
        for line in lines
    ]


def _fund_main_report_notes(*, analyzer_alias: str, input_path: Path, summary: FundAnalysisSummary) -> list[MainReportNote]:
    state_text = (
        'няма подадено начално състояние; отчетът се третира като "since inception".'
        if summary.opening_state_year_end is None
        else f"използвано е начално състояние към края на {summary.opening_state_year_end}."
    )
    lines = [
        f"{analyzer_alias} — {input_path.name}: Начално състояние: {state_text}",
        (
            f"{analyzer_alias} — {input_path.name}: Редове, включени в декларацията за данъчната година: "
            f"{summary.rows_included_in_tax_year}."
        ),
    ]
    if summary.rows_ignored_before_or_equal_opening_state_year or summary.rows_ignored_after_tax_year:
        lines.append(
            f"{analyzer_alias} — {input_path.name}: Игнорирани редове извън обхвата: "
            f"{summary.rows_ignored_before_or_equal_opening_state_year} преди/до началното състояние, "
            f"{summary.rows_ignored_after_tax_year} след данъчната година."
        )
    return [
        MainReportNote(
            section_title=_ANALYZER_ASSUMPTIONS_SECTION,
            text=line,
            analyzer_alias=analyzer_alias,
            source_path=input_path,
            category="setting",
        )
        for line in lines
    ]


def _p2p_main_report_notes(
    *,
    analyzer_alias: str,
    input_path: Path,
    result: P2PAppendix6Result,
) -> list[MainReportNote]:
    notes: list[MainReportNote] = []
    processing_messages = [*result.warnings, *result.informational_messages]
    for message in processing_messages:
        translated = _translate_tax_message_bg(message)
        if translated is None:
            translated = "Има обработваща бележка; вижте diagnostics файла за техническия детайл."
        notes.append(
            MainReportNote(
                section_title=_ANALYZER_ASSUMPTIONS_SECTION,
                text=f"{analyzer_alias} — {input_path.name}: {translated}",
                analyzer_alias=analyzer_alias,
                source_path=input_path,
                category="review" if message in result.warnings else "info",
            )
        )
    informative_values: list[str] = []
    for info in result.informative_rows:
        if _is_informative_value_empty_or_zero(info.value):
            continue
        base_label, _currency = _split_label_and_currency(info.label)
        label = _translate_info_label_bg(base_label)
        text = f"{label}: {_fmt_informative_value(info.value)}"
        if text not in informative_values:
            informative_values.append(text)
    if informative_values:
        notes.append(
            MainReportNote(
                section_title=_ANALYZER_ASSUMPTIONS_SECTION,
                text=(
                    f"{analyzer_alias} — {input_path.name}: Информативни стойности в индивидуалния отчет: "
                    f"{', '.join(informative_values[:8])}"
                    + ("." if len(informative_values) <= 8 else f" и още {len(informative_values) - 8}.")
                ),
                analyzer_alias=analyzer_alias,
                source_path=input_path,
                category="info",
            )
        )
    return notes


def build_crypto_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    summary: IrAnalysisSummary,
    declaration_code: str = "5082",
) -> TaxAnalysisResult:
    diagnostics = _crypto_summary_diagnostics(analyzer_alias=analyzer_alias, summary=summary)
    normalized_output_paths = _output_paths_to_path_map(output_paths)

    bucket = summary.appendix_5
    appendices = [
        AppendixRecord(
            appendix="5",
            part=None,
            table="2",
            code=declaration_code,
            values={
                "sale_value_eur": bucket.sale_price_eur,
                "acquisition_value_eur": bucket.purchase_price_eur,
                "profit_eur": bucket.wins_eur,
                "loss_eur": bucket.losses_eur,
                "trade_count": bucket.rows,
                "net_result_eur": bucket.net_result_eur,
            },
        )
    ]
    return TaxAnalysisResult(
        analyzer_alias=analyzer_alias,
        input_path=input_path.resolve(),
        tax_year=tax_year,
        output_paths=normalized_output_paths,
        appendices=appendices,
        diagnostics=diagnostics,
        generated_artifacts=_generated_artifacts_from_output_paths(normalized_output_paths),
        main_report_notes=_crypto_main_report_notes(
            analyzer_alias=analyzer_alias,
            input_path=input_path.resolve(),
            summary=summary,
        ),
    )


def build_fund_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    summary: FundAnalysisSummary,
    declaration_code: str,
) -> TaxAnalysisResult:
    diagnostics = _fund_summary_diagnostics(analyzer_alias=analyzer_alias, summary=summary)
    normalized_output_paths = _output_paths_to_path_map(output_paths)

    bucket = summary.appendix_5
    appendices = [
        AppendixRecord(
            appendix="5",
            part=None,
            table="2",
            code=declaration_code,
            values={
                "sale_value_eur": bucket.sale_price_eur,
                "acquisition_value_eur": bucket.purchase_price_eur,
                "profit_eur": bucket.wins_eur,
                "loss_eur": bucket.losses_eur,
                "trade_count": bucket.rows,
                "net_result_eur": bucket.net_result_eur,
            },
        )
    ]
    return TaxAnalysisResult(
        analyzer_alias=analyzer_alias,
        input_path=input_path.resolve(),
        tax_year=tax_year,
        output_paths=normalized_output_paths,
        appendices=appendices,
        diagnostics=diagnostics,
        generated_artifacts=_generated_artifacts_from_output_paths(normalized_output_paths),
        main_report_notes=_fund_main_report_notes(
            analyzer_alias=analyzer_alias,
            input_path=input_path.resolve(),
            summary=summary,
        ),
    )


def build_binance_futures_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    sale_value_eur: Decimal,
    acquisition_value_eur: Decimal,
    profit_eur: Decimal,
    loss_eur: Decimal,
    trade_count: int,
    warnings: list[str] | None = None,
) -> TaxAnalysisResult:
    normalized_output_paths = _output_paths_to_path_map(output_paths)
    diagnostics = [
        _binance_futures_warning_diagnostic(analyzer_alias=analyzer_alias, warning=warning)
        for warning in (warnings or [])
    ]
    appendices = [
        AppendixRecord(
            appendix="5",
            part=None,
            table="2",
            code="5082",
            values={
                "sale_value_eur": sale_value_eur,
                "acquisition_value_eur": acquisition_value_eur,
                "profit_eur": profit_eur,
                "loss_eur": loss_eur,
                "trade_count": trade_count,
                "net_result_eur": profit_eur - loss_eur,
            },
        )
    ]
    return TaxAnalysisResult(
        analyzer_alias=analyzer_alias,
        input_path=input_path.resolve(),
        tax_year=tax_year,
        output_paths=normalized_output_paths,
        appendices=appendices,
        diagnostics=diagnostics,
        generated_artifacts=_generated_artifacts_from_output_paths(normalized_output_paths),
    )


def build_p2p_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    result: P2PAppendix6Result,
) -> TaxAnalysisResult:
    normalized_output_paths = _output_paths_to_path_map(output_paths)
    diagnostics: list[AnalysisDiagnostic] = []
    diagnostics.extend(
        _p2p_warning_diagnostic(analyzer_alias=analyzer_alias, warning=warning)
        for warning in result.warnings
    )
    diagnostics.extend(
        _p2p_info_diagnostic(analyzer_alias=analyzer_alias, note=note)
        for note in result.informational_messages
    )

    appendices: list[AppendixRecord] = []
    for row in result.part1_rows:
        appendices.append(
            AppendixRecord(
                appendix="6",
                part="I",
                code=row.code,
                values={
                    "row_kind": "company",
                    "payer": row.payer_name,
                    "payer_eik": row.payer_eik or "-",
                    "income_eur": row.amount,
                },
            )
        )

    appendices.append(
        AppendixRecord(
            appendix="6",
            part="I",
            code="603",
            values={"row_kind": "total_by_code", "amount_eur": result.aggregate_code_603},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="I",
            code="606",
            values={"row_kind": "total_by_code", "amount_eur": result.aggregate_code_606},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="II",
            code="603",
            values={"taxable_income_eur": result.taxable_code_603},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="II",
            code="606",
            values={"taxable_income_eur": result.taxable_code_606},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="III",
            code=None,
            values={"withheld_tax_eur": result.withheld_tax},
        )
    )

    return TaxAnalysisResult(
        analyzer_alias=analyzer_alias,
        input_path=input_path.resolve(),
        tax_year=tax_year,
        output_paths=normalized_output_paths,
        appendices=appendices,
        diagnostics=diagnostics,
        generated_artifacts=_generated_artifacts_from_output_paths(normalized_output_paths),
        main_report_notes=_p2p_main_report_notes(
            analyzer_alias=analyzer_alias,
            input_path=input_path.resolve(),
            result=result,
        ),
    )


def build_ibkr_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    summary: IbkrAnalysisSummary,
) -> TaxAnalysisResult:
    normalized_output_paths = _output_paths_to_path_map(output_paths)
    legacy_diagnostics: list[AnalysisDiagnostic] = []
    legacy_diagnostics.extend(
        AnalysisDiagnostic(severity="WARNING", message=warning, analyzer_alias=analyzer_alias)
        for warning in summary.warnings
    )
    if summary.review_entries:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                analyzer_alias=analyzer_alias,
                code="IBKR_MANUAL_REVIEW_ROWS",
                message="IBKR trade rows require manual tax-treatment review.",
                params={
                    "count": len(summary.review_entries),
                    "rows": [
                        {
                            "row": entry.row_number,
                            "section": "Trades",
                            "symbol": entry.symbol,
                            "date": entry.trade_date,
                            "listing_exchange": entry.listing_exchange_raw,
                            "listing_exchange_normalized": entry.listing_exchange,
                            "mapped_classification": entry.mapped_listing_classification,
                            "execution_exchange": entry.execution_exchange,
                            "reason": entry.reason,
                        }
                        for entry in summary.review_entries
                    ],
                },
            )
        )
    legacy_diagnostics.extend(
        AnalysisDiagnostic(severity="MANUAL_REVIEW", message=reason, analyzer_alias=analyzer_alias)
        for reason in _build_manual_check_reasons(summary)
    )
    if summary.withholding_positive_dividend_rows > 0:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="WARNING",
                analyzer_alias=analyzer_alias,
                code="IBKR_DIVIDEND_WHT_REVERSAL_REVIEW",
                message=(
                    "Positive dividend withholding tax rows were netted against current-year "
                    "Appendix 8 foreign tax."
                ),
                params={
                    "positive_wht_rows": summary.withholding_positive_dividend_rows,
                    "non_positive_net_buckets": summary.withholding_non_positive_net_buckets,
                },
            )
        )
    if summary.appendix_9_withholding_mismatch_found:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                analyzer_alias=analyzer_alias,
                code="IBKR_APPENDIX9_WHT_SOURCE_MISMATCH",
                message=(
                    "Appendix 9 interest withholding tax detail rows differ from "
                    "Mark-to-Market Performance Summary."
                ),
                params={
                    "detail_wht_eur": summary.appendix_9_withholding_detail_paid_eur,
                    "mtm_wht_eur": summary.appendix_9_withholding_mtm_paid_eur,
                    "difference_eur": summary.appendix_9_withholding_mismatch_eur,
                },
            )
        )
    if summary.appendix_9_positive_withholding_rows > 0:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="WARNING",
                analyzer_alias=analyzer_alias,
                code="IBKR_APPENDIX9_POSITIVE_WHT_REVERSAL",
                message=(
                    "Positive interest withholding tax rows were netted against current-year "
                    "Appendix 9 foreign tax."
                ),
                params={
                    "positive_wht_rows": summary.appendix_9_positive_withholding_rows,
                    "non_positive_net_buckets": summary.appendix_9_non_positive_net_buckets,
                },
            )
        )
    if summary.futures_mtm_arithmetic_mismatches:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="WARNING",
                analyzer_alias=analyzer_alias,
                code="IBKR_FUTURES_MTM_ARITHMETIC_MISMATCH",
                message="Futures Mark-to-Market arithmetic mismatch was detected.",
                params={
                    "count": len(summary.futures_mtm_arithmetic_mismatches),
                    "rows": summary.futures_mtm_arithmetic_mismatches,
                },
            )
        )
    if summary.futures_mtm_other_rows > 0:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="INFO",
                analyzer_alias=analyzer_alias,
                code="IBKR_FUTURES_MTM_OTHER_INCLUDED",
                message="Non-zero Futures Mark-to-Market P/L Other was included via MTM Total.",
                params={
                    "count": summary.futures_mtm_other_rows,
                    "other_eur": summary.futures_mtm_other_eur,
                },
            )
        )
    if summary.option_exercise_assignment_without_closedlot_rows > 0:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="INFO",
                analyzer_alias=analyzer_alias,
                code="IBKR_OPTIONS_EXERCISE_ASSIGNMENT_NO_CLOSEDLOT",
                message=(
                    "Equity/index option exercise or assignment rows without ClosedLot did not create "
                    "standalone option taxable events."
                ),
                params={
                    "count": summary.option_exercise_assignment_without_closedlot_rows,
                    "rows": summary.option_exercise_assignment_details,
                },
            )
        )
    if summary.option_unhandled_trade_rows > 0:
        legacy_diagnostics.append(
            AnalysisDiagnostic(
                severity="WARNING",
                analyzer_alias=analyzer_alias,
                code="IBKR_OPTIONS_UNHANDLED_ROWS",
                message="Equity/index option rows require review because no attached ClosedLot was found.",
                params={
                    "count": summary.option_unhandled_trade_rows,
                    "rows": summary.option_unhandled_trade_details,
                },
            )
        )
    diagnostics = normalize_diagnostics(legacy_diagnostics)

    appendices: list[AppendixRecord] = []

    app5 = summary.appendix_5
    appendices.append(
        AppendixRecord(
            appendix="5",
            part=None,
            table="2",
            code="508",
            values={
                "sale_value_eur": app5.sale_price_eur,
                "acquisition_value_eur": app5.purchase_eur,
                "profit_eur": app5.wins_eur,
                "loss_eur": app5.losses_eur,
                "trade_count": app5.rows,
                "net_result_eur": app5.wins_eur - app5.losses_eur,
            },
        )
    )

    app13 = summary.appendix_13
    appendices.append(
        AppendixRecord(
            appendix="13",
            part="II",
            table="",
            code="5081",
            values={
                "gross_income_eur": app13.sale_price_eur,
                "acquisition_value_eur": app13.purchase_eur,
                "profit_eur": app13.wins_eur,
                "loss_eur": app13.losses_eur,
                "trade_count": app13.rows,
                "net_result_eur": app13.wins_eur - app13.losses_eur,
            },
        )
    )

    appendices.append(
        AppendixRecord(
            appendix="6",
            part="I",
            code="603",
            values={"row_kind": "total_by_code", "amount_eur": summary.appendix_6_code_603_eur},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="I",
            code="606",
            values={"row_kind": "total_by_code", "amount_eur": summary.appendix_6_code_606_eur},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="II",
            code="603",
            values={"taxable_income_eur": summary.appendix_6_code_603_eur},
        )
    )
    appendices.append(
        AppendixRecord(
            appendix="6",
            part="II",
            code="606",
            values={"taxable_income_eur": summary.appendix_6_code_606_eur},
        )
    )

    for row in summary.appendix_8_part1_rows:
        appendices.append(
            AppendixRecord(
                appendix="8",
                part="I",
                code=None,
                values={
                    "asset_type": "Акции",
                    "country": row.country_bulgarian,
                    "currency": row.cost_basis_original_currency or "-",
                    "quantity": row.quantity,
                    "acquisition_native": row.cost_basis_original,
                    "acquisition_eur": row.cost_basis_eur,
                },
            )
        )

    for row in summary.appendix_8_output_rows:
        appendices.append(
            AppendixRecord(
                appendix="8",
                part="III",
                code="8141",
                values={
                    "payer": row.payer_name,
                    "country": row.country_bulgarian,
                    "treaty_method": row.method_code,
                    "gross_income_eur": row.gross_dividend_eur,
                    "foreign_tax_eur": row.foreign_tax_paid_eur,
                    "allowable_credit_eur": row.allowable_credit_eur,
                    "recognized_credit_eur": row.recognized_credit_eur,
                    "tax_due_eur": row.tax_due_bg_eur,
                },
            )
        )

    for country in summary.appendix_9_country_results.values():
        appendices.append(
            AppendixRecord(
                appendix="9",
                part="II",
                code="603",
                values={
                    "country": country.country_bulgarian,
                    "gross_income_eur": country.aggregated_gross_eur,
                    "tax_base_eur": country.aggregated_gross_eur,
                    "foreign_tax_eur": country.aggregated_foreign_tax_paid_eur,
                    "allowable_credit_eur": country.allowable_credit_aggregated_eur,
                    "recognized_credit_eur": country.recognized_credit_correct_eur,
                    "document_ref": "",
                },
            )
        )

    return TaxAnalysisResult(
        analyzer_alias=analyzer_alias,
        input_path=input_path.resolve(),
        tax_year=tax_year,
        output_paths=normalized_output_paths,
        appendices=appendices,
        diagnostics=diagnostics,
        spb8_rows=summary.spb8_rows,
        spb8_notes=summary.spb8_notes,
        spb8_corporate_actions_present=summary.spb8_corporate_actions_present,
        main_report_notes=analysis_settings_main_report_notes(summary),
        generated_artifacts=_generated_artifacts_from_output_paths(normalized_output_paths),
        policy_notes=cfd_pil_policy_notes(summary),
        policy_audit_lines=(
            cfd_pil_policy_audit_lines(summary)
            + futures_policy_audit_lines(summary)
            + options_policy_audit_lines(summary)
        ),
    )
