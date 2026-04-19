from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from services.bnb_fx import BnbFxError
from services.bnb_fx import get_exchange_rate
from services.crypto_fx import get_crypto_eur_rate

from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from integrations.crypto.shared.crypto_outputs import (
    build_ir_run_cli_summary_lines,
    load_holdings_state_json,
    write_declaration_text,
    write_enriched_ir_csv,
    write_holdings_state_json,
)
from integrations.crypto.shared.generic_crypto_analyzer import analyze_ir_rows

from .coinbase_to_ir import load_and_map_coinbase_csv_to_ir
from .constants import DEFAULT_OUTPUT_DIR
from .models import AnalysisResult, CoinbaseAnalyzerError

# Re-export shared summary type for Coinbase analyzer callers.
AnalysisSummary = IrAnalysisSummary

logger = logging.getLogger(__name__)

EurUnitRateProvider = Callable[[str, datetime], Decimal]


def _default_eur_unit_rate_provider(cache_dir: str | Path | None) -> EurUnitRateProvider:
    def provider(currency: str, timestamp: datetime) -> Decimal:
        normalized = currency.strip().upper()
        if normalized == "EUR":
            return Decimal("1")

        try:
            fx = get_exchange_rate(normalized, timestamp.date(), cache_dir=cache_dir)
            return fx.rate
        except BnbFxError:
            pass

        fx_crypto = get_crypto_eur_rate(
            normalized,
            timestamp,
            "binance",
            cache_dir=cache_dir,
        )
        return fx_crypto.price_eur

    return provider


def _output_stem(input_path: Path) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", input_path.stem).strip("_").lower()
    return normalized or "coinbase_report"


def _output_paths(*, input_path: Path, output_dir: Path) -> tuple[Path, Path]:
    stem = _output_stem(input_path)
    return (
        # Primary CSV output is the enriched IR export for audit/debug/tax fields.
        output_dir / f"{stem}_modified.csv",
        output_dir / f"{stem}_declaration.txt",
    )


def _state_output_path(*, input_path: Path, output_dir: Path, tax_year: int) -> Path:
    stem = _output_stem(input_path)
    return output_dir / f"{stem}_state_end_{tax_year}.json"


def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise CoinbaseAnalyzerError(f"invalid tax year: {tax_year}")


def analyze_coinbase_report(
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
        else _default_eur_unit_rate_provider(cache_dir)
    )

    ir_summary = IrAnalysisSummary()
    try:
        mapping = load_and_map_coinbase_csv_to_ir(
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
        if isinstance(exc, CoinbaseAnalyzerError):
            raise
        raise CoinbaseAnalyzerError(str(exc)) from exc

    output_csv_path, declaration_txt_path = _output_paths(input_path=loaded.input_path, output_dir=out_dir)
    year_end_state_json_path = _state_output_path(
        input_path=loaded.input_path,
        output_dir=out_dir,
        tax_year=tax_year,
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
    parser = argparse.ArgumentParser(prog="coinbase-report-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="Coinbase transaction report CSV")
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
        result = analyze_coinbase_report(
            input_csv=args.input,
            tax_year=args.tax_year,
            opening_state_json=args.opening_state_json,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except CoinbaseAnalyzerError as exc:
        logger.error("%s", exc)
        return 2

    for line in build_ir_run_cli_summary_lines(result=result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
