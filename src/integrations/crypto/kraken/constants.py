from __future__ import annotations

from pathlib import Path

from config import OUTPUT_DIR

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "kraken"

REQUIRED_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "txid": ("txid",),
    "refid": ("refid",),
    "time": ("time",),
    "type": ("type",),
    "subtype": ("subtype",),
    "aclass": ("aclass",),
    "subclass": ("subclass",),
    "asset": ("asset",),
    "wallet": ("wallet",),
    "amount": ("amount",),
    "fee": ("fee",),
    "balance": ("balance",),
}

OPTIONAL_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "review_status": ("Review Status",),
    "cost_basis_eur": ("Cost Basis (EUR)",),
}

USD_LIKE_ASSETS = {"USD", "USDC", "USDT"}

__all__ = [name for name in globals() if not name.startswith("__")]
