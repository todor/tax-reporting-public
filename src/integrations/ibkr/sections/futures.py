from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from integrations.shared.contracts import UserFacingTaxError

from ..appendices.declaration_text import _sum_bucket
from ..constants import (
    ADDED_FUTURES_MTM_COLUMNS,
    APPENDIX_5,
    DECIMAL_EIGHT,
    DECIMAL_TWO,
    FUTURES_ASSET_CATEGORY,
    FxRateProvider,
    ZERO,
)
from ..models import AnalysisSummary, _ActiveHeader
from ..shared import _fmt, _optional_index, _parse_decimal, _to_eur

MTM_SECTION = "Mark-to-Market Performance Summary"
MTM_REQUIRED_COLUMNS = [
    "Asset Category",
    "Symbol",
    "Mark-to-Market P/L Position",
    "Mark-to-Market P/L Transaction",
    "Mark-to-Market P/L Commissions",
    "Mark-to-Market P/L Other",
    "Mark-to-Market P/L Total",
]


@dataclass(frozen=True, slots=True)
class _FuturesMtmIndexes:
    asset: int
    symbol: int
    position: int
    transaction: int
    commissions: int
    other: int
    total: int
    currency: int | None


@dataclass(slots=True)
class FuturesMtmSectionResult:
    row_extras: dict[int, dict[str, str]]
    row_base_len: dict[int, int]
    row_added_columns: dict[int, list[str]]


def _missing_required_columns(active_header: _ActiveHeader) -> list[str]:
    return [column for column in MTM_REQUIRED_COLUMNS if _optional_index(active_header.headers, column) is None]


def _required_index(active_header: _ActiveHeader, column: str) -> int:
    index = _optional_index(active_header.headers, column)
    if index is None:
        raise AssertionError(f"missing required column after validation: {column}")
    return index


def _futures_mtm_indexes(active_header: _ActiveHeader) -> _FuturesMtmIndexes:
    return _FuturesMtmIndexes(
        asset=_required_index(active_header, "Asset Category"),
        symbol=_required_index(active_header, "Symbol"),
        position=_required_index(active_header, "Mark-to-Market P/L Position"),
        transaction=_required_index(active_header, "Mark-to-Market P/L Transaction"),
        commissions=_required_index(active_header, "Mark-to-Market P/L Commissions"),
        other=_required_index(active_header, "Mark-to-Market P/L Other"),
        total=_required_index(active_header, "Mark-to-Market P/L Total"),
        currency=_optional_index(active_header.headers, "Currency"),
    )


def _raise_missing_mtm_rows() -> None:
    raise UserFacingTaxError(
        code="IBKR_FUTURES_MISSING_MTM_ROWS",
        technical_message_en=(
            "IBKR Futures Trades rows were detected, but no Futures rows were found in "
            "Mark-to-Market Performance Summary."
        ),
    )


def _raise_missing_mtm_columns(missing_columns: list[str]) -> None:
    raise UserFacingTaxError(
        code="IBKR_FUTURES_MISSING_MTM_COLUMNS",
        params={"columns": missing_columns},
        technical_message_en=(
            "IBKR Futures Mark-to-Market Performance Summary is missing required columns: "
            f"{missing_columns}"
        ),
    )


