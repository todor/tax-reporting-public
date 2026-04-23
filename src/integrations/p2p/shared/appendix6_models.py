from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ZERO = Decimal("0")

SECONDARY_MARKET_MODE_APPENDIX_6 = "appendix_6"
SECONDARY_MARKET_MODE_APPENDIX_5 = "appendix_5"
SUPPORTED_SECONDARY_MARKET_MODES = {
    SECONDARY_MARKET_MODE_APPENDIX_6,
    SECONDARY_MARKET_MODE_APPENDIX_5,
}


class P2PAnalyzerError(Exception):
    """Base error for P2P analyzer failures."""


class P2PValidationError(P2PAnalyzerError):
    """Raised on invalid or inconsistent parsed data."""


class UnsupportedSecondaryMarketModeError(P2PAnalyzerError):
    """Raised when unsupported secondary-market handling mode is requested."""


@dataclass(slots=True)
class Appendix6Part1Row:
    payer_name: str
    payer_eik: str | None
    code: str
    amount: Decimal


@dataclass(slots=True)
class InformativeRow:
    label: str
    value: Decimal | str


@dataclass(slots=True)
class P2PAppendix6Result:
    platform: str
    tax_year: int | None
    part1_rows: list[Appendix6Part1Row]
    aggregate_code_603: Decimal
    aggregate_code_606: Decimal
    taxable_code_603: Decimal
    taxable_code_606: Decimal
    withheld_tax: Decimal
    informative_rows: list[InformativeRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    informational_messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class P2PAnalysisRunResult:
    input_path: Path
    output_txt_path: Path
    result: P2PAppendix6Result


__all__ = [name for name in globals() if not name.startswith("__")]
