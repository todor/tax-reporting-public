from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .appendix6_models import P2PValidationError


def normalize_text_line(raw: str) -> str:
    """Normalize one extracted PDF line for deterministic regex parsing."""
    return " ".join(raw.replace("\u00a0", " ").strip().split())


def parse_decimal_text(value: str, *, field_name: str) -> Decimal:
    """Parse machine-generated money-like text into Decimal."""
    text = value.strip().replace(" ", "").replace("\u00a0", "")
    if text == "":
        raise P2PValidationError(f"missing numeric value for {field_name}")

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise P2PValidationError(f"invalid decimal for {field_name}: {value!r}") from exc


__all__ = [name for name in globals() if not name.startswith("__")]
