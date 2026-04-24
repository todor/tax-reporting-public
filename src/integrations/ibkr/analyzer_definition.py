from __future__ import annotations

import argparse

from integrations.shared.cli_helpers import CliMode, add_mode_argument, option_value, resolved_cache_dir
from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext
from integrations.shared.result_builders import build_ibkr_result

from .activity_statement_analyzer import analyze_ibkr_activity_statement
from .constants import (
    APPENDIX8_LIST_MODE_COMPANY,
    APPENDIX8_LIST_MODE_COUNTRY,
    DEFAULT_OUTPUT_DIR,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTED_SYMBOL,
)

_TAX_EXEMPT_MODE_ALIASES = {
    "execution": TAX_MODE_EXECUTION_EXCHANGE,
    "execution_exchange": TAX_MODE_EXECUTION_EXCHANGE,
    "listed": TAX_MODE_LISTED_SYMBOL,
    "listed_symbol": TAX_MODE_LISTED_SYMBOL,
}


def _normalize_tax_exempt_mode(value: str) -> str:
    normalized = value.strip().lower()
    resolved = _TAX_EXEMPT_MODE_ALIASES.get(normalized)
    if resolved is None:
        return value
    return resolved


def _add_arguments(parser: argparse.ArgumentParser, mode: CliMode) -> None:
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="tax-exempt-mode",
        choices=sorted(_TAX_EXEMPT_MODE_ALIASES),
        required=(mode == "single"),
        help="Tax exempt classification mode",
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="appendix8-dividend-list-mode",
        choices=[APPENDIX8_LIST_MODE_COMPANY, APPENDIX8_LIST_MODE_COUNTRY],
        default=APPENDIX8_LIST_MODE_COMPANY,
        help=argparse.SUPPRESS,
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="eu-regulated-exchange",
        action="append",
        default=[],
        help="Additional EU-regulated exchange override for IBKR (repeatable or comma-separated)",
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="closed-world",
        action="store_true",
        help="Force IBKR closed-world exchange classification mode",
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="report-alias",
        type=str,
        help="Optional report alias for IBKR output filenames",
    )


def _build_options(
    args: argparse.Namespace,
    mode: CliMode,
    group_options: dict[str, object],
) -> dict[str, object]:
    return {
        "tax_exempt_mode": _normalize_tax_exempt_mode(
            option_value(
                args,
                mode=mode,
                single_attr="tax_exempt_mode",
                aggregate_attr="ibkr_tax_exempt_mode",
                default="listed_symbol",
            )
        ),
        "appendix8_dividend_list_mode": option_value(
            args,
            mode=mode,
            single_attr="appendix8_dividend_list_mode",
            aggregate_attr="ibkr_appendix8_dividend_list_mode",
            default=APPENDIX8_LIST_MODE_COMPANY,
        ),
        "eu_regulated_exchanges": option_value(
            args,
            mode=mode,
            single_attr="eu_regulated_exchange",
            aggregate_attr="ibkr_eu_regulated_exchange",
            default=[],
        ),
        "closed_world": bool(
            option_value(
                args,
                mode=mode,
                single_attr="closed_world",
                aggregate_attr="ibkr_closed_world",
                default=False,
            )
        ),
        "report_alias": option_value(
            args,
            mode=mode,
            single_attr="report_alias",
            aggregate_attr="ibkr_report_alias",
            default=None,
        ),
        "cache_dir": resolved_cache_dir(args, mode=mode, group_options=group_options),
    }


def _run(context: AnalyzerRunContext):
    result = analyze_ibkr_activity_statement(
        input_csv=context.input_path,
        tax_year=context.tax_year,
        tax_exempt_mode=str(context.options["tax_exempt_mode"]),
        appendix8_dividend_list_mode=str(context.options["appendix8_dividend_list_mode"]),
        report_alias=context.options.get("report_alias"),
        output_dir=context.output_dir,
        cache_dir=context.options.get("cache_dir"),
        eu_regulated_exchanges=context.options.get("eu_regulated_exchanges"),
        closed_world=bool(context.options.get("closed_world")),
    )
    return build_ibkr_result(
        analyzer_alias="ibkr",
        input_path=result.input_csv_path,
        tax_year=context.tax_year,
        output_paths={
            "modified_csv": result.output_csv_path,
            "declaration_txt": result.declaration_txt_path,
        },
        summary=result.summary,
    )


ANALYZER = AnalyzerDefinition(
    alias="ibkr",
    group="",
    aliases=("interactive_brokers", "interactivebrokers"),
    description="IBKR activity statement CSV analyzer",
    default_output_dir=DEFAULT_OUTPUT_DIR,
    input_suffixes=(".csv",),
    detection_token_sets=(("ibkr",), ("interactive", "brokers")),
    add_arguments=_add_arguments,
    build_options=_build_options,
    run=_run,
)