def process_futures_mtm_section(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    summary: AnalysisSummary,
    fx_provider: FxRateProvider,
    tax_year: int,
) -> FuturesMtmSectionResult:
    futures_mtm_seen = False
    mtm_header_seen = False
    row_extras: dict[int, dict[str, str]] = {}
    row_base_len: dict[int, int] = {}
    row_added_columns: dict[int, list[str]] = {}
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != MTM_SECTION:
            continue
        if row[1] == "Header":
            active_header = active_headers.get(row_idx)
            headers = active_header.headers if active_header is not None else row[2:]
            row_base_len[row_idx] = 2 + len(headers)
            row_added_columns[row_idx] = [col for col in ADDED_FUTURES_MTM_COLUMNS if col not in headers]
            continue
        active_header_for_row = active_headers.get(row_idx)
        if active_header_for_row is not None:
            row_base_len[row_idx] = 2 + len(active_header_for_row.headers)
            row_added_columns[row_idx] = [col for col in ADDED_FUTURES_MTM_COLUMNS if col not in active_header_for_row.headers]
        if row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        mtm_header_seen = True
        missing_columns = _missing_required_columns(active_header)
        asset_idx = _optional_index(active_header.headers, "Asset Category")
        base_len = 2 + len(active_header.headers)
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]

        if asset_idx is None:
            if summary.futures_trade_rows > 0:
                _raise_missing_mtm_columns(missing_columns or ["Asset Category"])
            continue

        asset_category = data[asset_idx].strip()
        if asset_category != FUTURES_ASSET_CATEGORY:
            continue

        futures_mtm_seen = True
        if missing_columns:
            _raise_missing_mtm_columns(missing_columns)

        indexes = _futures_mtm_indexes(active_header)
        symbol = data[indexes.symbol].strip()
        currency = "EUR"
        if indexes.currency is not None:
            currency = data[indexes.currency].strip().upper() or "EUR"
        mtm_date = date(tax_year, 12, 31)
        position_eur, _ = _to_eur(
            _parse_decimal(data[indexes.position], row_number=row_number, field_name="Mark-to-Market P/L Position"),
            currency,
            mtm_date,
            fx_provider,
            row_number=row_number,
        )
        transaction_eur, _ = _to_eur(
            _parse_decimal(data[indexes.transaction], row_number=row_number, field_name="Mark-to-Market P/L Transaction"),
            currency,
            mtm_date,
            fx_provider,
            row_number=row_number,
        )
        commissions_eur, _ = _to_eur(
            _parse_decimal(data[indexes.commissions], row_number=row_number, field_name="Mark-to-Market P/L Commissions"),
            currency,
            mtm_date,
            fx_provider,
            row_number=row_number,
        )
        other_eur, _ = _to_eur(
            _parse_decimal(data[indexes.other], row_number=row_number, field_name="Mark-to-Market P/L Other"),
            currency,
            mtm_date,
            fx_provider,
            row_number=row_number,
        )
        total_eur, _ = _to_eur(
            _parse_decimal(data[indexes.total], row_number=row_number, field_name="Mark-to-Market P/L Total"),
            currency,
            mtm_date,
            fx_provider,
            row_number=row_number,
        )

        calculated_eur = position_eur + transaction_eur + commissions_eur + other_eur
        diff = calculated_eur - total_eur
        if abs(diff) > DECIMAL_TWO:
            summary.futures_mtm_arithmetic_mismatches.append(
                {
                    "row": str(row_number),
                    "symbol": symbol,
                    "position_eur": str(position_eur),
                    "transaction_eur": str(transaction_eur),
                    "commissions_eur": str(commissions_eur),
                    "other_eur": str(other_eur),
                    "total_eur": str(total_eur),
                    "difference_eur": str(diff),
                }
            )
        if other_eur != ZERO:
            summary.futures_mtm_other_rows += 1
            summary.futures_mtm_other_eur += other_eur

        summary.futures_mtm_rows += 1
        summary.futures_mtm_total_eur += total_eur
        if total_eur >= ZERO:
            summary.futures_mtm_positive_eur += total_eur
            _sum_bucket(summary.appendix_5, total_eur, ZERO, total_eur, count_row=False)
        else:
            negative_abs = -total_eur
            summary.futures_mtm_negative_eur += negative_abs
            _sum_bucket(summary.appendix_5, ZERO, negative_abs, total_eur, count_row=False)
        row_extras[row_idx] = {
            "Amount (EUR)": _fmt(total_eur, quant=DECIMAL_EIGHT),
            "Appendix Target": APPENDIX_5,
            "Tax Treatment Reason": "Futures MTM total -> Appendix 5",
            "Tax Year Scope": "IN_TAX_YEAR",
        }

    if summary.futures_trade_rows > 0 and (not mtm_header_seen or not futures_mtm_seen):
        _raise_missing_mtm_rows()
    return FuturesMtmSectionResult(
        row_extras=row_extras,
        row_base_len=row_base_len,
        row_added_columns=row_added_columns,
    )
