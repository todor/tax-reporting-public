from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.bnb_fx import BnbFxError
from services.crypto_fx import CryptoFxError

from integrations.crypto.shared.crypto_ir_models import (
    CryptoIrRow,
    IrAnalysisSummary,
    RECEIVE_REVIEW_STATUSES,
    ZERO,
)
from integrations.crypto.shared.runtime import EurUnitRateProvider

from .constants import USD_LIKE_ASSETS
from .kraken_parser import (
    load_kraken_csv,
    normalize_review_status,
    parse_decimal,
    parse_prefixed_amount,
    parse_timestamp,
)
from .models import FxConversionError, KrakenAnalyzerError, LoadedKrakenCsv


@dataclass(slots=True)
class KrakenIrMappingResult:
    loaded_csv: LoadedKrakenCsv
    ir_rows: list[CryptoIrRow]


@dataclass(slots=True)
class _ParsedKrakenRow:
    row_number: int
    txid: str
    refid: str
    timestamp: datetime
    tx_type: str
    subtype: str
    aclass: str
    subclass: str
    asset: str
    wallet: str
    amount: Decimal
    fee: Decimal
    balance: Decimal
    review_status_raw: str
    cost_basis_raw: str


def _parse_row(raw: dict[str, str], *, row_number: int, schema) -> _ParsedKrakenRow:
    return _ParsedKrakenRow(
        row_number=row_number,
        txid=raw.get(schema.txid, "").strip(),
        refid=raw.get(schema.refid, "").strip(),
        timestamp=parse_timestamp(raw.get(schema.time, ""), row_number=row_number),
        tx_type=raw.get(schema.type, "").strip().lower(),
        subtype=raw.get(schema.subtype, "").strip().lower(),
        aclass=raw.get(schema.aclass, "").strip().lower(),
        subclass=raw.get(schema.subclass, "").strip().lower(),
        asset=raw.get(schema.asset, "").strip().upper(),
        wallet=raw.get(schema.wallet, "").strip(),
        amount=parse_decimal(raw.get(schema.amount, ""), row_number=row_number, field_name="amount"),
        fee=parse_decimal(raw.get(schema.fee, ""), row_number=row_number, field_name="fee"),
        balance=parse_decimal(raw.get(schema.balance, ""), row_number=row_number, field_name="balance"),
        review_status_raw=raw.get(schema.review_status, "").strip() if schema.review_status is not None else "",
        cost_basis_raw=raw.get(schema.cost_basis_eur, "").strip() if schema.cost_basis_eur is not None else "",
    )


def _combo_name(row: _ParsedKrakenRow) -> str:
    return f"{row.tx_type}/{row.subtype}/{row.subclass}"


def _operation_id(row: _ParsedKrakenRow) -> str:
    if row.refid != "":
        return row.refid
    if row.txid != "":
        return f"kraken-{row.txid}"
    return f"kraken-row-{row.row_number}"


def _asset_type_from_subclass(subclass: str) -> str:
    return "fiat" if subclass.lower() == "fiat" else "crypto"


def _net_quantity_from_amount_and_fee(
    *,
    amount: Decimal,
    fee: Decimal,
    row_number: int,
    context: str,
) -> Decimal:
    quantity = abs(amount)
    fee_abs = abs(fee)
    if fee_abs == ZERO:
        return quantity
    if fee_abs >= quantity:
        raise KrakenAnalyzerError(
            f"row {row_number}: fee must be smaller than amount for {context}; amount={amount} fee={fee}"
        )
    return quantity - fee_abs


