from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from services.bnb_fx import BnbFxError, get_exchange_rate
from services.crypto_fx import CryptoFxError, get_crypto_eur_rate

from .constants import (
    ADDED_OUTPUT_COLUMNS,
    DECIMAL_EIGHT,
    DECIMAL_TWO,
    DEFAULT_OUTPUT_DIR,
    KNOWN_FIAT_ASSETS,
    KNOWN_FIAT_PRICE_CURRENCIES,
    RECEIVE_REVIEW_STATUSES,
    REVIEW_STATUS_NON_TAXABLE,
    REVIEW_STATUS_TAXABLE,
    SEND_REVIEW_STATUSES,
    SUPPORTED_TRANSACTION_TYPES,
    ZERO,
)
from .ledger import AverageCostLedger
from .models import AnalysisResult, AnalysisSummary, BucketTotals, CoinbaseAnalyzerError, FxConversionError
from .output import fmt_decimal, write_declaration_text, write_modified_csv
from .parsing import (
    load_coinbase_csv,
    normalize_review_status,
    parse_convert_note,
    parse_decimal,
    parse_prefixed_amount,
    parse_timestamp,
)

logger = logging.getLogger(__name__)

EurUnitRateProvider = Callable[[str, datetime], Decimal]
_TRANSACTION_TYPE_ALIASES = {
    "WITHDRAWAL": "Withdraw",
}


class _PreparedRow(dict):
    """Typed dict-like container for internal row preparation."""


def _is_reverse_chronological(rows: list[_PreparedRow]) -> bool:
    if len(rows) < 2:
        return True
    prev_ts = rows[0]["timestamp"]
    for row in rows[1:]:
        current_ts = row["timestamp"]
        if prev_ts < current_ts:
            return False
        prev_ts = current_ts
    return True


def _processing_order(rows: list[_PreparedRow]) -> list[_PreparedRow]:
    if _is_reverse_chronological(rows):
        return list(reversed(rows))
    return sorted(rows, key=lambda item: (item["timestamp"], item["row_number"]))


def _default_eur_unit_rate_provider(cache_dir: str | Path | None) -> EurUnitRateProvider:
    def provider(currency: str, timestamp: datetime) -> Decimal:
        normalized = currency.strip().upper()
        if normalized == "EUR":
            return Decimal("1")

        if normalized in KNOWN_FIAT_PRICE_CURRENCIES:
            fx = get_exchange_rate(normalized, timestamp.date(), cache_dir=cache_dir)
            return fx.rate

        fx_crypto = get_crypto_eur_rate(
            normalized,
            timestamp,
            "binance",
            cache_dir=cache_dir,
        )
        return fx_crypto.price_eur

    return provider


def _output_stem(input_path: Path) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", input_path.stem).strip("_").lower()
    return normalized or "coinbase_report"


def _output_paths(*, input_path: Path, output_dir: Path) -> tuple[Path, Path]:
    stem = _output_stem(input_path)
    return (
        output_dir / f"{stem}_modified.csv",
        output_dir / f"{stem}_declaration.txt",
    )


def _state_output_path(*, input_path: Path, output_dir: Path, tax_year: int) -> Path:
    stem = _output_stem(input_path)
    return output_dir / f"{stem}_state_end_{tax_year}.json"


