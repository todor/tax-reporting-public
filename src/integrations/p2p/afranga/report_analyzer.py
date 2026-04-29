from __future__ import annotations

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
    validate_secondary_market_mode,
)
from .afranga_parser import parse_afranga_pdf
from .constants import DEFAULT_OUTPUT_DIR
from .models import AfrangaAnalyzerError

def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise AfrangaAnalyzerError(f"invalid tax year: {tax_year}")


def analyze_afranga_report(
    *,
    input_pdf: str | Path,
    tax_year: int,
    output_dir: str | Path | None = None,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
    display_currency: str = "EUR",
    cache_dir: str | Path | None = None,
) -> P2PAnalysisRunResult:
    _validate_tax_year(tax_year)

    try:
        validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)
        result = parse_afranga_pdf(
            input_pdf=input_pdf,
            secondary_market_mode=secondary_market_mode,
        )
    except AfrangaAnalyzerError:
        raise
    except (P2PAnalyzerError, P2PValidationError) as exc:
        raise AfrangaAnalyzerError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise AfrangaAnalyzerError(str(exc)) from exc

    if result.tax_year is not None and result.tax_year != tax_year:
        result.warnings.append(
            f"reporting year in PDF ({result.tax_year}) differs from requested tax year ({tax_year})"
        )

    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    output_txt_path = build_appendix6_output_path(
        input_path=input_pdf,
        output_dir=out_dir,
        stem_fallback="afranga_report",
    )
    write_appendix6_text(
        output_txt_path,
        result=result,
        tax_year=tax_year,
        display_currency=display_currency,
        cache_dir=cache_dir,
    )

    return P2PAnalysisRunResult(
        input_path=Path(input_pdf).expanduser().resolve(),
        output_txt_path=output_txt_path,
        result=result,
    )
