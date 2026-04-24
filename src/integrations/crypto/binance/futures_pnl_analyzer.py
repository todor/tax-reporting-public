from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable

from config import OUTPUT_DIR
from services.bnb_fx import get_exchange_rate

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "User ID",
    "Time",
    "Account",
    "Operation",
    "Coin",
    "Change",
    "Remark",
]

RELEVANT_OPERATIONS = {"Fee", "Funding Fee", "Realized Profit and Loss"}
EXPECTED_COIN = "BNFCR"
TECHNICAL_DETAILS_SEPARATOR = (
    "------------------------------ Technical Details ------------------------------"
)

DECIMAL_TWO = Decimal("0.01")
DECIMAL_EIGHT = Decimal("0.00000001")
ZERO = Decimal("0")

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "binance" / "futures"

DETAILED_COLUMNS = [
    "original_row_number",
    "user_id",
    "time",
    "account",
    "operation",
    "coin",
    "change_bnfcr",
    "amount_usd",
    "fx_usd_eur_rate",
    "amount_eur",
    "profit_usd",
    "loss_usd",
    "profit_eur",
    "loss_eur",
    "sale_price_usd",
    "purchase_price_usd",
    "sale_price_eur",
    "purchase_price_eur",
    "remark",
]


class FuturesPnlAnalyzerError(Exception):
    """Base error for futures PnL analyzer failures."""


class CsvValidationError(FuturesPnlAnalyzerError):
    """Raised when input CSV headers are invalid."""


class UnexpectedCurrencyError(FuturesPnlAnalyzerError):
    """Raised when a relevant row contains unsupported Coin."""


EurRateProvider = Callable[[datetime], Decimal]


@dataclass(slots=True)
class PnlRow:
    original_row_number: int
    user_id: str
    time_raw: str
    time: datetime
    account: str
    operation: str
    coin: str
    change_bnfcr: Decimal
    remark: str


@dataclass(slots=True)
class AggregatedTotals:
    processed_rows: int = 0
    ignored_rows: int = 0
    profit_usd: Decimal = ZERO
    loss_usd: Decimal = ZERO
    profit_eur: Decimal = ZERO
    loss_eur: Decimal = ZERO

    @property
    def sale_price_usd(self) -> Decimal:
        return self.profit_usd

    @property
    def purchase_price_usd(self) -> Decimal:
        return self.loss_usd

    @property
    def sale_price_eur(self) -> Decimal:
        return self.profit_eur

    @property
    def purchase_price_eur(self) -> Decimal:
        return self.loss_eur

    @property
    def net_result_usd(self) -> Decimal:
        return self.profit_usd - self.loss_usd

    @property
    def net_result_eur(self) -> Decimal:
        return self.profit_eur - self.loss_eur


@dataclass(slots=True)
class AnalysisResult:
    tax_year: int
    input_csv_path: Path
    detailed_csv_path: Path
    tax_text_path: Path
    summary_json_path: Path
    totals: AggregatedTotals


def _fmt_decimal(value: Decimal, *, quant: Decimal | None = None) -> str:
    if quant is not None:
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(value, "f")


def _parse_time(value: str, *, row_number: int) -> datetime:
    text = value.strip()
    if not text:
        raise FuturesPnlAnalyzerError(f"row {row_number}: missing Time")

    candidates = [text]
    if text.endswith("Z"):
        candidates.append(f"{text[:-1]}+00:00")

    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    # Fast-path fixed-width legacy Binance formats to avoid repeated strptime attempts.
    if (
        len(text) == 17
        and text[2] in {"-", "/"}
        and text[5] == text[2]
        and text[8] == " "
        and text[11] == ":"
        and text[14] == ":"
    ):
        fmt = "%y-%m-%d %H:%M:%S" if text[2] == "-" else "%y/%m/%d %H:%M:%S"
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    if (
        len(text) == 19
        and text[4] in {"-", "/"}
        and text[7] == text[4]
        and text[10] == " "
        and text[13] == ":"
        and text[16] == ":"
    ):
        fmt = "%Y-%m-%d %H:%M:%S" if text[4] == "-" else "%Y/%m/%d %H:%M:%S"
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%y-%m-%d %H:%M:%S",
        "%y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise FuturesPnlAnalyzerError(f"row {row_number}: invalid Time format: {value!r}")


