from __future__ import annotations

import argparse

from integrations.shared.cli_helpers import CliMode, add_mode_argument, option_value, resolved_cache_dir
from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext
from integrations.shared.result_builders import build_p2p_result

from .constants import DEFAULT_OUTPUT_DIR
from .report_analyzer import analyze_robocash_report


def _add_arguments(parser: argparse.ArgumentParser, mode: CliMode) -> None:
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="robocash",
        single_flag="secondary-market-mode",
        type=str,
        help="Robocash secondary-market mode override",
    )


def _build_options(
    args: argparse.Namespace,
    mode: CliMode,
    group_options: dict[str, object],
) -> dict[str, object]:
    secondary_market_mode = option_value(
        args,
        mode=mode,
        single_attr="secondary_market_mode",
        aggregate_attr="robocash_secondary_market_mode",
        group_options=group_options,
        group_key="p2p_secondary_market_mode",
        default="appendix_6",
    )
    return {
        "secondary_market_mode": str(secondary_market_mode),
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
    run_result = analyze_robocash_report(
        input_pdf=context.input_path,
        tax_year=context.tax_year,
        output_dir=context.output_dir,
        secondary_market_mode=str(context.options["secondary_market_mode"]),
        display_currency=str(context.options.get("display_currency", "EUR")),
        cache_dir=context.options.get("cache_dir"),
    )
    return build_p2p_result(
        analyzer_alias="robocash",
        input_path=run_result.input_path,
        tax_year=context.tax_year,
        output_paths={
            "declaration_txt": run_result.output_txt_path,
        },
        result=run_result.result,
    )


ANALYZER = AnalyzerDefinition(
    alias="robocash",
    group="p2p",
    aliases=(),
    default_output_dir=DEFAULT_OUTPUT_DIR,
    description="Robocash PDF analyzer",
    input_suffixes=(".pdf",),
    detection_token_sets=(("robocash",),),
    add_arguments=_add_arguments,
    build_options=_build_options,
    run=_run,
)
