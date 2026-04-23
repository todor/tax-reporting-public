from __future__ import annotations

import argparse
import logging
from pathlib import Path

from integrations.p2p.shared.appendix6_models import (
    P2PAnalysisRunResult,
    P2PAnalyzerError,
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_6,
)
from integrations.p2p.shared.appendix6_renderer import write_appendix6_text
from integrations.p2p.shared.runtime import (
    build_appendix6_output_path,
    build_p2p_run_cli_summary_lines,
    validate_secondary_market_mode,
)

from .constants import DEFAULT_OUTPUT_DIR, SECONDARY_MARKET_MODE_HELP
from .estateguru_parser import parse_estateguru_pdf
from .models import EstateguruAnalyzerError

logger = logging.getLogger(__name__)


def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise EstateguruAnalyzerError(f"invalid tax year: {tax_year}")


def analyze_estateguru_report(
    *,
    input_pdf: str | Path,
    tax_year: int,
    output_dir: str | Path | None = None,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAnalysisRunResult:
    _validate_tax_year(tax_year)

    try:
        validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)
        result = parse_estateguru_pdf(
            input_pdf=input_pdf,
            secondary_market_mode=secondary_market_mode,
        )
    except EstateguruAnalyzerError:
        raise
    except (P2PAnalyzerError, P2PValidationError) as exc:
        raise EstateguruAnalyzerError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise EstateguruAnalyzerError(str(exc)) from exc

    if result.tax_year is not None and result.tax_year != tax_year:
        result.warnings.append(
            f"reporting year in PDF ({result.tax_year}) differs from requested tax year ({tax_year})"
        )

    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    output_txt_path = build_appendix6_output_path(
        input_path=input_pdf,
        output_dir=out_dir,
        stem_fallback="estateguru_report",
    )
    write_appendix6_text(output_txt_path, result=result)

    return P2PAnalysisRunResult(
        input_path=Path(input_pdf).expanduser().resolve(),
        output_txt_path=output_txt_path,
        result=result,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="estateguru-report-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="Estateguru Income Statement PDF")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
    parser.add_argument(
        "--secondary-market-mode",
        default=SECONDARY_MARKET_MODE_APPENDIX_6,
        help=SECONDARY_MARKET_MODE_HELP,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
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
        run_result = analyze_estateguru_report(
            input_pdf=args.input,
            tax_year=args.tax_year,
            output_dir=args.output_dir,
            secondary_market_mode=args.secondary_market_mode,
        )
    except EstateguruAnalyzerError as exc:
        logger.error("%s", exc)
        print("STATUS: ERROR")
        return 2

    for line in build_p2p_run_cli_summary_lines(
        result=run_result.result,
        output_txt_path=run_result.output_txt_path,
    ):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
