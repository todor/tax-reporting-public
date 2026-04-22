from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from integrations.fund.shared.fund_ir_models import (
    FundAnalysisSummary,
    FundCurrencyState,
    FundIrRow,
    ZERO,
)

from .constants import SUPPORTED_TYPES
from .finexify_parser import load_finexify_csv
from .models import FinexifyAnalyzerError, LoadedFinexifyCsv

TOLERANCE = Decimal("0.000000000001")


@dataclass(slots=True)
class FinexifyIrMappingResult:
    loaded_csv: LoadedFinexifyCsv
    ir_rows: list[FundIrRow]


@dataclass(slots=True)
class _ParsedTxRow:
    row_number: int
    tx_type: str
    timestamp: datetime
    has_time_component: bool
    currency: str
    amount: Decimal
    source: str


@dataclass(slots=True)
class _NativeState:
    native_deposit_balance: Decimal = ZERO
    native_profit_balance: Decimal = ZERO

    @property
    def native_total_balance(self) -> Decimal:
        return self.native_deposit_balance + self.native_profit_balance



def parse_timestamp(raw: str, *, row_number: int) -> tuple[datetime, bool]:
    text = raw.strip()
    if text == "":
        raise FinexifyAnalyzerError(f"row {row_number}: missing Date")

    if len(text) == 10:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise FinexifyAnalyzerError(f"row {row_number}: invalid Date format: {raw!r}") from exc
        return parsed.replace(tzinfo=timezone.utc), False

    candidate = text
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise FinexifyAnalyzerError(f"row {row_number}: invalid Date format: {raw!r}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed, True


def parse_amount(raw: str, *, row_number: int) -> Decimal:
    text = raw.strip().replace(",", "")
    if text == "":
        raise FinexifyAnalyzerError(f"row {row_number}: missing Amount")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise FinexifyAnalyzerError(f"row {row_number}: invalid Amount: {raw!r}") from exc


def _operation_id(*, row_number: int) -> str:
    return f"finexify-row-{row_number}"


def _is_effectively_empty_row(*, tx_type: str, currency: str, amount: str, date: str) -> bool:
    return tx_type == "" and currency == "" and amount == "" and date == ""


def _warning_for_unsupported_type(*, row_number: int, tx_type: str) -> str:
    return (
        f"row {row_number}: unsupported Type={tx_type!r}; excluded from tax calculations "
        f"(accepted: {sorted(SUPPORTED_TYPES)})"
    )


def _compare_for_order(left: _ParsedTxRow, right: _ParsedTxRow) -> int:
    # If at least one side is date-only, compare only date components.
    if (not left.has_time_component) or (not right.has_time_component):
        left_date = left.timestamp.date()
        right_date = right.timestamp.date()
        if left_date < right_date:
            return -1
        if left_date > right_date:
            return 1
        return 0

    if left.timestamp < right.timestamp:
        return -1
    if left.timestamp > right.timestamp:
        return 1
    return 0


def _is_monotonic(rows: list[_ParsedTxRow], *, ascending: bool) -> bool:
    for previous, current in zip(rows, rows[1:]):
        cmp = _compare_for_order(previous, current)
        if ascending and cmp > 0:
            return False
        if not ascending and cmp < 0:
            return False
    return True


def _normalize_processing_order(rows: list[_ParsedTxRow]) -> list[_ParsedTxRow]:
    if len(rows) <= 1:
        return rows

    ascending = _is_monotonic(rows, ascending=True)
    descending = _is_monotonic(rows, ascending=False)

    if ascending:
        return rows
    if descending:
        return list(reversed(rows))

    # Fallback only when original file order is not monotonic.
    # Sort by date and keep original relative order for same-day rows.
    return sorted(rows, key=lambda item: (item.timestamp.date(),))


def _seed_native_state(
    opening_state_by_currency: dict[str, FundCurrencyState] | None,
) -> dict[str, _NativeState]:
    native_state: dict[str, _NativeState] = {}
    if not opening_state_by_currency:
        return native_state

    for currency, state in opening_state_by_currency.items():
        native_state[currency] = _NativeState(
            native_deposit_balance=state.native_deposit_balance,
            native_profit_balance=state.native_profit_balance,
        )
    return native_state


def _currency_state(native_state: dict[str, _NativeState], *, currency: str) -> _NativeState:
    existing = native_state.get(currency)
    if existing is None:
        existing = _NativeState()
        native_state[currency] = existing
    return existing


def _apply_withdraw_for_snapshot_state(
    *,
    state: _NativeState,
    withdrawal_native: Decimal,
    row_number: int,
    currency: str,
) -> None:
    if withdrawal_native <= ZERO:
        raise FinexifyAnalyzerError(f"row {row_number}: Withdraw amount must be positive")

    total_native = state.native_total_balance
    if total_native <= ZERO:
        raise FinexifyAnalyzerError(
            f"row {row_number}: Withdraw requires positive balance for {currency}; available={total_native}"
        )

    if withdrawal_native > total_native + TOLERANCE:
        raise FinexifyAnalyzerError(
            f"row {row_number}: Withdraw exceeds current balance for {currency}; "
            f"requested={withdrawal_native} available={total_native}"
        )

    if abs(withdrawal_native - total_native) <= TOLERANCE:
        withdrawal_native = total_native

    ratio = withdrawal_native / total_native
    state.native_deposit_balance -= ratio * state.native_deposit_balance
    state.native_profit_balance -= ratio * state.native_profit_balance

    if abs(state.native_deposit_balance) <= TOLERANCE:
        state.native_deposit_balance = ZERO
    if abs(state.native_profit_balance) <= TOLERANCE:
        state.native_profit_balance = ZERO


def _apply_profit_for_snapshot_state(
    *,
    state: _NativeState,
    profit_delta: Decimal,
    row_number: int,
    currency: str,
) -> None:
    state.native_profit_balance += profit_delta
    if state.native_total_balance < -TOLERANCE:
        raise FinexifyAnalyzerError(
            f"row {row_number}: Profit update makes total balance negative for {currency}; "
            f"total={state.native_total_balance}"
        )

    if abs(state.native_profit_balance) <= TOLERANCE:
        state.native_profit_balance = ZERO


def load_and_map_finexify_csv_to_ir(
    *,
    input_csv: str,
    summary: FundAnalysisSummary,
    opening_state_by_currency: dict[str, FundCurrencyState] | None = None,
) -> FinexifyIrMappingResult:
    loaded = load_finexify_csv(input_csv)
    schema = loaded.schema

    parsed_rows: list[_ParsedTxRow] = []
    for row in loaded.rows:
        raw = row.raw
        row_number = row.row_number

        tx_type_raw = raw.get(schema.tx_type, "").strip()
        tx_type = tx_type_raw.upper()
        currency_raw = raw.get(schema.cryptocurrency, "").strip()
        amount_raw = raw.get(schema.amount, "").strip()
        date_raw = raw.get(schema.date, "").strip()

        if _is_effectively_empty_row(
            tx_type=tx_type_raw,
            currency=currency_raw,
            amount=amount_raw,
            date=date_raw,
        ):
            summary.ignored_rows += 1
            continue

        if tx_type not in SUPPORTED_TYPES:
            summary.unsupported_transaction_rows += 1
            summary.unknown_transaction_types.add(tx_type or "EMPTY")
            summary.warnings.append(
                _warning_for_unsupported_type(
                    row_number=row_number,
                    tx_type=tx_type_raw,
                )
            )
            continue

        timestamp, has_time_component = parse_timestamp(date_raw, row_number=row_number)

        currency = currency_raw.upper()
        if currency == "":
            raise FinexifyAnalyzerError(f"row {row_number}: missing Cryptocurrency")

        amount = parse_amount(amount_raw, row_number=row_number)
        source = raw.get(schema.source, "").strip()

        parsed_rows.append(
            _ParsedTxRow(
                row_number=row_number,
                tx_type=tx_type,
                timestamp=timestamp,
                has_time_component=has_time_component,
                currency=currency,
                amount=amount,
                source=source,
            )
        )

    parsed_rows = _normalize_processing_order(parsed_rows)

    native_state = _seed_native_state(opening_state_by_currency)

    ir_rows: list[FundIrRow] = []
    for sort_index, row in enumerate(parsed_rows):
        state = _currency_state(native_state, currency=row.currency)

        if row.tx_type == "DEPOSIT":
            if row.source.strip().upper() == "INVESTMENT":
                if row.amount <= ZERO:
                    raise FinexifyAnalyzerError(f"row {row.row_number}: Deposit amount must be positive")
                state.native_deposit_balance += row.amount
                ir_rows.append(
                    FundIrRow(
                        timestamp=row.timestamp,
                        operation_id=_operation_id(row_number=row.row_number),
                        transaction_type="deposit",
                        currency=row.currency,
                        currency_type="crypto",
                        amount=row.amount,
                        source_exchange="finexify",
                        source_row_number=row.row_number,
                        source_transaction_type="Deposit",
                        sort_index=sort_index,
                    )
                )
            else:
                _apply_profit_for_snapshot_state(
                    state=state,
                    profit_delta=row.amount,
                    row_number=row.row_number,
                    currency=row.currency,
                )
                ir_rows.append(
                    FundIrRow(
                        timestamp=row.timestamp,
                        operation_id=_operation_id(row_number=row.row_number),
                        transaction_type="profit",
                        currency=row.currency,
                        currency_type="crypto",
                        amount=row.amount,
                        source_exchange="finexify",
                        source_row_number=row.row_number,
                        source_transaction_type="Deposit",
                        sort_index=sort_index,
                    )
                )
            continue

        if row.tx_type == "BALANCE":
            if row.amount < ZERO:
                raise FinexifyAnalyzerError(
                    f"row {row.row_number}: Balance amount must not be negative for {row.currency}; amount={row.amount}"
                )

            current_total = state.native_total_balance
            profit_delta = row.amount - current_total
            next_total = current_total + profit_delta
            if next_total < -TOLERANCE:
                raise FinexifyAnalyzerError(
                    f"row {row.row_number}: Balance row leads to negative resulting total for {row.currency}; "
                    f"result={next_total}"
                )

            _apply_profit_for_snapshot_state(
                state=state,
                profit_delta=profit_delta,
                row_number=row.row_number,
                currency=row.currency,
            )

            ir_rows.append(
                FundIrRow(
                    timestamp=row.timestamp,
                    operation_id=_operation_id(row_number=row.row_number),
                    transaction_type="profit",
                    currency=row.currency,
                    currency_type="crypto",
                    amount=profit_delta,
                    source_exchange="finexify",
                    source_row_number=row.row_number,
                    source_transaction_type="Balance",
                    sort_index=sort_index,
                )
            )
            continue

        if row.tx_type == "WITHDRAW":
            _apply_withdraw_for_snapshot_state(
                state=state,
                withdrawal_native=row.amount,
                row_number=row.row_number,
                currency=row.currency,
            )
            ir_rows.append(
                FundIrRow(
                    timestamp=row.timestamp,
                    operation_id=_operation_id(row_number=row.row_number),
                    transaction_type="withdraw",
                    currency=row.currency,
                    currency_type="crypto",
                    amount=row.amount,
                    source_exchange="finexify",
                    source_row_number=row.row_number,
                    source_transaction_type="Withdraw",
                    sort_index=sort_index,
                )
            )
            continue

        raise FinexifyAnalyzerError(f"row {row.row_number}: unsupported mapped type={row.tx_type!r}")

    return FinexifyIrMappingResult(loaded_csv=loaded, ir_rows=ir_rows)


__all__ = [name for name in globals() if not name.startswith("__")]
