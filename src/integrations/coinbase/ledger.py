from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .constants import DECIMAL_EIGHT, ZERO
from .models import AssetHolding, LedgerError


@dataclass(slots=True)
class _Position:
    quantity: Decimal = ZERO
    total_cost_eur: Decimal = ZERO


class AverageCostLedger:
    """Average-cost holdings ledger keyed by asset symbol."""

    def __init__(self) -> None:
        self._positions: dict[str, _Position] = {}

    def add(self, asset: str, *, quantity: Decimal, total_cost_eur: Decimal, row_number: int) -> None:
        normalized = asset.strip().upper()
        if normalized == "":
            raise LedgerError(f"row {row_number}: missing asset")
        if quantity <= ZERO:
            raise LedgerError(f"row {row_number}: quantity must be positive for {normalized}")
        if total_cost_eur < ZERO:
            raise LedgerError(f"row {row_number}: total cost must not be negative for {normalized}")

        position = self._positions.get(normalized)
        if position is None:
            position = _Position()
            self._positions[normalized] = position

        position.quantity += quantity
        position.total_cost_eur += total_cost_eur

    def remove(self, asset: str, *, quantity: Decimal, row_number: int, reason: str) -> Decimal:
        normalized = asset.strip().upper()
        if normalized == "":
            raise LedgerError(f"row {row_number}: missing asset for {reason}")
        if quantity <= ZERO:
            raise LedgerError(f"row {row_number}: quantity must be positive for {reason} ({normalized})")

        position = self._positions.get(normalized)
        if position is None or position.quantity <= ZERO:
            raise LedgerError(
                f"row {row_number}: insufficient holdings for {reason}; asset={normalized} requested_qty={quantity}"
            )

        available = position.quantity
        if quantity > available + DECIMAL_EIGHT:
            raise LedgerError(
                f"row {row_number}: insufficient holdings for {reason}; "
                f"asset={normalized} requested_qty={quantity} available_qty={available}"
            )

        qty_to_remove = quantity if quantity <= available else available
        average_price = position.total_cost_eur / available
        purchase_price = average_price * qty_to_remove

        position.quantity -= qty_to_remove
        position.total_cost_eur -= purchase_price

        if abs(position.quantity) <= DECIMAL_EIGHT:
            position.quantity = ZERO
            position.total_cost_eur = ZERO

        return purchase_price

    def snapshot(self) -> dict[str, AssetHolding]:
        holdings: dict[str, AssetHolding] = {}
        for asset in sorted(self._positions):
            position = self._positions[asset]
            if position.quantity <= ZERO and position.total_cost_eur <= ZERO:
                continue
            holdings[asset] = AssetHolding(
                asset=asset,
                quantity=position.quantity,
                total_cost_eur=position.total_cost_eur,
            )
        return holdings


__all__ = [name for name in globals() if not name.startswith("__")]
