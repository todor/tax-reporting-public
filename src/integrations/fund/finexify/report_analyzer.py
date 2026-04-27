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


def _require_valid_opening_state_year(
    *,
    tax_year: int,
    opening_year_end: int | None,
    opening_state_path: Path,
) -> int:
    if opening_year_end is None:
        raise FinexifyAnalyzerError(
            "invalid opening state metadata: missing state_tax_year_end "
            f"(tax_year={tax_year}, state_tax_year_end=<missing>, state={opening_state_path}). "
            "Expected a closing state for any year strictly less than tax_year. "
            "Fix: pass a valid *_state_end_<year>.json where <year> < tax_year."
        )
    if opening_year_end >= tax_year:
        raise FinexifyAnalyzerError(
            "invalid opening state year: state_tax_year_end must be strictly less than tax_year "
            f"(tax_year={tax_year}, state_tax_year_end={opening_year_end}, state={opening_state_path}). "
            "Fix: pass a closing state from an earlier year, or run without --opening-state-json."
        )
    return opening_year_end


def analyze_finexify_report(
    *,
    input_csv: str | Path,
    tax_year: int,
    opening_state_json: str | Path | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    display_currency: str = "EUR",
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
        opening_year_end: int | None = None
        if opening_state_json is not None:
            opening_state_path = Path(opening_state_json).expanduser().resolve()
            opening_year_end, opening_state_by_currency = load_fund_state_json(opening_state_path)
            opening_year_end = _require_valid_opening_state_year(
                tax_year=tax_year,
                opening_year_end=opening_year_end,
                opening_state_path=opening_state_path,
            )

        mapping = load_and_map_finexify_csv_to_ir(
            input_csv=str(input_csv),
            summary=summary,
            tax_year=tax_year,
            opening_state_by_currency=opening_state_by_currency,
            opening_state_year_end=opening_year_end,
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
            opening_state_year_end=opening_year_end,
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
        tax_year=tax_year,
        display_currency=display_currency,
        cache_dir=cache_dir,
    )
    write_fund_state_json(
        year_end_state_json_path,
        tax_year=tax_year,
        state_by_currency=analysis.year_end_state_by_currency,
    )
    logger.info(
        "finexify state-window summary: tax_year=%s opening_state_year_end=%s "
        "loaded_rows=%s applied_rows=%s tax_year_rows=%s ignored_le_state_year=%s ignored_gt_tax_year=%s",
        tax_year,
        analysis.summary.opening_state_year_end,
        analysis.summary.processed_rows,
        analysis.summary.rows_applied_to_ledger,
        analysis.summary.rows_included_in_tax_year,
        analysis.summary.rows_ignored_before_or_equal_opening_state_year,
        analysis.summary.rows_ignored_after_tax_year,
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
    parser.add_argument(
        "--opening-state-json",
        type=Path,
        help=(
            "Optional opening state JSON. For --tax-year YYYY, state_tax_year_end in the state file "
            "must be < YYYY."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--cache-dir", type=Path, help="Optional FX cache dir override")
    parser.add_argument(
        "--display-currency",
        choices=["EUR", "BGN"],
        default="EUR",
        help=(
            "Controls ONLY TXT output rendering. "
            "All calculations and aggregation are performed in EUR. "
            "BGN rendering uses BNB FX service at tax year end."
        ),
    )
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
            display_currency=args.display_currency,
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
