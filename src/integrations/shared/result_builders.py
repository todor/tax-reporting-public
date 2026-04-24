from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from integrations.fund.shared.fund_ir_models import FundAnalysisSummary
from integrations.ibkr.appendices.declaration_text import _build_manual_check_reasons
from integrations.ibkr.models import AnalysisSummary as IbkrAnalysisSummary
from integrations.p2p.shared.appendix6_models import P2PAppendix6Result

from .contracts import AnalysisDiagnostic, AppendixRecord, TaxAnalysisResult


def _output_paths_to_path_map(paths: dict[str, str | Path]) -> dict[str, Path]:
    return {
        key: (value if isinstance(value, Path) else Path(value)).expanduser().resolve()
        for key, value in paths.items()
    }


def build_crypto_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    summary: IrAnalysisSummary,
    declaration_code: str = "5082",
) -> TaxAnalysisResult:
    diagnostics: list[AnalysisDiagnostic] = []
    diagnostics.extend(
        AnalysisDiagnostic(severity="WARNING", message=warning, analyzer_alias=analyzer_alias)
        for warning in summary.warnings
    )
    diagnostics.extend(
        AnalysisDiagnostic(severity="MANUAL_REVIEW", message=reason, analyzer_alias=analyzer_alias)
        for reason in summary.manual_check_reasons
    )

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
        output_paths=_output_paths_to_path_map(output_paths),
        appendices=appendices,
        diagnostics=diagnostics,
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
    diagnostics: list[AnalysisDiagnostic] = []
    diagnostics.extend(
        AnalysisDiagnostic(severity="WARNING", message=warning, analyzer_alias=analyzer_alias)
        for warning in summary.warnings
    )
    diagnostics.extend(
        AnalysisDiagnostic(severity="MANUAL_REVIEW", message=reason, analyzer_alias=analyzer_alias)
        for reason in summary.manual_check_reasons
    )

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
        output_paths=_output_paths_to_path_map(output_paths),
        appendices=appendices,
        diagnostics=diagnostics,
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
    diagnostics = [
        AnalysisDiagnostic(severity="WARNING", message=warning, analyzer_alias=analyzer_alias)
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
        output_paths=_output_paths_to_path_map(output_paths),
        appendices=appendices,
        diagnostics=diagnostics,
    )


def build_p2p_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    result: P2PAppendix6Result,
) -> TaxAnalysisResult:
    diagnostics: list[AnalysisDiagnostic] = []
    diagnostics.extend(
        AnalysisDiagnostic(severity="MANUAL_REVIEW", message=warning, analyzer_alias=analyzer_alias)
        for warning in result.warnings
    )
    diagnostics.extend(
        AnalysisDiagnostic(severity="INFO", message=note, analyzer_alias=analyzer_alias)
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
        output_paths=_output_paths_to_path_map(output_paths),
        appendices=appendices,
        diagnostics=diagnostics,
    )


def build_ibkr_result(
    *,
    analyzer_alias: str,
    input_path: Path,
    tax_year: int,
    output_paths: dict[str, str | Path],
    summary: IbkrAnalysisSummary,
) -> TaxAnalysisResult:
    diagnostics: list[AnalysisDiagnostic] = []
    diagnostics.extend(
        AnalysisDiagnostic(severity="WARNING", message=warning, analyzer_alias=analyzer_alias)
        for warning in summary.warnings
    )
    diagnostics.extend(
        AnalysisDiagnostic(severity="MANUAL_REVIEW", message=reason, analyzer_alias=analyzer_alias)
        for reason in _build_manual_check_reasons(summary)
    )

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
            part="II",
            code="603",
            values={"taxable_income_eur": summary.appendix_6_code_603_eur},
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
                    "document_ref": "R-185 / Activity Statement",
                },
            )
        )

    return TaxAnalysisResult(
        analyzer_alias=analyzer_alias,
        input_path=input_path.resolve(),
        tax_year=tax_year,
        output_paths=_output_paths_to_path_map(output_paths),
        appendices=appendices,
        diagnostics=diagnostics,
    )

