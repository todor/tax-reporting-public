from __future__ import annotations

import argparse

from integrations.shared.cli_helpers import CliMode, add_mode_argument, option_value
from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext
from integrations.shared.result_builders import build_p2p_result

from .constants import DEFAULT_OUTPUT_DIR
from .report_analyzer import analyze_afranga_report


def _add_arguments(parser: argparse.ArgumentParser, mode: CliMode) -> None:
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="afranga",
        single_flag="secondary-market-mode",
        type=str,
        help="Afranga secondary-market mode override",
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
        aggregate_attr="afranga_secondary_market_mode",
        group_options=group_options,
        group_key="p2p_secondary_market_mode",
        default="appendix_6",
    )
    return {"secondary_market_mode": str(secondary_market_mode)}


def _run(context: AnalyzerRunContext):
    run_result = analyze_afranga_report(
        input_pdf=context.input_path,
        tax_year=context.tax_year,
        output_dir=context.output_dir,
        secondary_market_mode=str(context.options["secondary_market_mode"]),
    )
    return build_p2p_result(
        analyzer_alias="afranga",
        input_path=run_result.input_path,
        tax_year=context.tax_year,
        output_paths={
            "declaration_txt": run_result.output_txt_path,
        },
        result=run_result.result,
    )


ANALYZER = AnalyzerDefinition(
    alias="afranga",
    group="p2p",
    aliases=(),
    default_output_dir=DEFAULT_OUTPUT_DIR,
    description="Afranga PDF analyzer",
    input_suffixes=(".pdf",),
    detection_token_sets=(("afranga",),),
    add_arguments=_add_arguments,
    build_options=_build_options,
    run=_run,
)
