from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from ..constants import ADDED_CORPORATE_ACTIONS_COLUMNS, ZERO
from ..models import _ActiveHeader
from ..shared import _optional_index, _parse_reconciliation_quantity

_MERGED_ACQUISITION_TOKEN = "Merged(Acquisition) WITH"


@dataclass(frozen=True, slots=True)
class CorporateActionInstrument:
    symbol: str
    name: str
    isin: str


@dataclass(slots=True)
class CorporateActionsSectionResult:
    row_extras: dict[int, dict[str, str]]
    row_base_len: dict[int, int]
    row_added_columns: dict[int, list[str]]
    recognized_quantity_delta_by_key: dict[tuple[str, str], Decimal]
    recognized_quantity_delta_by_isin: dict[str, Decimal]
    total_data_rows: int = 0
    ignored_rows: int = 0
    recognized_rows: int = 0
    unsupported_rows: int = 0


@dataclass(frozen=True, slots=True)
class _CorporateActionFieldIndexes:
    asset: int | None
    description: int | None
    quantity: int | None


def parse_merged_acquisition_final_tuple(description: str) -> CorporateActionInstrument | None:
    match = re.search(r"\(([^()]*)\)\s*$", description.strip())
    if match is None:
        return None
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) != 3:
        return None
    symbol, name, isin = parts
    isin = isin.upper()
    if not symbol or not name or not _is_isin(isin):
        return None
    return CorporateActionInstrument(symbol=symbol, name=name, isin=isin)


def process_corporate_actions_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
) -> CorporateActionsSectionResult:
    row_extras: dict[int, dict[str, str]] = {}
    row_base_len: dict[int, int] = {}
    row_added_columns: dict[int, list[str]] = {}
    recognized_quantity_delta_by_key: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    recognized_quantity_delta_by_isin: dict[str, Decimal] = defaultdict(lambda: ZERO)
    result = CorporateActionsSectionResult(
        row_extras=row_extras,
        row_base_len=row_base_len,
        row_added_columns=row_added_columns,
        recognized_quantity_delta_by_key=recognized_quantity_delta_by_key,
        recognized_quantity_delta_by_isin=recognized_quantity_delta_by_isin,
    )

    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Corporate Actions":
            continue
        if row[1] == "Header":
            header_columns = [value for value in row[2:] if value.strip()]
            row_base_len[row_idx] = 2 + len(header_columns)
            row_added_columns[row_idx] = [
                column for column in ADDED_CORPORATE_ACTIONS_COLUMNS if column not in header_columns
            ]
            continue
        if row[1] != "Data":
            continue

        result.total_data_rows += 1
        active_header = active_headers.get(row_idx)
        if active_header is None:
            row_base_len[row_idx] = len(row)
            _set_extras(
                row_extras,
                row_idx,
                status="NOT_SUPPORTED",
                action="Corporate action not handled automatically",
                reason=(
                    "Corporate action row has no active header, so the tool cannot classify it for "
                    "automatic quantity, cost-basis, income, gain/loss, withholding-tax, or other tax treatment."
                ),
            )
            result.unsupported_rows += 1
            continue

        base_len = 2 + len(active_header.headers)
        row_base_len[row_idx] = base_len
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]
        indexes = _corporate_action_indexes(active_header)
        asset_category = data[indexes.asset].strip() if indexes.asset is not None else ""
        description = data[indexes.description].strip() if indexes.description is not None else ""

        if _is_non_actionable_row(asset_category=asset_category, description=description):
            _mark_ignored(row_extras, row_idx)
            result.ignored_rows += 1
            continue

        if _MERGED_ACQUISITION_TOKEN in description:
            parsed = parse_merged_acquisition_final_tuple(description)
            quantity = (
                _parse_reconciliation_quantity(data[indexes.quantity])
                if indexes.quantity is not None
                else None
            )
            if parsed is not None and quantity is not None:
                asset_key = asset_category or "Stocks"
                recognized_quantity_delta_by_key[(asset_key, parsed.isin)] += quantity
                recognized_quantity_delta_by_isin[parsed.isin] += quantity
                _set_extras(
                    row_extras,
                    row_idx,
                    status="RECOGNIZED",
                    action="Apply recognized non-taxable merger",
                    reason=(
                        "Recognized IBKR Merged(Acquisition) WITH corporate action. The tool treats this "
                        "supported merger pattern as a non-taxable corporate action, applies the removed/received "
                        "quantities to the parsed ISINs, and does not create taxable income or realized gain/loss "
                        "from the merger rows."
                    ),
                )
                result.recognized_rows += 1
                continue

        _set_extras(
            row_extras,
            row_idx,
            status="NOT_SUPPORTED",
            action="Corporate action not handled automatically",
            reason=(
                "Corporate action pattern is not currently supported for automatic classification, quantity, "
                "cost-basis, income, gain/loss, withholding-tax, or other tax treatment."
            ),
        )
        result.unsupported_rows += 1

    return result


def _corporate_action_indexes(active_header: _ActiveHeader) -> _CorporateActionFieldIndexes:
    return _CorporateActionFieldIndexes(
        asset=_optional_index(active_header.headers, "Asset Category"),
        description=_optional_index(active_header.headers, "Description"),
        quantity=_optional_index(active_header.headers, "Quantity", "Qty"),
    )


def _is_non_actionable_row(*, asset_category: str, description: str) -> bool:
    return (
        asset_category in {"", "Total", "Total in EUR"}
        or description == ""
        or description.startswith("Basis:")
    )


def _mark_ignored(row_extras: dict[int, dict[str, str]], row_idx: int) -> None:
    _set_extras(
        row_extras,
        row_idx,
        status="IGNORE",
        action="Ignore corporate action non-actionable row",
        reason=(
            "Header/Total/Basis/empty-description row does not represent a corporate action that should be "
            "handled for tax-reporting output."
        ),
    )


def _set_extras(
    row_extras: dict[int, dict[str, str]],
    row_idx: int,
    *,
    status: str,
    action: str,
    reason: str,
) -> None:
    row_extras[row_idx] = {
        "Tax Status": status,
        "Tax Action": action,
        "Tax Reason": reason,
    }


def _is_isin(value: str) -> bool:
    return re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value) is not None