def _parse_change(value: str, *, row_number: int) -> Decimal:
    text = value.strip()
    if not text:
        raise FuturesPnlAnalyzerError(f"row {row_number}: missing Change")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise FuturesPnlAnalyzerError(f"row {row_number}: invalid Change: {value!r}") from exc


def _default_eur_rate_provider(cache_dir: str | Path | None) -> EurRateProvider:
    by_date_cache: dict[date, Decimal] = {}

    def provider(ts: datetime) -> Decimal:
        on_date = ts.date()
        cached = by_date_cache.get(on_date)
        if cached is not None:
            return cached

        fx = get_exchange_rate("USD", on_date, cache_dir=cache_dir)
        by_date_cache[on_date] = fx.rate
        return fx.rate

    return provider


def _build_detailed_row(row: PnlRow, *, fx_rate: Decimal, amount_eur: Decimal) -> dict[str, str]:
    amount_usd = row.change_bnfcr
    if amount_usd > 0:
        profit_usd = amount_usd
        loss_usd = ZERO
        profit_eur = amount_eur
        loss_eur = ZERO
        sale_price_usd = amount_usd
        purchase_price_usd = ZERO
        sale_price_eur = amount_eur
        purchase_price_eur = ZERO
    elif amount_usd < 0:
        profit_usd = ZERO
        loss_usd = -amount_usd
        profit_eur = ZERO
        loss_eur = -amount_eur
        sale_price_usd = ZERO
        purchase_price_usd = -amount_usd
        sale_price_eur = ZERO
        purchase_price_eur = -amount_eur
    else:
        profit_usd = ZERO
        loss_usd = ZERO
        profit_eur = ZERO
        loss_eur = ZERO
        sale_price_usd = ZERO
        purchase_price_usd = ZERO
        sale_price_eur = ZERO
        purchase_price_eur = ZERO

    return {
        "original_row_number": str(row.original_row_number),
        "user_id": row.user_id,
        "time": row.time_raw,
        "account": row.account,
        "operation": row.operation,
        "coin": row.coin,
        "change_bnfcr": _fmt_decimal(row.change_bnfcr),
        "amount_usd": _fmt_decimal(amount_usd),
        "fx_usd_eur_rate": _fmt_decimal(fx_rate, quant=DECIMAL_EIGHT),
        "amount_eur": _fmt_decimal(amount_eur, quant=DECIMAL_EIGHT),
        "profit_usd": _fmt_decimal(profit_usd),
        "loss_usd": _fmt_decimal(loss_usd),
        "profit_eur": _fmt_decimal(profit_eur, quant=DECIMAL_EIGHT),
        "loss_eur": _fmt_decimal(loss_eur, quant=DECIMAL_EIGHT),
        "sale_price_usd": _fmt_decimal(sale_price_usd),
        "purchase_price_usd": _fmt_decimal(purchase_price_usd),
        "sale_price_eur": _fmt_decimal(sale_price_eur, quant=DECIMAL_EIGHT),
        "purchase_price_eur": _fmt_decimal(purchase_price_eur, quant=DECIMAL_EIGHT),
        "remark": row.remark,
    }


def _validate_header(actual_columns: list[str] | None, *, csv_path: Path) -> None:
    if actual_columns is None:
        raise CsvValidationError(f"{csv_path}: CSV header is missing")
    missing = [column for column in REQUIRED_COLUMNS if column not in actual_columns]
    if missing:
        raise CsvValidationError(f"{csv_path}: missing required columns: {missing}")


