from __future__ import annotations

import argparse
import logging
from pathlib import Path

from integrations.crypto.shared.runtime import (
    build_enriched_ir_output_paths,
)
from integrations.fund.shared.fund_ir_models import FundAnalysisSummary
from integrations.fund.shared.fund_outputs import (
    build_fund_run_cli_summary_lines,
    load_fund_state_json,
    write_declaration_text,
    write_enriched_ir_csv,
    write_fund_state_json,
)
from integrations.fund.shared.generic_fund_analyzer import analyze_fund_ir_rows
from integrations.fund.shared.runtime import (
    FundEurUnitRateProvider,
    default_fund_eur_unit_rate_provider,
)

from .constants import APPENDIX_5_DECLARATION_CODE, DEFAULT_OUTPUT_DIR
from .finexify_to_ir import load_and_map_finexify_csv_to_ir
from .models import AnalysisResult, FinexifyAnalyzerError

# Re-export shared summary type for Finexify analyzer callers.
AnalysisSummary = FundAnalysisSummary

logger = logging.getLogger(__name__)


def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise FinexifyAnalyzerError(f"invalid tax year: {tax_year}")


def analyze_finexify_report(
    *,
    input_csv: str | Path,
    tax_year: int,
    opening_state_json: str | Path | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    eur_unit_rate_provider: FundEurUnitRateProvider | None = None,
) -> AnalysisResult:
    _validate_tax_year(tax_year)
    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rate_provider = (
        eur_unit_rate_provider
        if eur_unit_rate_provider is not None
        else default_fund_eur_unit_rate_provider(cache_dir=cache_dir)
    )

    summary = FundAnalysisSummary()
    try:
        opening_state_by_currency = None
        if opening_state_json is not None:
            opening_state_path = Path(opening_state_json).expanduser().resolve()
            opening_year_end, opening_state_by_currency = load_fund_state_json(opening_state_path)
            if opening_year_end is not None and opening_year_end >= tax_year:
                summary.warnings.append(
                    "opening state year is not before requested tax year "
                    f"(state_tax_year_end={opening_year_end}, tax_year={tax_year})"
                )

        mapping = load_and_map_finexify_csv_to_ir(
            input_csv=str(input_csv),
            summary=summary,
            opening_state_by_currency=opening_state_by_currency,
        )
        loaded = mapping.loaded_csv
        summary.processed_rows = len(loaded.rows)
        summary.preamble_rows_ignored = loaded.preamble_rows_ignored

        analysis = analyze_fund_ir_rows(
            ir_rows=mapping.ir_rows,
            tax_year=tax_year,
            summary=summary,
            eur_unit_rate_provider=rate_provider,
            opening_state_by_currency=opening_state_by_currency,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, FinexifyAnalyzerError):
            raise
        raise FinexifyAnalyzerError(str(exc)) from exc

    output_csv_path, declaration_txt_path, year_end_state_json_path = build_enriched_ir_output_paths(
        input_path=loaded.input_path,
        output_dir=out_dir,
        tax_year=tax_year,
        stem_fallback="finexify_report",
    )

    write_enriched_ir_csv(output_csv_path, rows=analysis.enriched_rows)
    write_declaration_text(
        declaration_txt_path,
        summary=analysis.summary,
        appendix_5_declaration_code=APPENDIX_5_DECLARATION_CODE,
    )
    write_fund_state_json(
        year_end_state_json_path,
        tax_year=tax_year,
        state_by_currency=analysis.year_end_state_by_currency,
    )

    return AnalysisResult(
        input_csv_path=loaded.input_path,
        output_csv_path=output_csv_path,
        declaration_txt_path=declaration_txt_path,
        year_end_state_json_path=year_end_state_json_path,
        summary=analysis.summary,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finexify-report-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="Finexify transaction CSV")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
    parser.add_argument("--opening-state-json", type=Path, help="Optional prior year-end state JSON")
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
        result = analyze_finexify_report(
            input_csv=args.input,
            tax_year=args.tax_year,
            opening_state_json=args.opening_state_json,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except FinexifyAnalyzerError as exc:
        logger.error("%s", exc)
        print("STATUS: ERROR")
        return 2

    for line in build_fund_run_cli_summary_lines(result=result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
