from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from integrations.crypto.shared.crypto_ir_models import CryptoIrRow, CryptoIrValidationError, validate_ir_row


def _base_row() -> CryptoIrRow:
    return CryptoIrRow(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        operation_id="op-1",
        transaction_type="Buy",
        asset="BTC",
        asset_type="crypto",
        quantity=Decimal("1"),
        proceeds_eur=Decimal("100"),
        fee_eur=Decimal("1"),
        cost_basis_eur=None,
        review_status=None,
    )


def test_validate_ir_row_rejects_invalid_transaction_type() -> None:
    row = _base_row()
    row.transaction_type = "Convert"
    with pytest.raises(CryptoIrValidationError, match="invalid IR transaction type"):
        validate_ir_row(row)


def test_validate_ir_row_rejects_invalid_sell_quantity_sign() -> None:
    row = _base_row()
    row.transaction_type = "Sell"
    row.quantity = Decimal("1")
    with pytest.raises(CryptoIrValidationError, match="must be negative for Sell"):
        validate_ir_row(row)


def test_validate_ir_row_requires_proceeds_for_buy_and_sell() -> None:
    row = _base_row()
    row.proceeds_eur = None
    with pytest.raises(CryptoIrValidationError, match="required for Buy"):
        validate_ir_row(row)
