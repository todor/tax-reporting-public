from __future__ import annotations

import re
from pathlib import Path

from .appendix6_models import (
    P2PAppendix6Result,
    SECONDARY_MARKET_MODE_APPENDIX_5,
    SUPPORTED_SECONDARY_MARKET_MODES,
    UnsupportedSecondaryMarketModeError,
)

def build_appendix6_output_path(
    *,
    input_path: str | Path,
    output_dir: str | Path,
    stem_fallback: str,
) -> Path:
    src = Path(input_path).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", src.stem).strip("_").lower()
    stem = normalized or stem_fallback
    return out / f"{stem}_declaration.txt"


def validate_secondary_market_mode(
    *,
    mode: str,
    allow_appendix_5: bool = False,
) -> None:
    if mode not in SUPPORTED_SECONDARY_MARKET_MODES:
        raise UnsupportedSecondaryMarketModeError(
            "invalid secondary-market mode: "
            f"{mode!r} (supported: {', '.join(sorted(SUPPORTED_SECONDARY_MARKET_MODES))})"
        )
    if mode == SECONDARY_MARKET_MODE_APPENDIX_5 and not allow_appendix_5:
        raise UnsupportedSecondaryMarketModeError(
            "secondary-market mode 'appendix_5' is not supported yet"
        )


def build_p2p_run_cli_summary_lines(
    *,
    result: P2PAppendix6Result,
    output_txt_path: Path,
) -> list[str]:
    return [
        f"platform: {result.platform}",
        f"tax_year: {result.tax_year if result.tax_year is not None else '-'}",
        f"aggregate_code_603: {result.aggregate_code_603}",
        f"aggregate_code_606: {result.aggregate_code_606}",
        f"taxable_code_603: {result.taxable_code_603}",
        f"taxable_code_606: {result.taxable_code_606}",
        f"withheld_tax: {result.withheld_tax}",
        f"part1_rows: {len(result.part1_rows)}",
        f"warnings: {len(result.warnings)}",
        f"Declaration TXT: {output_txt_path}",
    ]


__all__ = [name for name in globals() if not name.startswith("__")]
