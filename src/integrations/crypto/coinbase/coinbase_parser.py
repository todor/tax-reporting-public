from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .constants import (
    OPTIONAL_COLUMN_CANDIDATES,
    REQUIRED_COLUMN_CANDIDATES,
    REVIEW_STATUS_NON_TAXABLE,
)
from .models import CoinbaseAnalyzerError, CsvRow, CsvSchema, CsvValidationError, LoadedCoinbaseCsv

_MONEY_CLEAN_RE = re.compile(r"[^0-9+\-.,]")
_REQUIRED_HEADER_TOKENS = {
    "Timestamp",
    "Transaction Type",
    "Asset",
    "Quantity Transacted",
    "Price Currency",
}


@dataclass(slots=True)
class ConvertNote:
    qty_sold: Decimal
    asset_sold: str
    qty_bought: Decimal
    asset_bought: str


def parse_timestamp(raw: str, *, row_number: int) -> datetime:
    text = raw.strip()
    if text == "":
        raise CoinbaseAnalyzerError(f"row {row_number}: missing Timestamp")

    if text.endswith(" UTC"):
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise CoinbaseAnalyzerError(f"row {row_number}: invalid Timestamp format: {raw!r}") from exc

    candidate = text
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CoinbaseAnalyzerError(f"row {row_number}: invalid Timestamp format: {raw!r}") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_decimal(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise CoinbaseAnalyzerError(f"row {row_number}: missing {field_name}")

    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise CoinbaseAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def parse_prefixed_amount(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise CoinbaseAnalyzerError(f"row {row_number}: missing {field_name}")

    cleaned = _MONEY_CLEAN_RE.sub("", text)
    if cleaned in {"", "+", "-", ".", "+.", "-."}:
        raise CoinbaseAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}")

    normalized = cleaned.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise CoinbaseAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def normalize_review_status(raw: str) -> str:
    normalized = raw.strip().upper().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    if normalized == "NONTAXABLE":
        return REVIEW_STATUS_NON_TAXABLE
    if normalized == "RESET-BASIS-FROM-PRIOR-TAX-EVENT":
        return "CARRY-OVER-BASIS"
    return normalized


def parse_convert_note(raw: str, *, row_number: int) -> ConvertNote:
    text = raw.strip()
    match = re.fullmatch(
        r"Converted\s+([0-9]+(?:\.[0-9]+)?)\s+([A-Za-z0-9._-]+)\s+to\s+([0-9]+(?:\.[0-9]+)?)\s+([A-Za-z0-9._-]+)",
        text,
    )
    if match is None:
        raise CoinbaseAnalyzerError(
            f"row {row_number}: invalid Convert Notes format; expected "
            "'Converted <qty_sold> <asset_sold> to <qty_bought> <asset_bought>'"
        )

    try:
        qty_sold = Decimal(match.group(1))
        qty_bought = Decimal(match.group(3))
    except InvalidOperation as exc:
        raise CoinbaseAnalyzerError(f"row {row_number}: invalid Convert quantity in Notes: {raw!r}") from exc

    if qty_sold <= 0 or qty_bought <= 0:
        raise CoinbaseAnalyzerError(f"row {row_number}: Convert Notes quantities must be positive: {raw!r}")

    return ConvertNote(
        qty_sold=qty_sold,
        asset_sold=match.group(2).upper(),
        qty_bought=qty_bought,
        asset_bought=match.group(4).upper(),
    )


def _resolve_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {name.strip(): name for name in fieldnames}
    for candidate in candidates:
        resolved = normalized.get(candidate)
        if resolved is not None:
            return resolved
    return None


def _resolve_schema(fieldnames: list[str], *, input_path: Path) -> CsvSchema:
    resolved_required: dict[str, str] = {}
    missing: list[str] = []

    for key, candidates in REQUIRED_COLUMN_CANDIDATES.items():
        resolved = _resolve_column(fieldnames, candidates)
        if resolved is None:
            missing.append(" / ".join(candidates))
            continue
        resolved_required[key] = resolved

    if missing:
        raise CsvValidationError(f"{input_path}: missing required columns: {missing}")

    resolved_optional: dict[str, str | None] = {}
    for key, candidates in OPTIONAL_COLUMN_CANDIDATES.items():
        resolved_optional[key] = _resolve_column(fieldnames, candidates)

    return CsvSchema(
        timestamp=resolved_required["timestamp"],
        transaction_type=resolved_required["transaction_type"],
        asset=resolved_required["asset"],
        quantity_transacted=resolved_required["quantity_transacted"],
        price_currency=resolved_required["price_currency"],
        subtotal=resolved_required["subtotal"],
        total=resolved_required["total"],
        notes=resolved_required["notes"],
        fees=resolved_optional["fees"],
        review_status=resolved_optional["review_status"],
        cost_basis_eur=resolved_optional["cost_basis_eur"],
    )


def _find_header_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        cleaned_line = line.lstrip("\ufeff")
        parsed = next(csv.reader([cleaned_line]), [])
        normalized = {value.strip() for value in parsed if value is not None}
        if _REQUIRED_HEADER_TOKENS.issubset(normalized):
            return index
    raise CsvValidationError(
        "CSV header row was not found; expected header containing columns "
        f"{sorted(_REQUIRED_HEADER_TOKENS)}"
    )


def load_coinbase_csv(path: str | Path) -> LoadedCoinbaseCsv:
    input_path = Path(path).expanduser().resolve()
    if not input_path.exists():
        raise CoinbaseAnalyzerError(f"input CSV does not exist: {input_path}")

    lines = input_path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    if not lines:
        raise CsvValidationError(f"{input_path}: empty CSV input")

    header_start = _find_header_start(lines)
    csv_payload = "".join(lines[header_start:])

    reader = csv.DictReader(io.StringIO(csv_payload))
    if reader.fieldnames is None:
        raise CsvValidationError(f"{input_path}: missing CSV header")

    fieldnames = [name.strip() for name in reader.fieldnames]
    schema = _resolve_schema(fieldnames, input_path=input_path)

    rows: list[CsvRow] = []
    for row_number, raw in enumerate(reader, start=1):
        normalized_raw = {key.strip(): (value or "") for key, value in raw.items() if key is not None}
        rows.append(CsvRow(row_number=row_number, raw=normalized_raw))

    return LoadedCoinbaseCsv(
        input_path=input_path,
        preamble_rows_ignored=header_start,
        fieldnames=fieldnames,
        rows=rows,
        schema=schema,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
