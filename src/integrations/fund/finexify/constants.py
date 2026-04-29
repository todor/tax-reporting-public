from __future__ import annotations


from config import OUTPUT_DIR

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "finexify"
APPENDIX_5_DECLARATION_CODE = "5082"

REQUIRED_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "type": ("Type",),
    "cryptocurrency": ("Cryptocurrency",),
    "amount": ("Amount",),
    "date": ("Date",),
    "source": ("Source",),
}

SUPPORTED_TYPES = {
    "DEPOSIT",
    "BALANCE",
    "WITHDRAW",
}

__all__ = [name for name in globals() if not name.startswith("__")]
