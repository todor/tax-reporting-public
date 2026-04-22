from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Generic, TypeVar

ZERO = Decimal("0")

IR_TRANSACTION_TYPES = {
    "deposit",
    "profit",
    "withdraw",
}

IR_CURRENCY_TYPES = {
    "fiat",
    "crypto",
}


class FundIrValidationError(Exception):
    """Raised when fund IR rows are invalid."""


class GenericFundAnalyzerError(Exception):
    """Raised by the generic fund analyzer."""


@dataclass(slots=True)
class CsvRow:
    row_number: int
    raw: dict[str, str]


SchemaT = TypeVar("SchemaT")


@dataclass(slots=True)
class LoadedCsv(Generic[SchemaT]):
    input_path: Path
    preamble_rows_ignored: int
    fieldnames: list[str]
    rows: list[CsvRow]
    schema: SchemaT


@dataclass(slots=True)
class FundIrRow:
    timestamp: datetime
    operation_id: str
    transaction_type: str
    currency: str
    currency_type: str
    amount: Decimal
    source_exchange: str | None = None
    source_row_number: int | None = None
    source_transaction_type: str | None = None
    sort_index: int = 0


def validate_ir_row(row: FundIrRow) -> None:
    if row.transaction_type not in IR_TRANSACTION_TYPES:
        raise FundIrValidationError(
            f"invalid IR transaction type: {row.transaction_type!r} (operation_id={row.operation_id})"
        )
    if row.currency_type not in IR_CURRENCY_TYPES:
        raise FundIrValidationError(
            f"invalid IR currency type: {row.currency_type!r} (operation_id={row.operation_id})"
        )
    if row.currency.strip() == "":
        raise FundIrValidationError(f"missing IR currency (operation_id={row.operation_id})")

    if row.transaction_type in {"deposit", "withdraw"} and row.amount <= ZERO:
        raise FundIrValidationError(
            f"IR amount must be positive for {row.transaction_type} (operation_id={row.operation_id})"
        )


@dataclass(slots=True)
class FundBucketTotals:
    sale_price_eur: Decimal = ZERO
    purchase_price_eur: Decimal = ZERO
    wins_eur: Decimal = ZERO
    losses_eur: Decimal = ZERO
    rows: int = 0

    @property
    def net_result_eur(self) -> Decimal:
        return self.wins_eur - self.losses_eur


@dataclass(slots=True)
class FundCurrencyState:
    currency: str
    currency_type: str
    native_deposit_balance: Decimal = ZERO
    eur_deposit_balance: Decimal = ZERO
    native_profit_balance: Decimal = ZERO

    @property
    def native_total_balance(self) -> Decimal:
        return self.native_deposit_balance + self.native_profit_balance


@dataclass(slots=True)
class FundEnrichedRow:
    ir_row: FundIrRow
    amount_eur: Decimal | None = None
    balance_native: Decimal | None = None
    balance_eur: Decimal | None = None
    deposit_to_date_eur: Decimal | None = None
    purchase_price_eur: Decimal | None = None
    sale_price_eur: Decimal | None = None
    net_profit_eur: Decimal | None = None
    profit_win_eur: Decimal | None = None
    profit_loss_eur: Decimal | None = None


@dataclass(slots=True)
class FundAnalysisSummary:
    processed_rows: int = 0
    preamble_rows_ignored: int = 0
    ignored_rows: int = 0
    unsupported_transaction_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    unknown_transaction_types: set[str] = field(default_factory=set)
    appendix_5: FundBucketTotals = field(default_factory=FundBucketTotals)
    state_by_currency: dict[str, FundCurrencyState] = field(default_factory=dict)

    @property
    def manual_check_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.unsupported_transaction_rows > 0:
            values = ", ".join(sorted(self.unknown_transaction_types)) or "-"
            reasons.append(
                f"има {self.unsupported_transaction_rows} неподдържани/неясни записа, които са изключени ({values})"
            )
        return reasons

    @property
    def manual_check_required(self) -> bool:
        return bool(self.manual_check_reasons)


@dataclass(slots=True)
class FundAnalysisResult:
    summary: FundAnalysisSummary
    enriched_rows: list[FundEnrichedRow]
    year_end_state_by_currency: dict[str, FundCurrencyState]


@dataclass(slots=True)
class FundAnalysisRunResult:
    input_csv_path: Path
    output_csv_path: Path
    declaration_txt_path: Path
    year_end_state_json_path: Path
    summary: FundAnalysisSummary


__all__ = [name for name in globals() if not name.startswith("__")]
