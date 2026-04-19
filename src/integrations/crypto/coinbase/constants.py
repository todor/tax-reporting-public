from __future__ import annotations

from pathlib import Path

from config import OUTPUT_DIR

from decimal import Decimal

DECIMAL_TWO = Decimal("0.01")

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "coinbase"

REQUIRED_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "timestamp": ("Timestamp",),
    "transaction_type": ("Transaction Type",),
    "asset": ("Asset",),
    "quantity_transacted": ("Quantity Transacted",),
    "price_currency": ("Price Currency",),
    "subtotal": ("Subtotal",),
    "total": ("Total", "Total (inclusive of fees and/or spread)"),
    "notes": ("Notes",),
}

OPTIONAL_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "fees": ("Fees and/or Spread", "Fees"),
    "review_status": ("Review Status",),
    "purchase_price": ("Purchase Price",),
}

SUPPORTED_TRANSACTION_TYPES = {
    "Buy",
    "Sell",
    "Deposit",
    "Withdraw",
    "Convert",
    "Send",
    "Receive",
}

REVIEW_STATUS_NON_TAXABLE = "NON-TAXABLE"

__all__ = [name for name in globals() if not name.startswith("__")]
