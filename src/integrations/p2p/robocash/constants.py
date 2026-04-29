from __future__ import annotations


from config import OUTPUT_DIR

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "p2p" / "robocash"
PLATFORM_NAME = "robocash"

SECONDARY_MARKET_MODE_HELP = "Secondary-market handling mode: appendix_6 (default); appendix_5 reserved/not supported yet"

__all__ = [name for name in globals() if not name.startswith("__")]
