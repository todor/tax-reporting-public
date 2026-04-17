from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from config import OUTPUT_DIR

DECIMAL_TWO = Decimal("0.01")
DECIMAL_EIGHT = Decimal("0.00000001")
ZERO = Decimal("0")

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "coinbase"

ADDED_OUTPUT_COLUMNS = [
    "Subtotal (EUR)",
    "Total (EUR)",
    "Purchase Price (EUR)",
    "Sale Price (EUR)",
    "Profit Win (EUR)",
    "Profit Loss (EUR)",
    "Net Profit (EUR)",
]

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

REVIEW_STATUS_TAXABLE = "TAXABLE"
REVIEW_STATUS_NON_TAXABLE = "NON-TAXABLE"

REVIEW_STATUS_CARRY_OVER_BASIS = "CARRY_OVER_BASIS"
REVIEW_STATUS_RESET_BASIS_FROM_PRIOR_TAX_EVENT = "RESET_BASIS_FROM_PRIOR_TAX_EVENT"

SEND_REVIEW_STATUSES = {
    REVIEW_STATUS_TAXABLE,
    REVIEW_STATUS_NON_TAXABLE,
}

RECEIVE_REVIEW_STATUSES = {
    REVIEW_STATUS_CARRY_OVER_BASIS,
    REVIEW_STATUS_RESET_BASIS_FROM_PRIOR_TAX_EVENT,
}

# Deposits/withdrawals are expected to be fiat-only in this analyzer version.
KNOWN_FIAT_ASSETS = {
    "EUR",
    "USD",
    "BGN",
    "GBP",
    "CHF",
    "CAD",
    "AUD",
    "NZD",
    "JPY",
    "CNY",
    "HKD",
    "SGD",
    "DKK",
    "NOK",
    "SEK",
    "PLN",
    "CZK",
    "RON",
    "HUF",
    "TRY",
}

KNOWN_FIAT_PRICE_CURRENCIES = KNOWN_FIAT_ASSETS

__all__ = [name for name in globals() if not name.startswith("__")]
