from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable

from services.bnb_fx import BnbFxError
from services.crypto_fx import CryptoFxError

from integrations.crypto.shared.crypto_ir_models import CryptoIrRow, IrAnalysisSummary, ZERO

from .coinbase_parser import (
    load_coinbase_csv,
    normalize_review_status,
    parse_convert_note,
    parse_decimal,
    parse_prefixed_amount,
    parse_timestamp,
)
from .constants import SUPPORTED_TRANSACTION_TYPES
from .models import CoinbaseAnalyzerError, FxConversionError, LoadedCoinbaseCsv

EurUnitRateProvider = Callable[[str, datetime], Decimal]

_TRANSACTION_TYPE_ALIASES = {
    "WITHDRAWAL": "Withdraw",
}


@dataclass(slots=True)
class CoinbaseIrMappingResult:
    loaded_csv: LoadedCoinbaseCsv
    ir_rows: list[CryptoIrRow]


def _normalize_transaction_type(raw: str) -> str:
    text = raw.strip()
    if text == "":
        return text
    aliased = _TRANSACTION_TYPE_ALIASES.get(text.upper())
    if aliased is not None:
        return aliased
    return text


def _parse_quantity(raw: str, *, row_number: int, tx_type: str) -> Decimal:
    qty = parse_decimal(raw, row_number=row_number, field_name="Quantity Transacted")
    qty_abs = abs(qty)
    if qty_abs <= ZERO:
        raise CoinbaseAnalyzerError(f"row {row_number}: Quantity Transacted must be positive for {tx_type}")
    return qty_abs