def _to_eur_amount(
    *,
    amount: Decimal,
    asset: str,
    timestamp: datetime,
    row_number: int,
    context: str,
    eur_unit_rate_provider: EurUnitRateProvider,
) -> Decimal:
    amount_abs = abs(amount)
    normalized = asset.strip().upper()
    if normalized == "":
        raise KrakenAnalyzerError(f"row {row_number}: missing asset for {context}")

    if normalized == "EUR":
        return amount_abs

    symbol = "USD" if normalized in USD_LIKE_ASSETS else normalized

    try:
        rate = eur_unit_rate_provider(symbol, timestamp)
    except (BnbFxError, CryptoFxError, KrakenAnalyzerError) as exc:
        raise FxConversionError(
            f"row {row_number}: FX conversion failed for {context} "
            f"(asset={normalized}, symbol={symbol}, timestamp={timestamp.isoformat()})"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise FxConversionError(
            f"row {row_number}: FX conversion failed for {context} "
            f"(asset={normalized}, symbol={symbol}, timestamp={timestamp.isoformat()})"
        ) from exc

    if rate <= ZERO:
        raise FxConversionError(
            f"row {row_number}: invalid EUR rate for {context} (symbol={symbol}, rate={rate})"
        )
    return amount_abs * rate


def _manual_receive_fields_or_warn(
    *,
    row: _ParsedKrakenRow,
    summary: IrAnalysisSummary,
    combo_name: str,
) -> tuple[str, Decimal | None, bool] | None:
    if row.review_status_raw == "":
        _add_unsupported_row(
            summary=summary,
            combo_name=combo_name,
            warning=(
                f"row {row.row_number}: missing Review Status for {row.tx_type}/{row.subtype} {row.asset}; "
                "excluded from tax calculations"
            ),
        )
        return None
    review_status = normalize_review_status(row.review_status_raw)
    normalized_for_validation = review_status.replace("-", "_").upper()
    accepted_review_statuses = sorted({*RECEIVE_REVIEW_STATUSES, "NON_TAXABLE"})
    if normalized_for_validation == "NON_TAXABLE":
        return review_status, None, True
    if normalized_for_validation not in RECEIVE_REVIEW_STATUSES:
        _add_unsupported_row(
            summary=summary,
            combo_name=combo_name,
            warning=(
                f"row {row.row_number}: invalid Review Status for receive-like deposit={row.review_status_raw!r}; "
                f"excluded from tax calculations (accepted values: {accepted_review_statuses})"
            ),
        )
        return None
    if normalized_for_validation == "GIFT":
        return review_status, ZERO, False

    if row.cost_basis_raw == "":
        _add_unsupported_row(
            summary=summary,
            combo_name=combo_name,
            warning=(
                f"row {row.row_number}: missing Cost Basis (EUR) for {row.tx_type}/{row.subtype} {row.asset}; "
                "excluded from tax calculations"
            ),
        )
        return None

    try:
        cost_basis_eur = parse_prefixed_amount(
            row.cost_basis_raw,
            row_number=row.row_number,
            field_name="Cost Basis (EUR)",
        )
    except KrakenAnalyzerError:
        _add_unsupported_row(
            summary=summary,
            combo_name=combo_name,
            warning=(
                f"row {row.row_number}: invalid Cost Basis (EUR)={row.cost_basis_raw!r}; "
                "excluded from tax calculations"
            ),
        )
        return None
    if cost_basis_eur < ZERO:
        _add_unsupported_row(
            summary=summary,
            combo_name=combo_name,
            warning=(
                f"row {row.row_number}: Cost Basis (EUR) must not be negative for receive-like deposit; "
                "excluded from tax calculations"
            ),
        )
        return None
    return review_status, cost_basis_eur, False


def _add_unsupported_row(
    *,
    summary: IrAnalysisSummary,
    combo_name: str,
    warning: str,
) -> None:
    summary.unsupported_transaction_rows += 1
    summary.unknown_transaction_types.add(combo_name)
    summary.warnings.append(warning)


def _derive_trade_value_eur(
    *,
    sell_row: _ParsedKrakenRow,
    buy_row: _ParsedKrakenRow,
    eur_unit_rate_provider: EurUnitRateProvider,
) -> Decimal:
    legs = [sell_row, buy_row]
    for asset in ("EUR", "USD"):
        leg = next((item for item in legs if item.asset == asset), None)
        if leg is not None:
            return _to_eur_amount(
                amount=leg.amount,
                asset=leg.asset,
                timestamp=leg.timestamp,
                row_number=leg.row_number,
                context="trade value",
                eur_unit_rate_provider=eur_unit_rate_provider,
            )

    leg = next((item for item in legs if item.asset in {"USDC", "USDT"}), None)
    if leg is not None:
        return _to_eur_amount(
            amount=leg.amount,
            asset=leg.asset,
            timestamp=leg.timestamp,
            row_number=leg.row_number,
            context="trade value",
            eur_unit_rate_provider=eur_unit_rate_provider,
        )

    return _to_eur_amount(
        amount=sell_row.amount,
        asset=sell_row.asset,
        timestamp=sell_row.timestamp,
        row_number=sell_row.row_number,
        context="trade value",
        eur_unit_rate_provider=eur_unit_rate_provider,
    )


def _derive_trade_fee_eur(
    *,
    row: _ParsedKrakenRow,
    implied_rate_eur: Decimal,
    eur_unit_rate_provider: EurUnitRateProvider,
) -> Decimal:
    fee_abs = abs(row.fee)
    if fee_abs == ZERO:
        return ZERO

    if implied_rate_eur > ZERO:
        return fee_abs * implied_rate_eur

    return _to_eur_amount(
        amount=fee_abs,
        asset=row.asset,
        timestamp=row.timestamp,
        row_number=row.row_number,
        context="trade fee",
        eur_unit_rate_provider=eur_unit_rate_provider,
    )


def load_and_map_kraken_csv_to_ir(
    *,
    input_csv: str,
    summary: IrAnalysisSummary,
    eur_unit_rate_provider: EurUnitRateProvider,
) -> KrakenIrMappingResult:
    loaded = load_kraken_csv(input_csv)
    parsed_rows: list[_ParsedKrakenRow] = []
    for row in loaded.rows:
        parsed = _parse_row(row.raw, row_number=row.row_number, schema=loaded.schema)
        parsed_rows.append(parsed)
        if parsed.review_status_raw != "":
            summary.manual_check_overrides_rows += 1

    rows_by_refid: dict[str, list[_ParsedKrakenRow]] = {}
    for row in parsed_rows:
        if row.refid != "":
            rows_by_refid.setdefault(row.refid, []).append(row)

    consumed_rows: set[int] = set()
    ir_rows: list[CryptoIrRow] = []

    for row in parsed_rows:
        if row.row_number in consumed_rows:
            continue

        combo = _combo_name(row)

        if row.tx_type in {"spend", "receive"}:
            group = rows_by_refid.get(row.refid, []) if row.refid != "" else []
            spend_rows = [item for item in group if item.tx_type == "spend"]
            receive_rows = [item for item in group if item.tx_type == "receive"]

            if len(spend_rows) == 1 and len(receive_rows) == 1:
                spend_row = spend_rows[0]
                receive_row = receive_rows[0]
                if spend_row.row_number in consumed_rows or receive_row.row_number in consumed_rows:
                    continue

                if spend_row.amount >= ZERO:
                    raise KrakenAnalyzerError(
                        f"row {spend_row.row_number}: spend amount must be negative for spend/receive pair"
                    )
                if receive_row.amount <= ZERO:
                    raise KrakenAnalyzerError(
                        f"row {receive_row.row_number}: receive amount must be positive for spend/receive pair"
                    )
                if abs(receive_row.fee) > ZERO:
                    raise KrakenAnalyzerError(
                        f"row {receive_row.row_number}: receive fee must be zero in spend/receive pair "
                        "or require manual handling"
                    )

                trade_value_eur = _to_eur_amount(
                    amount=spend_row.amount,
                    asset=spend_row.asset,
                    timestamp=spend_row.timestamp,
                    row_number=spend_row.row_number,
                    context="spend/receive buy value",
                    eur_unit_rate_provider=eur_unit_rate_provider,
                )
                fee_eur = _to_eur_amount(
                    amount=spend_row.fee,
                    asset=spend_row.asset,
                    timestamp=spend_row.timestamp,
                    row_number=spend_row.row_number,
                    context="spend/receive buy fee",
                    eur_unit_rate_provider=eur_unit_rate_provider,
                )
                ir_rows.append(
                    CryptoIrRow(
                        timestamp=spend_row.timestamp,
                        operation_id=_operation_id(spend_row),
                        transaction_type="Buy",
                        asset=receive_row.asset,
                        asset_type="crypto",
                        quantity=abs(receive_row.amount),
                        proceeds_eur=trade_value_eur,
                        fee_eur=fee_eur,
                        cost_basis_eur=None,
                        review_status=None,
                        source_exchange="kraken",
                        source_row_number=min(spend_row.row_number, receive_row.row_number),
                        source_transaction_type="spend+receive",
                    )
                )
                consumed_rows.add(spend_row.row_number)
                consumed_rows.add(receive_row.row_number)
                continue

            if row.tx_type == "receive" and (
                row.refid == "" or (len(spend_rows) == 0 and len(receive_rows) <= 1)
            ):
                manual_fields = _manual_receive_fields_or_warn(
                    row=row,
                    summary=summary,
                    combo_name=combo,
                )
                if manual_fields is None:
                    consumed_rows.add(row.row_number)
                    continue
                review_status, cost_basis_eur, non_taxable = manual_fields
                quantity = _net_quantity_from_amount_and_fee(
                    amount=row.amount,
                    fee=row.fee,
                    row_number=row.row_number,
                    context="standalone receive",
                )
                proceeds_eur = None
                if non_taxable:
                    proceeds_eur = _to_eur_amount(
                        amount=quantity,
                        asset=row.asset,
                        timestamp=row.timestamp,
                        row_number=row.row_number,
                        context="non-taxable receive value",
                        eur_unit_rate_provider=eur_unit_rate_provider,
                    )
                ir_rows.append(
                    CryptoIrRow(
                        timestamp=row.timestamp,
                        operation_id=_operation_id(row),
                        transaction_type="Deposit",
                        asset=row.asset,
                        asset_type="crypto",
                        quantity=quantity,
                        proceeds_eur=proceeds_eur,
                        fee_eur=None,
                        cost_basis_eur=cost_basis_eur,
                        review_status=review_status,
                        source_exchange="kraken",
                        source_row_number=row.row_number,
                        source_transaction_type="Receive",
                    )
                )
                consumed_rows.add(row.row_number)
                continue

            _add_unsupported_row(
                summary=summary,
                combo_name=combo,
                warning=(
                    f"row {row.row_number}: malformed spend/receive grouping for refid={row.refid!r}; "
                    f"spend_rows={len(spend_rows)} receive_rows={len(receive_rows)}; "
                    "excluded from tax calculations"
                ),
            )
            consumed_rows.add(row.row_number)
            continue

        if row.tx_type == "trade" and row.subtype == "tradespot":
            if row.refid == "":
                _add_unsupported_row(
                    summary=summary,
                    combo_name=combo,
                    warning=(
                        f"row {row.row_number}: trade/tradespot row missing refid; excluded from tax calculations"
                    ),
                )
                consumed_rows.add(row.row_number)
                continue

            group = [
                item
                for item in rows_by_refid.get(row.refid, [])
                if item.tx_type == "trade" and item.subtype == "tradespot"
            ]
            if any(item.row_number in consumed_rows for item in group):
                continue

            if len(group) != 2:
                _add_unsupported_row(
                    summary=summary,
                    combo_name=combo,
                    warning=(
                        f"row {row.row_number}: trade/tradespot pair must have exactly two rows for refid={row.refid}; "
                        f"found {len(group)}; excluded from tax calculations"
                    ),
                )
                consumed_rows.update(item.row_number for item in group)
                if len(group) == 0:
                    consumed_rows.add(row.row_number)
                continue

            sell_candidates = [item for item in group if item.amount < ZERO]
            buy_candidates = [item for item in group if item.amount > ZERO]
            if len(sell_candidates) != 1 or len(buy_candidates) != 1:
                _add_unsupported_row(
                    summary=summary,
                    combo_name=combo,
                    warning=(
                        f"row {row.row_number}: trade/tradespot pair must contain one negative and one positive row "
                        f"for refid={row.refid}; excluded from tax calculations"
                    ),
                )
                consumed_rows.update(item.row_number for item in group)
                continue

            sell_row = sell_candidates[0]
            buy_row = buy_candidates[0]

            trade_value_eur = _derive_trade_value_eur(
                sell_row=sell_row,
                buy_row=buy_row,
                eur_unit_rate_provider=eur_unit_rate_provider,
            )

            sold_amount = abs(sell_row.amount)
            bought_amount = abs(buy_row.amount)
            if sold_amount <= ZERO or bought_amount <= ZERO:
                raise KrakenAnalyzerError(
                    f"row {row.row_number}: trade/tradespot amounts must be non-zero for refid={row.refid}"
                )

            implied_sell_rate_eur = trade_value_eur / sold_amount
            implied_buy_rate_eur = trade_value_eur / bought_amount

            sell_fee_eur = _derive_trade_fee_eur(
                row=sell_row,
                implied_rate_eur=implied_sell_rate_eur,
                eur_unit_rate_provider=eur_unit_rate_provider,
            )
            buy_fee_eur = _derive_trade_fee_eur(
                row=buy_row,
                implied_rate_eur=implied_buy_rate_eur,
                eur_unit_rate_provider=eur_unit_rate_provider,
            )

            buy_quantity = _net_quantity_from_amount_and_fee(
                amount=buy_row.amount,
                fee=buy_row.fee,
                row_number=buy_row.row_number,
                context="trade buy leg",
            )
            sell_proceeds_eur = trade_value_eur - sell_fee_eur
            if sell_proceeds_eur < ZERO:
                raise KrakenAnalyzerError(
                    f"row {sell_row.row_number}: trade sell proceeds net of fees must not be negative "
                    f"(refid={sell_row.refid})"
                )

            operation_id = _operation_id(sell_row)
            ir_rows.append(
                CryptoIrRow(
                    timestamp=sell_row.timestamp,
                    operation_id=operation_id,
                    transaction_type="Sell",
                    asset=sell_row.asset,
                    asset_type=_asset_type_from_subclass(sell_row.subclass),
                    quantity=-sold_amount,
                    proceeds_eur=sell_proceeds_eur,
                    fee_eur=sell_fee_eur,
                    cost_basis_eur=None,
                    review_status=None,
                    source_exchange="kraken",
                    source_row_number=sell_row.row_number,
                    source_transaction_type="trade",
                    operation_leg="SELL",
                    sort_index=0,
                )
            )
            ir_rows.append(
                CryptoIrRow(
                    timestamp=buy_row.timestamp,
                    operation_id=operation_id,
                    transaction_type="Buy",
                    asset=buy_row.asset,
                    asset_type=_asset_type_from_subclass(buy_row.subclass),
                    quantity=buy_quantity,
                    proceeds_eur=trade_value_eur,
                    fee_eur=buy_fee_eur,
                    cost_basis_eur=None,
                    review_status=None,
                    source_exchange="kraken",
                    source_row_number=buy_row.row_number,
                    source_transaction_type="trade",
                    operation_leg="BUY",
                    sort_index=1,
                )
            )
            consumed_rows.update(item.row_number for item in group)
            continue

        if row.tx_type == "deposit" and row.subtype == "":
            if row.amount <= ZERO:
                raise KrakenAnalyzerError(f"row {row.row_number}: deposit amount must be positive")

            if row.subclass == "fiat":
                ir_rows.append(
                    CryptoIrRow(
                        timestamp=row.timestamp,
                        operation_id=_operation_id(row),
                        transaction_type="Deposit",
                        asset=row.asset,
                        asset_type="fiat",
                        quantity=abs(row.amount),
                        proceeds_eur=_to_eur_amount(
                            amount=row.amount,
                            asset=row.asset,
                            timestamp=row.timestamp,
                            row_number=row.row_number,
                            context="fiat deposit value",
                            eur_unit_rate_provider=eur_unit_rate_provider,
                        ),
                        fee_eur=_to_eur_amount(
                            amount=row.fee,
                            asset=row.asset,
                            timestamp=row.timestamp,
                            row_number=row.row_number,
                            context="fiat deposit fee",
                            eur_unit_rate_provider=eur_unit_rate_provider,
                        ),
                        cost_basis_eur=None,
                        review_status=None,
                        source_exchange="kraken",
                        source_row_number=row.row_number,
                        source_transaction_type="deposit",
                    )
                )
            else:
                manual_fields = _manual_receive_fields_or_warn(
                    row=row,
                    summary=summary,
                    combo_name=combo,
                )
                if manual_fields is None:
                    consumed_rows.add(row.row_number)
                    continue
                review_status, cost_basis_eur, non_taxable = manual_fields
                quantity = _net_quantity_from_amount_and_fee(
                    amount=row.amount,
                    fee=row.fee,
                    row_number=row.row_number,
                    context="crypto deposit",
                )
                proceeds_eur = None
                if non_taxable:
                    proceeds_eur = _to_eur_amount(
                        amount=quantity,
                        asset=row.asset,
                        timestamp=row.timestamp,
                        row_number=row.row_number,
                        context="non-taxable deposit value",
                        eur_unit_rate_provider=eur_unit_rate_provider,
                    )
                ir_rows.append(
                    CryptoIrRow(
                        timestamp=row.timestamp,
                        operation_id=_operation_id(row),
                        transaction_type="Deposit",
                        asset=row.asset,
                        asset_type="crypto",
                        quantity=quantity,
                        proceeds_eur=proceeds_eur,
                        fee_eur=None,
                        cost_basis_eur=cost_basis_eur,
                        review_status=review_status,
                        source_exchange="kraken",
                        source_row_number=row.row_number,
                        source_transaction_type="Receive",
                    )
                )
            consumed_rows.add(row.row_number)
            continue

        if row.tx_type == "transfer" and row.subtype == "spotfromfutures":
            consumed_rows.add(row.row_number)
            continue

        if row.tx_type == "earn" and row.subtype == "autoallocation":
            consumed_rows.add(row.row_number)
            continue

        if row.tx_type == "earn" and row.subtype == "reward":
            if row.amount <= ZERO:
                raise KrakenAnalyzerError(f"row {row.row_number}: earn/reward amount must be positive")
            quantity = _net_quantity_from_amount_and_fee(
                amount=row.amount,
                fee=row.fee,
                row_number=row.row_number,
                context="earn reward",
            )
            ir_rows.append(
                CryptoIrRow(
                    timestamp=row.timestamp,
                    operation_id=_operation_id(row),
                    transaction_type="Earn",
                    asset=row.asset,
                    asset_type="crypto",
                    quantity=quantity,
                    proceeds_eur=ZERO,
                    fee_eur=ZERO,
                    cost_basis_eur=ZERO,
                    review_status=None,
                    source_exchange="kraken",
                    source_row_number=row.row_number,
                    source_transaction_type="earn/reward",
                )
            )
            consumed_rows.add(row.row_number)
            continue

        _add_unsupported_row(
            summary=summary,
            combo_name=combo,
            warning=(
                f"row {row.row_number}: unsupported Kraken combination type/subtype/subclass="
                f"{combo!r}; excluded from tax calculations"
            ),
        )
        consumed_rows.add(row.row_number)

    return KrakenIrMappingResult(loaded_csv=loaded, ir_rows=ir_rows)


__all__ = [name for name in globals() if not name.startswith("__")]
