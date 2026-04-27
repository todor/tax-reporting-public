from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .constants import OPTIONAL_COLUMN_CANDIDATES, REQUIRED_COLUMN_CANDIDATES
from .models import CsvRow, CsvSchema, CsvValidationError, KrakenAnalyzerError, LoadedKrakenCsv

_MONEY_CLEAN_RE = re.compile(r"[^0-9+\-.,]")
_REQUIRED_HEADER_TOKENS = set(sum((list(values) for values in REQUIRED_COLUMN_CANDIDATES.values()), []))


def parse_timestamp(raw: str, *, row_number: int) -> datetime:
    text = raw.strip()
    if text == "":
        raise KrakenAnalyzerError(f"row {row_number}: missing time")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise KrakenAnalyzerError(f"row {row_number}: invalid time format: {raw!r}") from exc
    return parsed.replace(tzinfo=timezone.utc)


def parse_decimal(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise KrakenAnalyzerError(f"row {row_number}: missing {field_name}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise KrakenAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def parse_prefixed_amount(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise KrakenAnalyzerError(f"row {row_number}: missing {field_name}")

    cleaned = _MONEY_CLEAN_RE.sub("", text)
    if cleaned in {"", "+", "-", ".", "+.", "-."}:
        raise KrakenAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}")

    normalized = cleaned.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise KrakenAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def normalize_review_status(raw: str) -> str:
    normalized = raw.strip().upper().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    if normalized == "NONTAXABLE":
        return "NON-TAXABLE"
    if normalized == "RESET-BASIS-FROM-PRIOR-TAX-EVENT":
        return "CARRY-OVER-BASIS"
    return normalized


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
        txid=resolved_required["txid"],
        refid=resolved_required["refid"],
        time=resolved_required["time"],
        type=resolved_required["type"],
        subtype=resolved_required["subtype"],
        aclass=resolved_required["aclass"],
        subclass=resolved_required["subclass"],
        asset=resolved_required["asset"],
        wallet=resolved_required["wallet"],
        amount=resolved_required["amount"],
        fee=resolved_required["fee"],
        balance=resolved_required["balance"],
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


def load_kraken_csv(path: str | Path) -> LoadedKrakenCsv:
    input_path = Path(path).expanduser().resolve()
    if not input_path.exists():
        raise KrakenAnalyzerError(f"input CSV does not exist: {input_path}")

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

    return LoadedKrakenCsv(
        input_path=input_path,
        preamble_rows_ignored=header_start,
        fieldnames=fieldnames,
        rows=rows,
        schema=schema,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
