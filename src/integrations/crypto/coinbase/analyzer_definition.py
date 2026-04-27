from __future__ import annotations

import argparse

from integrations.shared.cli_helpers import CliMode, add_mode_argument, option_value, resolved_cache_dir
from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext
from integrations.shared.result_builders import build_crypto_result

from .constants import DEFAULT_OUTPUT_DIR
from .report_analyzer import analyze_coinbase_report


def _add_arguments(parser: argparse.ArgumentParser, mode: CliMode) -> None:
    add_mode_argument(
        parser,
        mode=mode,
        analyzer_alias="coinbase",
        single_flag="opening-state-json",
        type=str,
        help="Optional opening holdings state JSON; for --tax-year YYYY state_tax_year_end must be < YYYY",
    )


def _build_options(
    args: argparse.Namespace,
    mode: CliMode,
    group_options: dict[str, object],
) -> dict[str, object]:
    return {
        "opening_state_json": option_value(
            args,
            mode=mode,
            single_attr="opening_state_json",
            aggregate_attr="coinbase_opening_state_json",
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
    result = analyze_coinbase_report(
        input_csv=context.input_path,
        tax_year=context.tax_year,
        opening_state_json=context.options.get("opening_state_json"),
        output_dir=context.output_dir,
        cache_dir=context.options.get("cache_dir"),
        display_currency=str(context.options.get("display_currency", "EUR")),
    )
    return build_crypto_result(
        analyzer_alias="coinbase",
        input_path=result.input_csv_path,
        tax_year=context.tax_year,
        output_paths={
            "enriched_ir_csv": result.output_csv_path,
            "declaration_txt": result.declaration_txt_path,
            "state_json": result.year_end_state_json_path,
        },
        summary=result.summary,
        declaration_code="5082",
    )


ANALYZER = AnalyzerDefinition(
    alias="coinbase",
    group="crypto",
    aliases=(),
    description="Coinbase CSV analyzer",
    default_output_dir=DEFAULT_OUTPUT_DIR,
    input_suffixes=(".csv",),
    detection_token_sets=(("coinbase",),),
    add_arguments=_add_arguments,
    build_options=_build_options,
    run=_run,
)
