from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .constants import ZERO


class CoinbaseAnalyzerError(Exception):
    """Base error for Coinbase analyzer failures."""


class CsvValidationError(CoinbaseAnalyzerError):
    """Raised when CSV structure is invalid or required fields are missing."""


class FxConversionError(CoinbaseAnalyzerError):
    """Raised when EUR conversion cannot be resolved."""


class LedgerError(CoinbaseAnalyzerError):
    """Raised when holdings/basis operations are invalid."""


def _bg_zapis_plural(count: int) -> str:
    return "запис" if count == 1 else "записа"


@dataclass(slots=True)
class BucketTotals:
    sale_price_eur: Decimal = ZERO
    purchase_price_eur: Decimal = ZERO
    wins_eur: Decimal = ZERO
    losses_eur: Decimal = ZERO
    rows: int = 0

    @property
    def net_result_eur(self) -> Decimal:
        return self.wins_eur - self.losses_eur


@dataclass(slots=True)
class AssetHolding:
    asset: str
    quantity: Decimal
    total_cost_eur: Decimal

    @property
    def average_price_eur(self) -> Decimal:
        if self.quantity == ZERO:
            return ZERO
        return abs(self.total_cost_eur) / abs(self.quantity)


@dataclass(slots=True)
class CsvRow:
    row_number: int
    raw: dict[str, str]


@dataclass(slots=True)
class CsvSchema:
    timestamp: str
    transaction_type: str
    asset: str
    quantity_transacted: str
    price_currency: str
    subtotal: str
    total: str
    notes: str
    review_status: str | None
    purchase_price: str | None


@dataclass(slots=True)
class LoadedCoinbaseCsv:
    input_path: Path
    preamble_rows_ignored: int
    fieldnames: list[str]
    rows: list[CsvRow]
    schema: CsvSchema


@dataclass(slots=True)
class AnalysisSummary:
    processed_rows: int = 0
    preamble_rows_ignored: int = 0
    manual_check_overrides_rows: int = 0
    ignored_fiat_deposit_withdraw_rows: int = 0
    unsupported_transaction_rows: int = 0
    taxable_send_rows: int = 0
    non_taxable_send_rows: int = 0
    invalid_send_review_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    unknown_transaction_types: set[str] = field(default_factory=set)
    unknown_send_review_statuses: set[str] = field(default_factory=set)
    appendix_5: BucketTotals = field(default_factory=BucketTotals)
    holdings_by_asset: dict[str, AssetHolding] = field(default_factory=dict)

    @property
    def manual_check_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.unsupported_transaction_rows > 0:
            values = ", ".join(sorted(self.unknown_transaction_types)) or "-"
            reasons.append(
                f"има {self.unsupported_transaction_rows} неподдържани/неясни записа, които са изключени ({values})"
            )
        if self.invalid_send_review_rows > 0:
            values = ", ".join(sorted(self.unknown_send_review_statuses)) or "-"
            reasons.append(
                f"има {self.invalid_send_review_rows} Send {_bg_zapis_plural(self.invalid_send_review_rows)} "
                f"без валиден Review Status ({values})"
            )
        return reasons

    @property
    def manual_check_required(self) -> bool:
        return bool(self.manual_check_reasons)


@dataclass(slots=True)
class AnalysisResult:
    input_csv_path: Path
    output_csv_path: Path
    declaration_txt_path: Path
    year_end_state_json_path: Path
    summary: AnalysisSummary


__all__ = [name for name in globals() if not name.startswith("__")]
