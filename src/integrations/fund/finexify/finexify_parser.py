from __future__ import annotations

import csv
import io
from pathlib import Path

from integrations.fund.shared.fund_ir_models import CsvRow

from .constants import REQUIRED_COLUMN_CANDIDATES
from .models import CsvSchema, CsvValidationError, FinexifyAnalyzerError, LoadedFinexifyCsv

_REQUIRED_HEADER_TOKENS = {
    "Type",
    "Cryptocurrency",
    "Amount",
}


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

    return CsvSchema(
        tx_type=resolved_required["type"],
        cryptocurrency=resolved_required["cryptocurrency"],
        amount=resolved_required["amount"],
        date=resolved_required["date"],
        source=resolved_required["source"],
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


def load_finexify_csv(path: str | Path) -> LoadedFinexifyCsv:
    input_path = Path(path).expanduser().resolve()
    if not input_path.exists():
        raise FinexifyAnalyzerError(f"input CSV does not exist: {input_path}")

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

    return LoadedFinexifyCsv(
        input_path=input_path,
        preamble_rows_ignored=header_start,
        fieldnames=fieldnames,
        rows=rows,
        schema=schema,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
