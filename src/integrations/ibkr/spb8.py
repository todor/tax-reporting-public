from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from integrations.shared.spb8 import SPB8Row

from .constants import ZERO
from .models import CsvStructureError, InstrumentListing, _ActiveHeader
from .sections.instruments import _is_supported_asset, _resolve_instrument_for_trade_symbol
from .sections.open_positions import _open_positions_indexes, _trade_order_indexes
from .shared import _index_for, _optional_index, _parse_decimal_loose_or_zero, _parse_reconciliation_quantity


@dataclass(frozen=True, slots=True)
class IbkrSPB8Extraction:
    rows: list[SPB8Row]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class _TransferFieldIndexes:
    asset: int
    symbol: int
    direction: int
    quantity: int
    code: int | None


def extract_ibkr_spb8_rows(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    account_name: str,
    corporate_actions_present: bool = False,
) -> IbkrSPB8Extraction:
    warnings: list[str] = []
    spb8_rows = _extract_cash_rows(rows=rows, active_headers=active_headers, account_name=account_name)
    securities_rows, securities_warnings = _extract_security_rows(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        account_name=account_name,
        corporate_actions_present=corporate_actions_present,
    )
    spb8_rows.extend(securities_rows)
    warnings.extend(securities_warnings)
    if securities_rows:
        warnings.append(
            "СПБ-8: количествата за ценни книжа са изчислени от Open Positions, Trades и Transfers; корпоративни събития не са обработени автоматично."
        )
    return IbkrSPB8Extraction(rows=spb8_rows, warnings=warnings)


def _extract_cash_rows(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    account_name: str,
) -> list[SPB8Row]:
    cash_by_currency: dict[str, dict[str, Decimal | None]] = defaultdict(lambda: {"start": None, "end": None})
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Cash Report" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        try:
            row_kind_idx = _index_for(active_header.headers, "Currency Summary", section_name="Cash Report")
            currency_idx = _index_for(active_header.headers, "Currency", section_name="Cash Report")
            total_idx = _index_for(active_header.headers, "Total", section_name="Cash Report")
        except CsvStructureError:
            continue

        base_len = 2 + len(active_header.headers)
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]
        row_kind = data[row_kind_idx].strip()
        if row_kind not in {"Starting Cash", "Ending Cash"}:
            continue
        currency = data[currency_idx].strip().upper()
        if not _is_iso_currency_code(currency):
            continue
        amount = _parse_decimal_loose_or_zero(data[total_idx])
        if amount is None:
            continue
        if row_kind == "Starting Cash":
            cash_by_currency[currency]["start"] = amount
        else:
            cash_by_currency[currency]["end"] = amount

    result: list[SPB8Row] = []
    for currency in sorted(cash_by_currency):
        values = cash_by_currency[currency]
        result.append(
            SPB8Row(
                account_name=f"{account_name} cash {currency}",
                platform="ibkr",
                type_code="03",
                country="Ирландия",
                currency=currency,
                start_nav=values["start"],
                end_nav=values["end"],
            )
        )
    return result


def _is_iso_currency_code(value: str) -> bool:
    return re.fullmatch(r"[A-Z]{3}", value) is not None


def _extract_security_rows(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    account_name: str,
    corporate_actions_present: bool,
) -> tuple[list[SPB8Row], list[str]]:
    warnings: list[str] = []
    end_qty_by_isin: dict[str, Decimal] = defaultdict(lambda: ZERO)
    trade_delta_by_isin: dict[str, Decimal] = defaultdict(lambda: ZERO)
    transfer_adjustment_by_isin: dict[str, Decimal] = defaultdict(lambda: ZERO)

    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Open Positions" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        field_idx = _open_positions_indexes(active_header)
        base_len = 2 + len(active_header.headers)
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]
        if data[field_idx.discriminator].strip().lower() != "summary":
            continue
        asset_category = data[field_idx.asset].strip()
        if not _is_supported_asset(asset_category):
            continue
        symbol = data[field_idx.symbol].strip()
        instrument, _normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol,
            listings=listings,
        )
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if instrument is None or instrument.isin == "" or quantity is None:
            warnings.append(
                "⚠️ Някои ценни книжа не са включени в СПБ-8 поради липсващ ISIN. "
                f"Symbol={symbol}; quantity={data[field_idx.quantity].strip()}; reason={forced_reason or 'missing ISIN'}"
            )
            continue
        end_qty_by_isin[instrument.isin] += quantity

    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        try:
            field_idx = _trade_order_indexes(active_header)
        except CsvStructureError:
            continue
        base_len = 2 + len(active_header.headers)
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]
        if data[field_idx.discriminator].strip().lower() != "order":
            continue
        asset_category = data[field_idx.asset].strip()
        if not _is_supported_asset(asset_category):
            continue
        symbol = data[field_idx.symbol].strip()
        instrument, _normalized_symbol, _forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol,
            listings=listings,
        )
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if instrument is None or instrument.isin == "" or quantity is None:
            continue
        trade_delta_by_isin[instrument.isin] += quantity

    transfer_adjustment_by_isin.update(
        _extract_transfer_adjustments(
            rows=rows,
            active_headers=active_headers,
            listings=listings,
            spb8_isins=set(end_qty_by_isin),
            warnings=warnings,
        )
    )

    result: list[SPB8Row] = []
    for isin, end_quantity in sorted(end_qty_by_isin.items()):
        trade_delta = trade_delta_by_isin.get(isin, ZERO)
        transfer_adjustment = transfer_adjustment_by_isin.get(isin, ZERO)
        start_quantity = None if corporate_actions_present else end_quantity - trade_delta + transfer_adjustment
        result.append(
            SPB8Row(
                account_name=f"{account_name} securities",
                platform="ibkr",
                type_code="04",
                country="Ирландия",
                currency="",
                start_nav=start_quantity,
                end_nav=end_quantity,
                isin=isin,
            )
        )
    return result, warnings


