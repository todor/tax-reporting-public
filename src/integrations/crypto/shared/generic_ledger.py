from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .crypto_ir_models import GenericCryptoAnalyzerError, IrAssetHolding, ZERO

DECIMAL_EIGHT = Decimal("0.00000001")


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


class GenericAverageCostLedger:
    """Signed average-cost holdings ledger keyed by asset symbol."""

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
            raise GenericCryptoAnalyzerError(
                f"{context}: invalid signed position for {asset}; "
                f"quantity={position.quantity} total_cost_eur={position.total_cost_eur}"
            )
        if position.quantity < ZERO and position.total_cost_eur > DECIMAL_EIGHT:
            raise GenericCryptoAnalyzerError(
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
        context: str,
    ) -> TradeResult:
        normalized = asset.strip().upper()
        if normalized == "":
            raise GenericCryptoAnalyzerError(f"{context}: missing asset")
        if quantity <= ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: quantity must be positive ({normalized})")
        if execution_value_eur < ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: execution value must not be negative ({normalized})")

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

        self._normalize_position(asset=normalized, position=position, context=context)
        return result

    def sell(
        self,
        asset: str,
        *,
        quantity: Decimal,
        execution_value_eur: Decimal,
        context: str,
    ) -> TradeResult:
        normalized = asset.strip().upper()
        if normalized == "":
            raise GenericCryptoAnalyzerError(f"{context}: missing asset")
        if quantity <= ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: quantity must be positive ({normalized})")
        if execution_value_eur < ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: execution value must not be negative ({normalized})")

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

        self._normalize_position(asset=normalized, position=position, context=context)
        return result

    def decrease_without_realization(self, asset: str, *, quantity: Decimal, context: str) -> Decimal:
        normalized = asset.strip().upper()
        if normalized == "":
            raise GenericCryptoAnalyzerError(f"{context}: missing asset")
        if quantity <= ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: quantity must be positive ({normalized})")

        position = self._positions.get(normalized)
        if position is None or position.quantity <= ZERO:
            raise GenericCryptoAnalyzerError(
                f"{context}: insufficient holdings; asset={normalized} requested_qty={quantity}"
            )

        available = position.quantity
        if quantity > available + DECIMAL_EIGHT:
            raise GenericCryptoAnalyzerError(
                f"{context}: insufficient holdings; asset={normalized} "
                f"requested_qty={quantity} available_qty={available}"
            )

        quantity_to_remove = quantity if quantity <= available else available
        average_price_eur = position.total_cost_eur / available
        removed_cost_eur = quantity_to_remove * average_price_eur

        position.quantity -= quantity_to_remove
        position.total_cost_eur -= removed_cost_eur

        self._normalize_position(asset=normalized, position=position, context=context)
        return removed_cost_eur

    def increase_without_realization(
        self,
        asset: str,
        *,
        quantity: Decimal,
        execution_value_eur: Decimal,
        context: str,
    ) -> None:
        normalized = asset.strip().upper()
        if normalized == "":
            raise GenericCryptoAnalyzerError(f"{context}: missing asset")
        if quantity <= ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: quantity must be positive ({normalized})")
        if execution_value_eur < ZERO:
            raise GenericCryptoAnalyzerError(f"{context}: execution value must not be negative ({normalized})")

        position = self._get_or_create_position(normalized)
        remaining_quantity = quantity

        if position.quantity < ZERO:
            short_quantity = -position.quantity
            close_quantity = min(short_quantity, quantity)
            _, opening_value_eur = self._split_execution_value(
                total_quantity=quantity,
                total_value_eur=execution_value_eur,
                closing_quantity=close_quantity,
            )

            if close_quantity > ZERO:
                short_average_price_eur = abs(position.total_cost_eur) / short_quantity
                close_sale_price_eur = close_quantity * short_average_price_eur
                position.quantity += close_quantity
                position.total_cost_eur += close_sale_price_eur
                remaining_quantity -= close_quantity

            if remaining_quantity > ZERO:
                position.quantity += remaining_quantity
                position.total_cost_eur += opening_value_eur
        else:
            position.quantity += quantity
            position.total_cost_eur += execution_value_eur

        self._normalize_position(asset=normalized, position=position, context=context)

    def seed(self, asset: str, *, quantity: Decimal, total_cost_eur: Decimal, context: str) -> None:
        normalized = asset.strip().upper()
        if normalized == "":
            raise GenericCryptoAnalyzerError(f"{context}: missing asset")
        if quantity == ZERO:
            if total_cost_eur != ZERO:
                raise GenericCryptoAnalyzerError(
                    f"{context}: total cost must be zero when quantity is zero for {normalized}"
                )
            return
        if quantity > ZERO and total_cost_eur < ZERO:
            raise GenericCryptoAnalyzerError(
                f"{context}: total cost must not be negative for long position in {normalized}"
            )
        if quantity < ZERO and total_cost_eur > ZERO:
            raise GenericCryptoAnalyzerError(
                f"{context}: total cost must not be positive for short position in {normalized}"
            )

        position = self._get_or_create_position(normalized)
        position.quantity += quantity
        position.total_cost_eur += total_cost_eur
        self._normalize_position(asset=normalized, position=position, context=context)

    def quantity(self, asset: str) -> Decimal:
        normalized = asset.strip().upper()
        if normalized == "":
            return ZERO
        position = self._positions.get(normalized)
        if position is None:
            return ZERO
        return position.quantity

    def position(self, asset: str) -> IrAssetHolding | None:
        normalized = asset.strip().upper()
        position = self._positions.get(normalized)
        if position is None or abs(position.quantity) <= DECIMAL_EIGHT:
            return None
        return IrAssetHolding(
            asset=normalized,
            quantity=position.quantity,
            total_cost_eur=position.total_cost_eur,
        )

    def snapshot(self) -> dict[str, IrAssetHolding]:
        holdings: dict[str, IrAssetHolding] = {}
        for asset in sorted(self._positions):
            position = self._positions[asset]
            if abs(position.quantity) <= DECIMAL_EIGHT:
                continue
            holdings[asset] = IrAssetHolding(
                asset=asset,
                quantity=position.quantity,
                total_cost_eur=position.total_cost_eur,
            )
        return holdings


__all__ = [name for name in globals() if not name.startswith("__")]
