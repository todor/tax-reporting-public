from __future__ import annotations

import argparse

from integrations.shared.cli_helpers import CliMode, add_mode_argument, option_value, resolved_cache_dir
from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext
from integrations.shared.result_builders import build_ibkr_result

from .activity_statement_analyzer import analyze_ibkr_activity_statement
from .constants import (
    APPENDIX8_LIST_MODE_COMPANY,
    APPENDIX8_LIST_MODE_COUNTRY,
    DEFAULT_TAX_EXEMPT_MODE,
    DEFAULT_OUTPUT_DIR,
    NEGATIVE_PIL_MODE_POSITION_AWARE,
    NEGATIVE_PIL_MODES,
    TAX_MODE_EXECUTION_EXCHANGE,
    TAX_MODE_LISTING_EXCHANGE,
)

_TAX_EXEMPT_MODES = (TAX_MODE_EXECUTION_EXCHANGE, TAX_MODE_LISTING_EXCHANGE)
_IBKR_SUPPORTED_AGGREGATE_OVERRIDES = frozenset(
    {
        "tax_exempt_mode",
        "appendix8_dividend_list_mode",
        "eu_regulated_exchange",
        "closed_world",
        "skip_period_validation",
        "no_net_cfd_financing",
        "negative_pil_mode",
    }
)


def _normalize_tax_exempt_mode(value: str) -> str:
    return value.strip().lower()


def _add_arguments(parser: argparse.ArgumentParser, mode: CliMode) -> None:
    if mode == "aggregate":
        return
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="tax-exempt-mode",
        choices=sorted(_TAX_EXEMPT_MODES),
        help="Tax exempt classification mode",
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="appendix8-dividend-list-mode",
        choices=[APPENDIX8_LIST_MODE_COMPANY, APPENDIX8_LIST_MODE_COUNTRY],
        default=APPENDIX8_LIST_MODE_COMPANY,
        help="Appendix 8 dividend listing mode",
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
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="skip-period-validation",
        action="store_true",
        help="Skip strict IBKR full-year statement period validation; for development/testing only",
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="no-net-cfd-financing",
        action="store_true",
        help="Do not net IBKR CFD financing into Appendix 5; positive amounts go to Appendix 6 code 606",
    )
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="ibkr",
        single_flag="negative-pil-mode",
        choices=sorted(NEGATIVE_PIL_MODES),
        default=NEGATIVE_PIL_MODE_POSITION_AWARE,
        help="Negative IBKR Payment in Lieu handling mode",
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
                group_options=group_options,
                group_key="tax_exempt_mode",
                default=DEFAULT_TAX_EXEMPT_MODE,
            )
        ),
        "appendix8_dividend_list_mode": option_value(
            args,
            mode=mode,
            single_attr="appendix8_dividend_list_mode",
            group_options=group_options,
            group_key="appendix8_dividend_list_mode",
            default=APPENDIX8_LIST_MODE_COMPANY,
        ),
        "eu_regulated_exchanges": option_value(
            args,
            mode=mode,
            single_attr="eu_regulated_exchange",
            group_options=group_options,
            group_key="eu_regulated_exchange",
            default=[],
        ),
        "closed_world": bool(
            option_value(
                args,
                mode=mode,
                single_attr="closed_world",
                group_options=group_options,
                group_key="closed_world",
                default=False,
            )
        ),
        "report_alias": option_value(
            args,
            mode=mode,
            single_attr="report_alias",
            default=None,
        ),
        "skip_period_validation": bool(
            option_value(
                args,
                mode=mode,
                single_attr="skip_period_validation",
                group_options=group_options,
                group_key="skip_period_validation",
                default=False,
            )
        ),
        "net_cfd_financing": not bool(
            option_value(
                args,
                mode=mode,
                single_attr="no_net_cfd_financing",
                group_options=group_options,
                group_key="no_net_cfd_financing",
                default=False,
            )
        ),
        "negative_pil_mode": str(
            option_value(
                args,
                mode=mode,
                single_attr="negative_pil_mode",
                group_options=group_options,
                group_key="negative_pil_mode",
                default=NEGATIVE_PIL_MODE_POSITION_AWARE,
            )
        ),
        "display_currency": str(
            option_value(
                args,
                mode=mode,
                single_attr="display_currency",
                group_options=group_options,
                group_key="display_currency",
                default="EUR",
            )
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
        display_currency=str(context.options.get("display_currency", "EUR")),
        eu_regulated_exchanges=context.options.get("eu_regulated_exchanges"),
        closed_world=bool(context.options.get("closed_world")),
        skip_period_validation=bool(context.options.get("skip_period_validation")),
        net_cfd_financing=bool(context.options.get("net_cfd_financing", True)),
        negative_pil_mode=str(context.options.get("negative_pil_mode", NEGATIVE_PIL_MODE_POSITION_AWARE)),
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
    supported_aggregate_overrides=_IBKR_SUPPORTED_AGGREGATE_OVERRIDES,
)
