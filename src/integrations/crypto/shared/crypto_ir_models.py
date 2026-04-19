from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Generic, TypeVar

ZERO = Decimal("0")

IR_TRANSACTION_TYPES = {
    "Deposit",
    "Withdraw",
    "Buy",
    "Sell",
    "Earn",
}

IR_ASSET_TYPES = {
    "fiat",
    "crypto",
}

SEND_REVIEW_STATUSES = {
    "TAXABLE",
    "NON-TAXABLE",
}

RECEIVE_REVIEW_STATUSES = {
    "CARRY_OVER_BASIS",
    "RESET_BASIS_FROM_PRIOR_TAX_EVENT",
}


class CryptoIrValidationError(Exception):
    """Raised when IR rows are invalid."""


class GenericCryptoAnalyzerError(Exception):
    """Raised by the generic crypto analyzer."""


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
class CryptoIrRow:
    timestamp: datetime
    operation_id: str
    transaction_type: str
    asset: str
    asset_type: str
    quantity: Decimal
    proceeds_eur: Decimal | None
    fee_eur: Decimal | None
    cost_basis_eur: Decimal | None
    review_status: str | None
    source_exchange: str | None = None
    source_row_number: int | None = None
    source_transaction_type: str | None = None
    operation_leg: str | None = None
    subtotal_eur: Decimal | None = None
    total_eur: Decimal | None = None
    sort_index: int = 0


def validate_ir_row(row: CryptoIrRow) -> None:
    if row.transaction_type not in IR_TRANSACTION_TYPES:
        raise CryptoIrValidationError(
            f"invalid IR transaction type: {row.transaction_type!r} (operation_id={row.operation_id})"
        )
    if row.asset_type not in IR_ASSET_TYPES:
        raise CryptoIrValidationError(
            f"invalid IR asset type: {row.asset_type!r} (operation_id={row.operation_id})"
        )
    if row.asset.strip() == "":
        raise CryptoIrValidationError(f"missing IR asset (operation_id={row.operation_id})")

    if row.transaction_type in {"Buy", "Deposit", "Earn"} and row.quantity <= ZERO:
        raise CryptoIrValidationError(
            f"IR quantity must be positive for {row.transaction_type} (operation_id={row.operation_id})"
        )
    if row.transaction_type in {"Sell", "Withdraw"} and row.quantity >= ZERO:
        raise CryptoIrValidationError(
            f"IR quantity must be negative for {row.transaction_type} (operation_id={row.operation_id})"
        )

    if row.transaction_type in {"Buy", "Sell"} and row.proceeds_eur is None:
        raise CryptoIrValidationError(
            f"IR proceeds are required for {row.transaction_type} (operation_id={row.operation_id})"
        )

    if row.cost_basis_eur is not None and row.cost_basis_eur < ZERO:
        raise CryptoIrValidationError(
            f"IR cost basis must not be negative (operation_id={row.operation_id})"
        )


@dataclass(slots=True)
class IrBucketTotals:
    sale_price_eur: Decimal = ZERO
    purchase_price_eur: Decimal = ZERO
    wins_eur: Decimal = ZERO
    losses_eur: Decimal = ZERO
    rows: int = 0

    @property
    def net_result_eur(self) -> Decimal:
        return self.wins_eur - self.losses_eur


@dataclass(slots=True)
class IrAssetHolding:
    asset: str
    quantity: Decimal
    total_cost_eur: Decimal

    @property
    def average_price_eur(self) -> Decimal:
        if self.quantity == ZERO:
            return ZERO
        return abs(self.total_cost_eur) / abs(self.quantity)


@dataclass(slots=True)
class IrEnrichedRow:
    ir_row: CryptoIrRow
    purchase_price_eur: Decimal | None = None
    sale_price_eur: Decimal | None = None
    net_profit_eur: Decimal | None = None
    profit_win_eur: Decimal | None = None
    profit_loss_eur: Decimal | None = None
    position_quantity_after: Decimal | None = None
    total_cost_after_eur: Decimal | None = None
    average_price_after_eur: Decimal | None = None


def _bg_zapis_plural(count: int) -> str:
    return "запис" if count == 1 else "записа"


@dataclass(slots=True)
class IrAnalysisSummary:
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
    appendix_5: IrBucketTotals = field(default_factory=IrBucketTotals)
    holdings_by_asset: dict[str, IrAssetHolding] = field(default_factory=dict)

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
class IrAnalysisResult:
    summary: IrAnalysisSummary
    enriched_rows: list[IrEnrichedRow]
    year_end_holdings_by_asset: dict[str, tuple[Decimal, Decimal]]


@dataclass(slots=True)
class IrAnalysisRunResult:
    input_csv_path: Path
    output_csv_path: Path
    declaration_txt_path: Path
    year_end_state_json_path: Path
    summary: IrAnalysisSummary


__all__ = [name for name in globals() if not name.startswith("__")]