def _transfer_indexes(active_header: _ActiveHeader) -> _TransferFieldIndexes:
    section_name = f"Transfers header at row {active_header.row_number}"
    return _TransferFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        direction=_index_for(active_header.headers, "Direction", section_name=section_name),
        quantity=_index_for(active_header.headers, "Qty", section_name=section_name),
        code=_optional_index(active_header.headers, "Code"),
    )


def _extract_transfer_adjustments(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    spb8_isins: set[str],
    warnings: list[str],
) -> dict[str, Decimal]:
    adjustments: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Transfers" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            continue
        try:
            field_idx = _transfer_indexes(active_header)
        except CsvStructureError:
            continue

        base_len = 2 + len(active_header.headers)
        data = (row + [""] * (base_len - len(row)))[2 : 2 + len(active_header.headers)]
        asset_category = data[field_idx.asset].strip()
        symbol = data[field_idx.symbol].strip()
        direction = data[field_idx.direction].strip()
        raw_quantity = data[field_idx.quantity].strip()
        code = data[field_idx.code].strip() if field_idx.code is not None else ""

        if asset_category == "" or asset_category == "Total":
            continue
        if direction == "" or symbol == "" or raw_quantity == "":
            continue
        if _is_cancelled_transfer_code(code):
            continue

        if not _is_supported_asset(asset_category):
            warnings.append(
                "СПБ-8: пропуснат Transfers ред с неподдържан Asset Category. "
                f"Symbol={symbol}; Asset Category={asset_category}; Direction={direction}; Qty={raw_quantity}"
            )
            continue

        instrument, _normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol,
            listings=listings,
        )
        if instrument is None:
            warnings.append(
                "СПБ-8: пропуснат Transfers ред, защото инструментът не може да бъде разпознат. "
                f"Symbol={symbol}; Asset Category={asset_category}; Direction={direction}; Qty={raw_quantity}; "
                f"reason={forced_reason or 'missing instrument mapping'}"
            )
            continue
        if instrument.isin == "":
            warnings.append(
                "СПБ-8: пропуснат Transfers ред, защото инструментът няма ISIN. "
                f"Symbol={symbol}; Asset Category={asset_category}; Direction={direction}; Qty={raw_quantity}"
            )
            continue
        if instrument.isin not in spb8_isins:
            continue

        quantity = _parse_reconciliation_quantity(raw_quantity)
        if quantity is None:
            warnings.append(
                "СПБ-8: пропуснат Transfers ред с невалидно Qty. "
                f"Symbol={symbol}; ISIN={instrument.isin}; Asset Category={asset_category}; "
                f"Direction={direction}; Qty={raw_quantity}"
            )
            continue
        if direction in {"In", "Out"}:
            adjustments[instrument.isin] -= quantity
            continue
        warnings.append(
            "СПБ-8: пропуснат Transfers ред с неподдържана Direction. "
            f"Symbol={symbol}; ISIN={instrument.isin}; Asset Category={asset_category}; Direction={direction}; Qty={raw_quantity}"
        )
    return adjustments


def _is_cancelled_transfer_code(code: str) -> bool:
    return "Ca" in {part.strip() for part in re.split(r"[,;\s]+", code) if part.strip()}
