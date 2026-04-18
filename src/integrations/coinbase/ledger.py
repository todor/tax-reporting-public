from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .constants import DECIMAL_EIGHT, ZERO
from .models import AssetHolding, LedgerError


@dataclass(slots=True)
class _Position:
    quantity: Decimal = ZERO
    total_cost_eur: Decimal = ZERO


@dataclass(slots=True)
class TradeResult:
    closing_quantity: Decimal = ZERO
    opening_quantity: Decimal = ZERO
    closing_purchase_price_eur: Decimal = ZERO
    closing_sale_price_eur: Decimal = ZERO

    @property
    def realized_pnl_eur(self) -> Decimal:
        return self.closing_sale_price_eur - self.closing_purchase_price_eur

    @property
    def has_closing_leg(self) -> bool:
        return self.closing_quantity > ZERO


class AverageCostLedger:
    """Average-cost holdings ledger keyed by asset symbol."""

    def __init__(self) -> None:
        self._positions: dict[str, _Position] = {}

    def _get_or_create_position(self, asset: str) -> _Position:
        position = self._positions.get(asset)
        if position is None:
            position = _Position()
            self._positions[asset] = position
        return position

    def _normalize_position(self, *, asset: str, position: _Position, context: str) -> None:
        if abs(position.quantity) <= DECIMAL_EIGHT:
            position.quantity = ZERO
            position.total_cost_eur = ZERO
            return

        if abs(position.total_cost_eur) <= DECIMAL_EIGHT:
            position.total_cost_eur = ZERO

        if position.quantity > ZERO and position.total_cost_eur < -DECIMAL_EIGHT:
            raise LedgerError(
                f"{context}: invalid signed position for {asset}; "
                f"quantity={position.quantity} total_cost_eur={position.total_cost_eur}"
            )
        if position.quantity < ZERO and position.total_cost_eur > DECIMAL_EIGHT:
            raise LedgerError(
                f"{context}: invalid signed position for {asset}; "
                f"quantity={position.quantity} total_cost_eur={position.total_cost_eur}"
            )

    @staticmethod
    def _split_execution_value(
        *,
        total_quantity: Decimal,
        total_value_eur: Decimal,
        closing_quantity: Decimal,
    ) -> tuple[Decimal, Decimal]:
        if closing_quantity <= ZERO:
            return ZERO, total_value_eur
        if closing_quantity >= total_quantity:
            return total_value_eur, ZERO
        closing_value = total_value_eur * (closing_quantity / total_quantity)
        return closing_value, total_value_eur - closing_value

    def buy(
        self,
        asset: str,
        *,
        quantity: Decimal,
        execution_value_eur: Decimal,
        row_number: int,
        reason: str,
    ) -> TradeResult:
        normalized = asset.strip().upper()
        if normalized == "":
            raise LedgerError(f"row {row_number}: missing asset for {reason}")
        if quantity <= ZERO:
            raise LedgerError(f"row {row_number}: quantity must be positive for {reason} ({normalized})")
        if execution_value_eur < ZERO:
            raise LedgerError(
                f"row {row_number}: execution value must not be negative for {reason} ({normalized})"
            )

        result = TradeResult()
        position = self._get_or_create_position(normalized)
        remaining_quantity = quantity

        if position.quantity < ZERO:
            short_quantity = -position.quantity
            close_quantity = min(short_quantity, quantity)
            close_purchase_price_eur, open_purchase_price_eur = self._split_execution_value(
                total_quantity=quantity,
                total_value_eur=execution_value_eur,
                closing_quantity=close_quantity,
            )

            if close_quantity > ZERO:
                short_average_price_eur = abs(position.total_cost_eur) / short_quantity
                close_sale_price_eur = close_quantity * short_average_price_eur

                result.closing_quantity = close_quantity
                result.closing_purchase_price_eur = close_purchase_price_eur
                result.closing_sale_price_eur = close_sale_price_eur

                position.quantity += close_quantity
                position.total_cost_eur += close_sale_price_eur
                remaining_quantity -= close_quantity

            if remaining_quantity > ZERO:
                result.opening_quantity = remaining_quantity
                position.quantity += remaining_quantity
                position.total_cost_eur += open_purchase_price_eur
        else:
            result.opening_quantity = quantity
            position.quantity += quantity
            position.total_cost_eur += execution_value_eur

        self._normalize_position(
            asset=normalized,
            position=position,
            context=f"row {row_number}: {reason}",
        )
        return result

    def seed(self, asset: str, *, quantity: Decimal, total_cost_eur: Decimal) -> None:
        """Seed ledger with pre-existing holdings from prior period state."""
        normalized = asset.strip().upper()
        if normalized == "":
            raise LedgerError("opening state: missing asset")
        if quantity == ZERO:
            if total_cost_eur != ZERO:
                raise LedgerError(
                    f"opening state: total cost must be zero when quantity is zero for {normalized}"
                )
            return
        if quantity > ZERO and total_cost_eur < ZERO:
            raise LedgerError(
                f"opening state: total cost must not be negative for long position in {normalized}"
            )
        if quantity < ZERO and total_cost_eur > ZERO:
            raise LedgerError(
                f"opening state: total cost must not be positive for short position in {normalized}"
            )

        position = self._get_or_create_position(normalized)
        position.quantity += quantity
        position.total_cost_eur += total_cost_eur
        self._normalize_position(asset=normalized, position=position, context="opening state")

    def sell(
        self,
        asset: str,
        *,
        quantity: Decimal,
        execution_value_eur: Decimal,
        row_number: int,
        reason: str,
    ) -> TradeResult:
        normalized = asset.strip().upper()
        if normalized == "":
            raise LedgerError(f"row {row_number}: missing asset for {reason}")
        if quantity <= ZERO:
            raise LedgerError(f"row {row_number}: quantity must be positive for {reason} ({normalized})")
        if execution_value_eur < ZERO:
            raise LedgerError(
                f"row {row_number}: execution value must not be negative for {reason} ({normalized})"
            )

        result = TradeResult()
        position = self._get_or_create_position(normalized)
        remaining_quantity = quantity

        if position.quantity > ZERO:
            long_quantity = position.quantity
            close_quantity = min(long_quantity, quantity)
            close_sale_price_eur, open_sale_price_eur = self._split_execution_value(
                total_quantity=quantity,
                total_value_eur=execution_value_eur,
                closing_quantity=close_quantity,
            )

            if close_quantity > ZERO:
                long_average_price_eur = position.total_cost_eur / long_quantity
                close_purchase_price_eur = close_quantity * long_average_price_eur

                result.closing_quantity = close_quantity
                result.closing_purchase_price_eur = close_purchase_price_eur
                result.closing_sale_price_eur = close_sale_price_eur

                position.quantity -= close_quantity
                position.total_cost_eur -= close_purchase_price_eur
                remaining_quantity -= close_quantity

            if remaining_quantity > ZERO:
                result.opening_quantity = remaining_quantity
                position.quantity -= remaining_quantity
                position.total_cost_eur -= open_sale_price_eur
        else:
            result.opening_quantity = quantity
            position.quantity -= quantity
            position.total_cost_eur -= execution_value_eur

        self._normalize_position(
            asset=normalized,
            position=position,
            context=f"row {row_number}: {reason}",
        )
        return result

    def quantity(self, asset: str) -> Decimal:
        normalized = asset.strip().upper()
        if normalized == "":
            return ZERO
        position = self._positions.get(normalized)
        if position is None:
            return ZERO
        return position.quantity

    def snapshot(self) -> dict[str, AssetHolding]:
        holdings: dict[str, AssetHolding] = {}
        for asset in sorted(self._positions):
            position = self._positions[asset]
            if abs(position.quantity) <= DECIMAL_EIGHT:
                continue
            holdings[asset] = AssetHolding(
                asset=asset,
                quantity=position.quantity,
                total_cost_eur=position.total_cost_eur,
            )
        return holdings


__all__ = [name for name in globals() if not name.startswith("__")]