def _load_opening_state(path: Path) -> tuple[int | None, dict[str, tuple[Decimal, Decimal]]]:
    if not path.exists():
        raise CoinbaseAnalyzerError(f"opening state JSON does not exist: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoinbaseAnalyzerError(f"invalid opening state JSON: {path}") from exc

    year_end_raw = payload.get("state_tax_year_end")
    year_end = int(year_end_raw) if year_end_raw is not None else None

    holdings_payload = payload.get("holdings_by_asset")
    if not isinstance(holdings_payload, dict):
        raise CoinbaseAnalyzerError(
            f"opening state JSON must contain object 'holdings_by_asset': {path}"
        )

    holdings: dict[str, tuple[Decimal, Decimal]] = {}
    for asset_raw, values in holdings_payload.items():
        asset = str(asset_raw).strip().upper()
        if asset == "":
            raise CoinbaseAnalyzerError(f"opening state JSON contains empty asset key: {path}")
        if not isinstance(values, dict):
            raise CoinbaseAnalyzerError(
                f"opening state JSON asset entry must be an object for asset={asset}: {path}"
            )
        quantity_raw = values.get("quantity")
        total_cost_raw = values.get("total_cost_eur")
        if quantity_raw is None or total_cost_raw is None:
            raise CoinbaseAnalyzerError(
                f"opening state JSON asset entry must contain quantity and total_cost_eur for asset={asset}: {path}"
            )
        try:
            quantity = Decimal(str(quantity_raw))
            total_cost_eur = Decimal(str(total_cost_raw))
        except Exception as exc:  # noqa: BLE001
            raise CoinbaseAnalyzerError(
                f"opening state JSON contains invalid decimals for asset={asset}: {path}"
            ) from exc
        holdings[asset] = (quantity, total_cost_eur)

    return year_end, holdings


def _write_year_end_state_json(
    path: Path,
    *,
    tax_year: int,
    holdings_by_asset: dict[str, tuple[Decimal, Decimal]],
) -> None:
    payload = {
        "state_tax_year_end": tax_year,
        "holdings_by_asset": {
            asset: {
                "quantity": format(quantity, "f"),
                "total_cost_eur": format(total_cost_eur, "f"),
            }
            for asset, (quantity, total_cost_eur) in sorted(holdings_by_asset.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def _parse_quantity(raw: str, *, row_number: int, tx_type: str) -> Decimal:
    qty = parse_decimal(raw, row_number=row_number, field_name="Quantity Transacted")
    qty_abs = abs(qty)
    if qty_abs <= ZERO:
        raise CoinbaseAnalyzerError(f"row {row_number}: Quantity Transacted must be positive for {tx_type}")
    return qty_abs


def _normalize_transaction_type(raw: str) -> str:
    text = raw.strip()
    if text == "":
        return text
    aliased = _TRANSACTION_TYPE_ALIASES.get(text.upper())
    if aliased is not None:
        return aliased
    return text


def _apply_disposal(
    bucket: BucketTotals,
    *,
    sale_price_eur: Decimal,
    purchase_price_eur: Decimal,
) -> Decimal:
    net = sale_price_eur - purchase_price_eur

    bucket.sale_price_eur += sale_price_eur
    bucket.purchase_price_eur += purchase_price_eur
    if net > ZERO:
        bucket.wins_eur += net
    elif net < ZERO:
        bucket.losses_eur += -net
    bucket.rows += 1

    return net


def _set_disposal_output_fields(
    row: dict[str, str],
    *,
    purchase_price_eur: Decimal,
    sale_price_eur: Decimal,
    net_profit_eur: Decimal,
) -> None:
    row["Purchase Price (EUR)"] = fmt_decimal(purchase_price_eur, quant=DECIMAL_EIGHT)
    row["Sale Price (EUR)"] = fmt_decimal(sale_price_eur, quant=DECIMAL_EIGHT)
    row["Net Profit (EUR)"] = fmt_decimal(net_profit_eur, quant=DECIMAL_EIGHT)
    if net_profit_eur > ZERO:
        row["Profit Win (EUR)"] = fmt_decimal(net_profit_eur, quant=DECIMAL_EIGHT)
        row["Profit Loss (EUR)"] = fmt_decimal(ZERO, quant=DECIMAL_EIGHT)
    elif net_profit_eur < ZERO:
        row["Profit Win (EUR)"] = fmt_decimal(ZERO, quant=DECIMAL_EIGHT)
        row["Profit Loss (EUR)"] = fmt_decimal(-net_profit_eur, quant=DECIMAL_EIGHT)
    else:
        row["Profit Win (EUR)"] = fmt_decimal(ZERO, quant=DECIMAL_EIGHT)
        row["Profit Loss (EUR)"] = fmt_decimal(ZERO, quant=DECIMAL_EIGHT)


def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise CoinbaseAnalyzerError(f"invalid tax year: {tax_year}")


def analyze_coinbase_report(
    *,
    input_csv: str | Path,
    tax_year: int,
    opening_state_json: str | Path | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    eur_unit_rate_provider: EurUnitRateProvider | None = None,
) -> AnalysisResult:
    _validate_tax_year(tax_year)
    loaded = load_coinbase_csv(input_csv)
    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    output_csv_path, declaration_txt_path = _output_paths(input_path=loaded.input_path, output_dir=out_dir)
    year_end_state_json_path = _state_output_path(
        input_path=loaded.input_path,
        output_dir=out_dir,
        tax_year=tax_year,
    )

    rate_provider = (
        eur_unit_rate_provider
        if eur_unit_rate_provider is not None
        else _default_eur_unit_rate_provider(cache_dir)
    )

    summary = AnalysisSummary(
        processed_rows=len(loaded.rows),
        preamble_rows_ignored=loaded.preamble_rows_ignored,
    )
    ledger = AverageCostLedger()
    if opening_state_json is not None:
        opening_state_path = Path(opening_state_json).expanduser().resolve()
        opening_year_end, opening_holdings = _load_opening_state(opening_state_path)
        if opening_year_end is not None and opening_year_end >= tax_year:
            summary.warnings.append(
                "opening state year is not before requested tax year "
                f"(state_tax_year_end={opening_year_end}, tax_year={tax_year})"
            )
        for asset, (quantity, total_cost_eur) in opening_holdings.items():
            ledger.seed(asset, quantity=quantity, total_cost_eur=total_cost_eur)

    prepared_rows: list[_PreparedRow] = []
    output_rows_by_number: dict[int, dict[str, str]] = {}
    schema = loaded.schema

    for row in loaded.rows:
        raw = row.raw
        row_number = row.row_number

        timestamp = parse_timestamp(raw.get(schema.timestamp, ""), row_number=row_number)
        tx_type_raw = raw.get(schema.transaction_type, "")
        tx_type = _normalize_transaction_type(tx_type_raw)
        asset = raw.get(schema.asset, "").strip().upper()
        price_currency_raw = raw.get(schema.price_currency, "")

        subtotal_raw = raw.get(schema.subtotal, "")
        total_raw = raw.get(schema.total, "")

        subtotal_eur = _to_eur(
            amount_raw=subtotal_raw,
            price_currency_raw=price_currency_raw,
            timestamp=timestamp,
            row_number=row_number,
            field_name="Subtotal",
            eur_unit_rate_provider=rate_provider,
        )
        total_eur = _to_eur(
            amount_raw=total_raw,
            price_currency_raw=price_currency_raw,
            timestamp=timestamp,
            row_number=row_number,
            field_name="Total",
            eur_unit_rate_provider=rate_provider,
        )

        output_row = dict(raw)
        for col in ADDED_OUTPUT_COLUMNS:
            output_row[col] = ""
        if subtotal_eur is not None:
            output_row["Subtotal (EUR)"] = fmt_decimal(subtotal_eur, quant=DECIMAL_EIGHT)
        if total_eur is not None:
            output_row["Total (EUR)"] = fmt_decimal(total_eur, quant=DECIMAL_EIGHT)
        output_rows_by_number[row_number] = output_row
        prepared_rows.append(
            _PreparedRow(
                row_number=row_number,
                raw=raw,
                timestamp=timestamp,
                tx_type=tx_type,
                asset=asset,
                subtotal_eur=subtotal_eur,
                total_eur=total_eur,
            )
        )

    # Process in chronological order for correct average-cost basis evolution:
    # - if input is reverse chronological, reverse it
    # - otherwise sort by timestamp
    year_end_snapshot_captured = False
    for prepared in _processing_order(prepared_rows):
        row_number = prepared["row_number"]
        raw = prepared["raw"]
        tx_type = prepared["tx_type"]
        asset = prepared["asset"]
        timestamp = prepared["timestamp"]
        subtotal_eur = prepared["subtotal_eur"]
        total_eur = prepared["total_eur"]
        output_row = output_rows_by_number[row_number]
        include_in_appendix = timestamp.year == tax_year

        if not year_end_snapshot_captured and timestamp.year > tax_year:
            holdings_before_row = ledger.snapshot()
            year_end_holdings_by_asset = {
                key: (item.quantity, item.total_cost_eur) for key, item in holdings_before_row.items()
            }
            year_end_snapshot_captured = True

        if tx_type not in SUPPORTED_TRANSACTION_TYPES:
            summary.unsupported_transaction_rows += 1
            summary.unknown_transaction_types.add(tx_type or "EMPTY")
            summary.warnings.append(
                f"row {row_number}: unsupported Transaction Type={tx_type!r}; excluded from tax calculations"
            )
            continue

        if tx_type == "Deposit" or tx_type == "Withdraw":
            if asset not in KNOWN_FIAT_ASSETS:
                raise CoinbaseAnalyzerError(
                    f"row {row_number}: {tx_type} is supported only for fiat assets in this analyzer "
                    f"(asset={asset!r})"
                )
            summary.ignored_fiat_deposit_withdraw_rows += 1
            continue

        if tx_type == "Buy":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            if total_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Total for Buy")

            acquisition_cost_eur = abs(total_eur)
            ledger.add(asset, quantity=quantity, total_cost_eur=acquisition_cost_eur, row_number=row_number)
            output_row["Purchase Price (EUR)"] = fmt_decimal(acquisition_cost_eur, quant=DECIMAL_EIGHT)
            continue

        if tx_type == "Sell":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            if subtotal_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Subtotal for Sell")

            sale_price_eur = abs(subtotal_eur)
            purchase_price_eur = ledger.remove(
                asset,
                quantity=quantity,
                row_number=row_number,
                reason="Sell",
            )
            net_profit_eur = sale_price_eur - purchase_price_eur
            if include_in_appendix:
                _apply_disposal(
                    summary.appendix_5,
                    sale_price_eur=sale_price_eur,
                    purchase_price_eur=purchase_price_eur,
                )
            _set_disposal_output_fields(
                output_row,
                purchase_price_eur=purchase_price_eur,
                sale_price_eur=sale_price_eur,
                net_profit_eur=net_profit_eur,
            )
            continue

        if tx_type == "Convert":
            if subtotal_eur is None:
                raise CoinbaseAnalyzerError(f"row {row_number}: missing Subtotal for Convert")

            note = parse_convert_note(raw.get(schema.notes, ""), row_number=row_number)
            sale_price_eur = abs(subtotal_eur)
            purchase_price_eur = ledger.remove(
                note.asset_sold,
                quantity=note.qty_sold,
                row_number=row_number,
                reason="Convert",
            )
            ledger.add(
                note.asset_bought,
                quantity=note.qty_bought,
                total_cost_eur=sale_price_eur,
                row_number=row_number,
            )
            net_profit_eur = sale_price_eur - purchase_price_eur
            if include_in_appendix:
                _apply_disposal(
                    summary.appendix_5,
                    sale_price_eur=sale_price_eur,
                    purchase_price_eur=purchase_price_eur,
                )
            _set_disposal_output_fields(
                output_row,
                purchase_price_eur=purchase_price_eur,
                sale_price_eur=sale_price_eur,
                net_profit_eur=net_profit_eur,
            )
            continue

        if tx_type == "Send":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)
            purchase_price_eur = ledger.remove(
                asset,
                quantity=quantity,
                row_number=row_number,
                reason="Send",
            )
            output_row["Purchase Price (EUR)"] = fmt_decimal(purchase_price_eur, quant=DECIMAL_EIGHT)

            review_status_raw = raw.get(schema.review_status, "") if schema.review_status is not None else ""
            review_status = normalize_review_status(review_status_raw)

            if review_status == REVIEW_STATUS_TAXABLE:
                if subtotal_eur is None:
                    raise CoinbaseAnalyzerError(f"row {row_number}: missing Subtotal for taxable Send")

                sale_price_eur = abs(subtotal_eur)
                net_profit_eur = sale_price_eur - purchase_price_eur
                # Send is never a taxable event for this platform's Appendix 5 totals.
                # For TAXABLE status we keep per-row computed values for downstream transfer workflows only.
                _set_disposal_output_fields(
                    output_row,
                    purchase_price_eur=purchase_price_eur,
                    sale_price_eur=sale_price_eur,
                    net_profit_eur=net_profit_eur,
                )
                if include_in_appendix:
                    summary.taxable_send_rows += 1
            elif review_status == REVIEW_STATUS_NON_TAXABLE:
                summary.non_taxable_send_rows += 1
            else:
                summary.invalid_send_review_rows += 1
                summary.unknown_send_review_statuses.add(review_status or "EMPTY")
                summary.warnings.append(
                    f"row {row_number}: Send without valid Review Status; accepted values: "
                    f"{sorted(SEND_REVIEW_STATUSES)}"
                )

            continue

        if tx_type == "Receive":
            quantity = _parse_quantity(raw.get(schema.quantity_transacted, ""), row_number=row_number, tx_type=tx_type)

            review_status_raw = raw.get(schema.review_status, "") if schema.review_status is not None else ""
            review_status = normalize_review_status(review_status_raw).replace("-", "_")
            if review_status not in RECEIVE_REVIEW_STATUSES:
                raise CoinbaseAnalyzerError(
                    f"row {row_number}: invalid Review Status for Receive={review_status_raw!r}; "
                    f"accepted values: {sorted(RECEIVE_REVIEW_STATUSES)}"
                )

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

            ledger.add(asset, quantity=quantity, total_cost_eur=purchase_price_eur, row_number=row_number)
            output_row["Purchase Price (EUR)"] = fmt_decimal(purchase_price_eur, quant=DECIMAL_EIGHT)
            continue

    summary.holdings_by_asset = ledger.snapshot()
    if year_end_snapshot_captured:
        effective_year_end_holdings = year_end_holdings_by_asset
    else:
        effective_year_end_holdings = {
            key: (item.quantity, item.total_cost_eur) for key, item in summary.holdings_by_asset.items()
        }
    _write_year_end_state_json(
        year_end_state_json_path,
        tax_year=tax_year,
        holdings_by_asset=effective_year_end_holdings,
    )
    output_rows = [output_rows_by_number[row.row_number] for row in loaded.rows]

    output_fieldnames = list(loaded.fieldnames)
    for col in ADDED_OUTPUT_COLUMNS:
        if col not in output_fieldnames:
            output_fieldnames.append(col)

    write_modified_csv(
        output_csv_path,
        fieldnames=output_fieldnames,
        rows=output_rows,
    )
    write_declaration_text(declaration_txt_path, summary=summary)

    return AnalysisResult(
        input_csv_path=loaded.input_path,
        output_csv_path=output_csv_path,
        declaration_txt_path=declaration_txt_path,
        year_end_state_json_path=year_end_state_json_path,
        summary=summary,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coinbase-report-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="Coinbase transaction report CSV")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
    parser.add_argument("--opening-state-json", type=Path, help="Optional prior year-end holdings state JSON")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--cache-dir", type=Path, help="Optional FX cache dir override")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        result = analyze_coinbase_report(
            input_csv=args.input,
            tax_year=args.tax_year,
            opening_state_json=args.opening_state_json,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except CoinbaseAnalyzerError as exc:
        logger.error("%s", exc)
        return 2

    bucket = result.summary.appendix_5
    print(f"processed_rows: {result.summary.processed_rows}")
    print(f"manual_check_required: {'YES' if result.summary.manual_check_required else 'NO'}")
    print(f"sale_price_eur: {fmt_decimal(bucket.sale_price_eur, quant=DECIMAL_TWO)}")
    print(f"purchase_price_eur: {fmt_decimal(bucket.purchase_price_eur, quant=DECIMAL_TWO)}")
    print(f"wins_eur: {fmt_decimal(bucket.wins_eur, quant=DECIMAL_TWO)}")
    print(f"losses_eur: {fmt_decimal(bucket.losses_eur, quant=DECIMAL_TWO)}")
    print(f"net_result_eur: {fmt_decimal(bucket.net_result_eur, quant=DECIMAL_TWO)}")
    print(f"Modified CSV: {result.output_csv_path}")
    print(f"Declaration TXT: {result.declaration_txt_path}")
    print(f"Year-end state JSON: {result.year_end_state_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
