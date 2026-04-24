from __future__ import annotations

import argparse

from integrations.shared.cli_helpers import CliMode, resolved_cache_dir
from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext
from integrations.shared.result_builders import build_binance_futures_result

from .futures_pnl_analyzer import DEFAULT_OUTPUT_DIR
from .futures_pnl_analyzer import analyze_futures_pnl_report


def _add_arguments(parser: argparse.ArgumentParser, mode: CliMode) -> None:
    _ = parser
    _ = mode


def _build_options(
    args: argparse.Namespace,
    mode: CliMode,
    group_options: dict[str, object],
) -> dict[str, object]:
    return {"cache_dir": resolved_cache_dir(args, mode=mode, group_options=group_options)}


def _run(context: AnalyzerRunContext):
    result = analyze_futures_pnl_report(
        input_csv=context.input_path,
        tax_year=context.tax_year,
        output_dir=context.output_dir,
        cache_dir=context.options.get("cache_dir"),
    )
    return build_binance_futures_result(
        analyzer_alias="binance_futures",
        input_path=result.input_csv_path,
        tax_year=context.tax_year,
        output_paths={
            "detailed_csv": result.detailed_csv_path,
            "declaration_txt": result.tax_text_path,
            "summary_json": result.summary_json_path,
        },
        sale_value_eur=result.totals.sale_price_eur,
        acquisition_value_eur=result.totals.purchase_price_eur,
        profit_eur=result.totals.profit_eur,
        loss_eur=result.totals.loss_eur,
        trade_count=result.totals.processed_rows,
    )


ANALYZER = AnalyzerDefinition(
    alias="binance_futures",
    group="crypto",
    aliases=("binance",),
    description="Binance Futures PnL CSV analyzer",
    default_output_dir=DEFAULT_OUTPUT_DIR,
    input_suffixes=(".csv",),
    # Auto-detect mode uses OR-of-AND token sets extracted from filename stem.
    # This matches common report names like:
    # - "Binance Report PnL.csv"
    # - "binance_futures_*.csv"
    detection_token_sets=(("binance", "report", "pnl"), ("binance", "futures")),
    add_arguments=_add_arguments,
    build_options=_build_options,
    run=_run,
)
