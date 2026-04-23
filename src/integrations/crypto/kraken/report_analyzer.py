from __future__ import annotations

import argparse
import logging
from decimal import Decimal
from pathlib import Path

from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from integrations.crypto.shared.crypto_outputs import (
    build_ir_run_cli_summary_lines,
    load_holdings_state_json,
    write_declaration_text,
    write_enriched_ir_csv,
    write_holdings_state_json,
)
from integrations.crypto.shared.generic_crypto_analyzer import analyze_ir_rows
from integrations.crypto.shared.runtime import (
    EurUnitRateProvider,
    build_enriched_ir_output_paths,
    default_eur_unit_rate_provider,
)

from .constants import DEFAULT_OUTPUT_DIR
from .kraken_to_ir import load_and_map_kraken_csv_to_ir
from .models import AnalysisResult, KrakenAnalyzerError

# Re-export shared summary type for Kraken analyzer callers.
AnalysisSummary = IrAnalysisSummary

logger = logging.getLogger(__name__)


def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise KrakenAnalyzerError(f"invalid tax year: {tax_year}")


def analyze_kraken_report(
    *,
    input_csv: str | Path,
    tax_year: int,
    opening_state_json: str | Path | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    eur_unit_rate_provider: EurUnitRateProvider | None = None,
) -> AnalysisResult:
    _validate_tax_year(tax_year)
    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rate_provider = (
        eur_unit_rate_provider
        if eur_unit_rate_provider is not None
        else default_eur_unit_rate_provider(cache_dir=cache_dir)
    )

    ir_summary = IrAnalysisSummary()
    try:
        mapping = load_and_map_kraken_csv_to_ir(
            input_csv=str(input_csv),
            summary=ir_summary,
            eur_unit_rate_provider=rate_provider,
        )
        loaded = mapping.loaded_csv
        ir_summary.processed_rows = len(loaded.rows)
        ir_summary.preamble_rows_ignored = loaded.preamble_rows_ignored

        opening_holdings: dict[str, tuple[Decimal, Decimal]] | None = None
        if opening_state_json is not None:
            opening_state_path = Path(opening_state_json).expanduser().resolve()
            opening_year_end, opening_holdings = load_holdings_state_json(opening_state_path)
            if opening_year_end is not None and opening_year_end >= tax_year:
                ir_summary.warnings.append(
                    "opening state year is not before requested tax year "
                    f"(state_tax_year_end={opening_year_end}, tax_year={tax_year})"
                )

        analysis = analyze_ir_rows(
            ir_rows=mapping.ir_rows,
            tax_year=tax_year,
            summary=ir_summary,
            opening_holdings=opening_holdings,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, KrakenAnalyzerError):
            raise
        raise KrakenAnalyzerError(str(exc)) from exc

    output_csv_path, declaration_txt_path, year_end_state_json_path = build_enriched_ir_output_paths(
        input_path=loaded.input_path,
        output_dir=out_dir,
        tax_year=tax_year,
        stem_fallback="kraken_report",
    )

    write_enriched_ir_csv(output_csv_path, rows=analysis.enriched_rows)
    write_declaration_text(declaration_txt_path, summary=analysis.summary)
    write_holdings_state_json(
        year_end_state_json_path,
        tax_year=tax_year,
        holdings_by_asset=analysis.year_end_holdings_by_asset,
    )

    return AnalysisResult(
        input_csv_path=loaded.input_path,
        output_csv_path=output_csv_path,
        declaration_txt_path=declaration_txt_path,
        year_end_state_json_path=year_end_state_json_path,
        summary=analysis.summary,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kraken-report-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="Kraken ledger CSV")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
    parser.add_argument("--opening-state-json", type=Path, help="Optional prior year-end holdings state JSON")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--cache-dir", type=Path, help="Optional FX cache dir override")
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
        result = analyze_kraken_report(
            input_csv=args.input,
            tax_year=args.tax_year,
            opening_state_json=args.opening_state_json,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except KrakenAnalyzerError as exc:
        logger.error("%s", exc)
        print("STATUS: ERROR")
        return 2

    for line in build_ir_run_cli_summary_lines(result=result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