def _to_eur(
    *,
    amount_raw: str,
    price_currency_raw: str,
    timestamp: datetime,
    row_number: int,
    field_name: str,
    eur_unit_rate_provider: EurUnitRateProvider,
) -> Decimal | None:
    if amount_raw.strip() == "":
        return None

    amount = parse_prefixed_amount(amount_raw, row_number=row_number, field_name=field_name)
    currency = price_currency_raw.strip().upper()
    if currency == "":
        raise CoinbaseAnalyzerError(f"row {row_number}: missing Price Currency for {field_name}")

    try:
        rate = eur_unit_rate_provider(currency, timestamp)
    except (BnbFxError, CryptoFxError, CoinbaseAnalyzerError) as exc:
        raise FxConversionError(
            f"row {row_number}: FX conversion failed for {field_name} "
            f"(currency={currency}, timestamp={timestamp.isoformat()})"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise FxConversionError(
            f"row {row_number}: FX conversion failed for {field_name} "
            f"(currency={currency}, timestamp={timestamp.isoformat()})"
        ) from exc

    if rate <= ZERO:
        raise FxConversionError(
            f"row {row_number}: invalid EUR rate for {field_name} "
            f"(currency={currency}, rate={rate})"
        )

    return amount * rate


def _operation_id(raw: dict[str, str], *, row_number: int) -> str:
    source_id = raw.get("ID", "").strip()
    if source_id != "":
        return f"coinbase-{source_id}"
    return f"coinbase-row-{row_number}"


def _derive_fee(*, subtotal_eur: Decimal | None, total_eur: Decimal | None, fee_eur: Decimal | None) -> Decimal | None:
    if fee_eur is not None:
        return fee_eur
    if subtotal_eur is not None and total_eur is not None:
        return total_eur - subtotal_eur
    return None


def load_and_map_coinbase_csv_to_ir(
    *,
    input_csv: str,
    summary: IrAnalysisSummary,
    eur_unit_rate_provider: EurUnitRateProvider,
) -> CoinbaseIrMappingResult:
    loaded = load_coinbase_csv(input_csv)
    ir_rows: list[CryptoIrRow] = []
    schema = loaded.schema

    for row in loaded.rows:
        raw = row.raw
        row_number = row.row_number
        timestamp = parse_timestamp(raw.get(schema.timestamp, ""), row_number=row_number)
        tx_type = _normalize_transaction_type(raw.get(schema.transaction_type, ""))
        asset = raw.get(schema.asset, "").strip().upper()
        price_currency_raw = raw.get(schema.price_currency, "")
        operation_id = _operation_id(raw, row_number=row_number)

        review_status_raw = raw.get(schema.review_status, "") if schema.review_status is not None else ""
        if review_status_raw.strip() != "":
            summary.manual_check_overrides_rows += 1
        review_status = normalize_review_status(review_status_raw) if review_status_raw.strip() != "" else None

        if tx_type not in SUPPORTED_TRANSACTION_TYPES:
            summary.unsupported_transaction_rows += 1
            summary.unknown_transaction_types.add(tx_type or "EMPTY")
            summary.warnings.append(
                f"row {row_number}: unsupported Transaction Type={tx_type!r}; excluded from tax calculations"
            )
            continue

        subtotal_eur = _to_eur(
            amount_raw=raw.get(schema.subtotal, ""),
            price_currency_raw=price_currency_raw,
            timestamp=timestamp,
            row_number=row_number,
            field_name="Subtotal",
            eur_unit_rate_provider=eur_unit_rate_provider,
        )
        total_eur = _to_eur(
            amount_raw=raw.get(schema.total, ""),
            price_currency_raw=price_currency_raw,
            timestamp=timestamp,
            row_number=row_number,
            field_name="Total",
            eur_unit_rate_provider=eur_unit_rate_provider,
        )
        fee_eur_raw = (
            _to_eur(
                amount_raw=raw.get(schema.fees, ""),
                price_currency_raw=price_currency_raw,
                timestamp=timestamp,
                row_number=row_number,
                field_name="Fees and/or Spread",
                eur_unit_rate_provider=eur_unit_rate_provider,
            )
            if schema.fees is not None
            else None
        )
        fee_eur = _derive_fee(subtotal_eur=subtotal_eur, total_eur=total_eur, fee_eur=fee_eur_raw)

        # For Coinbase Statements CSV:
        # - Total = Subtotal + Fees
        # - Use Total for all economic values
        # - Exception: Convert -> Subtotal = sell proceeds, Total = buy cost
        if tx_type == "Buy":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            if total_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Total for Buy")
            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type="Buy",
                    asset=asset,
                    asset_type="crypto",
                    quantity=quantity,
                    proceeds_eur=abs(total_eur),
                    fee_eur=fee_eur,
                    cost_basis_eur=None,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type="Buy",
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                )
            )
            continue

        if tx_type == "Sell":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            if total_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Total for Sell")
            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type="Sell",
                    asset=asset,
                    asset_type="crypto",
                    quantity=-quantity,
                    proceeds_eur=abs(total_eur),
                    fee_eur=fee_eur,
                    cost_basis_eur=None,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type="Sell",
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                )
            )
            continue

        if tx_type == "Convert":
            if subtotal_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Subtotal for Convert")
            if total_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Total for Convert")
            note = parse_convert_note(raw.get(schema.notes, ""), row_number=row_number)

            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type="Sell",
                    asset=note.asset_sold,
                    asset_type="crypto",
                    quantity=-note.qty_sold,
                    proceeds_eur=abs(subtotal_eur),
                    fee_eur=None,
                    cost_basis_eur=None,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type="Convert",
                    operation_leg="SELL",
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                    sort_index=0,
                )
            )
            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type="Buy",
                    asset=note.asset_bought,
                    asset_type="crypto",
                    quantity=note.qty_bought,
                    proceeds_eur=abs(total_eur),
                    fee_eur=fee_eur,
                    cost_basis_eur=None,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type="Convert",
                    operation_leg="BUY",
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                    sort_index=1,
                )
            )
            continue

        if tx_type == "Send":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type="Withdraw",
                    asset=asset,
                    asset_type="crypto",
                    quantity=-quantity,
                    proceeds_eur=abs(total_eur) if total_eur is not None else None,
                    fee_eur=fee_eur,
                    cost_basis_eur=None,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type="Send",
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                )
            )
            continue

        if tx_type == "Receive":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            purchase_price_raw = raw.get(schema.purchase_price, "") if schema.purchase_price is not None else ""
            if purchase_price_raw.strip() == "":
                raise CoinbaseAnalyzerError(
                    f"row {row_number}: missing Purchase Price for Receive"
                )
            purchase_price_eur = parse_prefixed_amount(
                purchase_price_raw,
                row_number=row_number,
                field_name="Purchase Price",
            )
            if purchase_price_eur < ZERO:
                raise CoinbaseAnalyzerError(
                    f"row {row_number}: Purchase Price for Receive must not be negative"
                )

            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type="Deposit",
                    asset=asset,
                    asset_type="crypto",
                    quantity=quantity,
                    proceeds_eur=abs(total_eur) if total_eur is not None else None,
                    fee_eur=fee_eur,
                    cost_basis_eur=purchase_price_eur,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type="Receive",
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                )
            )
            continue

        if tx_type == "Deposit" or tx_type == "Withdraw":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            ir_rows.append(
                CryptoIrRow(
                    timestamp=timestamp,
                    operation_id=operation_id,
                    transaction_type=tx_type,
                    asset=asset,
                    asset_type="fiat",
                    quantity=quantity if tx_type == "Deposit" else -quantity,
                    proceeds_eur=abs(total_eur) if total_eur is not None else None,
                    fee_eur=fee_eur,
                    cost_basis_eur=None,
                    review_status=review_status,
                    source_exchange="coinbase",
                    source_row_number=row_number,
                    source_transaction_type=tx_type,
                    subtotal_eur=subtotal_eur,
                    total_eur=total_eur,
                )
            )
            continue

        raise CoinbaseAnalyzerError(f"row {row_number}: unsupported mapped transaction type={tx_type!r}")

    return CoinbaseIrMappingResult(loaded_csv=loaded, ir_rows=ir_rows)


__all__ = [name for name in globals() if not name.startswith("__")]