def _read_relevant_rows(path: Path, *, tax_year: int) -> tuple[list[PnlRow], int]:
    rows: list[PnlRow] = []
    ignored_rows = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _validate_header(reader.fieldnames, csv_path=path)

        for row_number, raw in enumerate(reader, start=1):
            operation = (raw.get("Operation") or "").strip()
            if operation not in RELEVANT_OPERATIONS:
                ignored_rows += 1
                continue

            time_raw = (raw.get("Time") or "").strip()
            row_time = _parse_time(time_raw, row_number=row_number)
            coin = (raw.get("Coin") or "").strip()
            change_raw = raw.get("Change") or ""
            change = _parse_change(change_raw, row_number=row_number)

            if coin != EXPECTED_COIN:
                raise UnexpectedCurrencyError(
                    f"row {row_number}: unexpected currency (Time={time_raw}, Operation={operation}, "
                    f"Coin={coin!r}, Change={change_raw!r})"
                )

            if row_time.year != tax_year:
                ignored_rows += 1
                continue

            rows.append(
                PnlRow(
                    original_row_number=row_number,
                    user_id=(raw.get("User ID") or "").strip(),
                    time_raw=time_raw,
                    time=row_time,
                    account=(raw.get("Account") or "").strip(),
                    operation=operation,
                    coin=coin,
                    change_bnfcr=change,
                    remark=(raw.get("Remark") or "").strip(),
                )
            )

    return rows, ignored_rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_tax_text(path: Path, *, tax_year: int, totals: AggregatedTotals) -> None:
    lines = [f"Данъчна година: {tax_year}"]
    should_render_appendix = any(
        value != ZERO
        for value in (
            totals.sale_price_eur,
            totals.purchase_price_eur,
            totals.profit_eur,
            totals.loss_eur,
            totals.net_result_eur,
        )
    )
    if should_render_appendix:
        lines.extend(
            [
                "",
                "Приложение 5",
                "Таблица 2",
                f"- Продажна цена (EUR) - код 5082: {_fmt_decimal(totals.sale_price_eur, quant=DECIMAL_TWO)}",
                f"  Цена на придобиване (EUR) - код 5082: {_fmt_decimal(totals.purchase_price_eur, quant=DECIMAL_TWO)}",
                f"  Печалба (EUR) - код 5082: {_fmt_decimal(totals.profit_eur, quant=DECIMAL_TWO)}",
                f"  Загуба (EUR) - код 5082: {_fmt_decimal(totals.loss_eur, quant=DECIMAL_TWO)}",
            ]
        )
        if totals.net_result_eur != ZERO:
            lines.extend(
                [
                    "Информативни",
                    f"- Нетен резултат (EUR): {_fmt_decimal(totals.net_result_eur, quant=DECIMAL_TWO)}",
                ]
            )
    technical_lines = [
        "Audit Data",
        f"- profit_usd: {_fmt_decimal(totals.profit_usd)}",
        f"- loss_usd: {_fmt_decimal(totals.loss_usd)}",
        f"- sale_price_usd: {_fmt_decimal(totals.sale_price_usd)}",
        f"- purchase_price_usd: {_fmt_decimal(totals.purchase_price_usd)}",
        f"- net_result_usd: {_fmt_decimal(totals.net_result_usd)}",
        f"- processed_rows: {totals.processed_rows}",
        f"- ignored_rows: {totals.ignored_rows}",
    ]
    if technical_lines:
        lines.extend(["", TECHNICAL_DETAILS_SEPARATOR, ""])
        lines.extend(technical_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_summary_json(path: Path, *, tax_year: int, totals: AggregatedTotals) -> None:
    payload = {
        "tax_year": tax_year,
        "processed_rows": totals.processed_rows,
        "ignored_rows": totals.ignored_rows,
        "sale_price_usd": _fmt_decimal(totals.sale_price_usd),
        "purchase_price_usd": _fmt_decimal(totals.purchase_price_usd),
        "profit_usd": _fmt_decimal(totals.profit_usd),
        "loss_usd": _fmt_decimal(totals.loss_usd),
        "sale_price_eur": _fmt_decimal(totals.sale_price_eur, quant=DECIMAL_TWO),
        "purchase_price_eur": _fmt_decimal(totals.purchase_price_eur, quant=DECIMAL_TWO),
        "profit_eur": _fmt_decimal(totals.profit_eur, quant=DECIMAL_TWO),
        "loss_eur": _fmt_decimal(totals.loss_eur, quant=DECIMAL_TWO),
        "net_result_usd": _fmt_decimal(totals.net_result_usd),
        "net_result_eur": _fmt_decimal(totals.net_result_eur, quant=DECIMAL_TWO),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def analyze_futures_pnl_report(
    *,
    input_csv: str | Path,
    tax_year: int,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    eur_rate_provider: EurRateProvider | None = None,
) -> AnalysisResult:
    """Analyze Binance Futures PnL cashflows for a given tax year.

    Input source is Binance Futures PnL / Transaction History with columns:
    User ID, Time, Account, Operation, Coin, Change, Remark.

    Logic:
    - Use only rows where Operation is Fee / Funding Fee / Realized Profit and Loss.
    - Require Coin=BNFCR and treat 1 BNFCR = 1 USD.
    - Aggregate by Change sign only (no FIFO, no position tracking).
    """
    if tax_year < 2009 or tax_year > 2100:
        raise FuturesPnlAnalyzerError(f"invalid tax year: {tax_year}")

    input_path = Path(input_csv).expanduser().resolve()
    if not input_path.exists():
        raise FuturesPnlAnalyzerError(f"input CSV does not exist: {input_path}")

    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rate_provider = eur_rate_provider if eur_rate_provider is not None else _default_eur_rate_provider(cache_dir)

    relevant_rows, ignored_rows = _read_relevant_rows(input_path, tax_year=tax_year)
    detailed_rows: list[dict[str, str]] = []
    totals = AggregatedTotals(processed_rows=len(relevant_rows), ignored_rows=ignored_rows)

    for row in relevant_rows:
        amount_usd = row.change_bnfcr
        fx_rate = rate_provider(row.time)
        amount_eur = amount_usd * fx_rate

        detailed_rows.append(_build_detailed_row(row, fx_rate=fx_rate, amount_eur=amount_eur))

        if amount_usd > 0:
            totals.profit_usd += amount_usd
            totals.profit_eur += amount_eur
        elif amount_usd < 0:
            totals.loss_usd += -amount_usd
            totals.loss_eur += -amount_eur

    detailed_path = out_dir / f"futures_pnl_detailed_{tax_year}.csv"
    tax_text_path = out_dir / f"futures_pnl_tax_{tax_year}.txt"
    summary_json_path = out_dir / f"futures_pnl_summary_{tax_year}.json"

    _write_csv(detailed_path, DETAILED_COLUMNS, detailed_rows)
    _write_tax_text(tax_text_path, tax_year=tax_year, totals=totals)
    _write_summary_json(summary_json_path, tax_year=tax_year, totals=totals)

    return AnalysisResult(
        tax_year=tax_year,
        input_csv_path=input_path,
        detailed_csv_path=detailed_path,
        tax_text_path=tax_text_path,
        summary_json_path=summary_json_path,
        totals=totals,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="binance-futures-pnl-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="Binance Futures PnL / Transaction History CSV")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year to process")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--cache-dir", type=Path, help="Optional bnb_fx cache dir override")
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
        result = analyze_futures_pnl_report(
            input_csv=args.input,
            tax_year=args.tax_year,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except FuturesPnlAnalyzerError as exc:
        logger.error("%s", exc)
        print("STATUS: ERROR")
        return 2

    print("STATUS: SUCCESS")
    print(f"Detailed CSV: {result.detailed_csv_path}")
    print(f"Tax text file: {result.tax_text_path}")
    print(f"Summary file: {result.summary_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
