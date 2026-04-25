from __future__ import annotations

import csv
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from integrations.shared.rendering.appendix5 import (
    Appendix5Table2Entry,
    render_appendix5_table2,
)
from integrations.shared.rendering.common import Money

from .fund_ir_models import (
    FundAnalysisRunResult,
    FundAnalysisSummary,
    FundCurrencyState,
    FundEnrichedRow,
    GenericFundAnalyzerError,
    ZERO,
)

DECIMAL_TWO = Decimal("0.01")
DECIMAL_EIGHT = Decimal("0.00000001")
TECHNICAL_DETAILS_SEPARATOR = (
    "------------------------------ Technical Details ------------------------------"
)


def fmt_decimal(value: Decimal, *, quant: Decimal | None = None) -> str:
    if quant is not None:
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(value, "f")


def _fmt_opt(value: Decimal | None, *, quant: Decimal | None = None) -> str:
    if value is None:
        return ""
    return fmt_decimal(value, quant=quant)


def _is_zero_amount(value: Decimal) -> bool:
    return value == ZERO


def _should_render_appendix5(summary: FundAnalysisSummary) -> bool:
    bucket = summary.appendix_5
    return any(
        not _is_zero_amount(amount)
        for amount in (
            bucket.sale_price_eur,
            bucket.purchase_price_eur,
            bucket.wins_eur,
            bucket.losses_eur,
            bucket.net_result_eur,
        )
    ) or bucket.rows > 0


def _validate_declaration_code(value: str) -> str:
    code = value.strip()
    if code == "":
        raise GenericFundAnalyzerError("missing declaration code for Appendix 5 output")
    return code


def _row_to_csv_dict(row: FundEnrichedRow) -> dict[str, str]:
    ir = row.ir_row
    return {
        "Timestamp": ir.timestamp.isoformat(),
        "Operation ID": ir.operation_id,
        "Type": ir.transaction_type,
        "Currency": ir.currency,
        "Currency Type": ir.currency_type,
        "Amount": fmt_decimal(ir.amount),
        "Amount (EUR)": _fmt_opt(row.amount_eur),
        "Balance": _fmt_opt(row.balance_native),
        "Balance (EUR)": _fmt_opt(row.balance_eur),
        "Deposit to Date (EUR)": _fmt_opt(row.deposit_to_date_eur),
        "Source Exchange": ir.source_exchange or "",
        "Source Row": "" if ir.source_row_number is None else str(ir.source_row_number),
        "Source Type": ir.source_transaction_type or "",
        "Purchase Price (EUR)": _fmt_opt(row.purchase_price_eur, quant=DECIMAL_EIGHT),
        "Sale Price (EUR)": _fmt_opt(row.sale_price_eur, quant=DECIMAL_EIGHT),
        "Profit Win (EUR)": _fmt_opt(row.profit_win_eur, quant=DECIMAL_EIGHT),
        "Profit Loss (EUR)": _fmt_opt(row.profit_loss_eur, quant=DECIMAL_EIGHT),
        "Net Profit (EUR)": _fmt_opt(row.net_profit_eur, quant=DECIMAL_EIGHT),
    }


def write_enriched_ir_csv(path: Path, *, rows: list[FundEnrichedRow]) -> None:
    payload = [_row_to_csv_dict(item) for item in rows]
    fieldnames = list(payload[0].keys()) if payload else [
        "Timestamp",
        "Operation ID",
        "Type",
        "Currency",
        "Currency Type",
        "Amount",
        "Amount (EUR)",
        "Balance",
        "Balance (EUR)",
        "Deposit to Date (EUR)",
        "Source Exchange",
        "Source Row",
        "Source Type",
        "Purchase Price (EUR)",
        "Sale Price (EUR)",
        "Profit Win (EUR)",
        "Profit Loss (EUR)",
        "Net Profit (EUR)",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload)


def build_declaration_text(*, summary: FundAnalysisSummary, appendix_5_declaration_code: str) -> str:
    declaration_code = _validate_declaration_code(appendix_5_declaration_code)
    lines: list[str] = []

    if summary.manual_check_required:
        lines.append("!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!")
        for reason in summary.manual_check_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    if _should_render_appendix5(summary):
        bucket = summary.appendix_5
        appendix_lines = render_appendix5_table2(
            [
                Appendix5Table2Entry(
                    code=declaration_code,
                    sale_value=Money(bucket.sale_price_eur, "EUR"),
                    acquisition_value=Money(bucket.purchase_price_eur, "EUR"),
                    profit=Money(bucket.wins_eur, "EUR"),
                    loss=Money(bucket.losses_eur, "EUR"),
                    net_result=Money(bucket.net_result_eur, "EUR"),
                    trade_count=bucket.rows,
                )
            ]
        )
        lines.extend(appendix_lines)
        lines.append("")

    technical_lines: list[str] = []
    if summary.warnings:
        technical_lines.append("Processing Notes")
        for warning in summary.warnings:
            technical_lines.append(f"- {warning}")
        technical_lines.append("")

    technical_lines.append("Audit Data")
    technical_lines.append(f"- processed rows: {summary.processed_rows}")
    technical_lines.append(f"- ignored rows: {summary.ignored_rows}")

    if technical_lines:
        lines.append(TECHNICAL_DETAILS_SEPARATOR)
        lines.append("")
        lines.extend(technical_lines)
    return "\n".join(lines).rstrip() + "\n"


def write_declaration_text(path: Path, *, summary: FundAnalysisSummary, appendix_5_declaration_code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_declaration_text(
            summary=summary,
            appendix_5_declaration_code=appendix_5_declaration_code,
        ),
        encoding="utf-8",
    )


def build_fund_run_cli_summary_lines(*, result: FundAnalysisRunResult) -> list[str]:
    return [
        f"STATUS: {'MANUAL CHECK REQUIRED' if result.summary.manual_check_required else 'SUCCESS'}",
        f"Enriched IR CSV: {result.output_csv_path}",
        f"Declaration TXT: {result.declaration_txt_path}",
        f"Year-end state JSON: {result.year_end_state_json_path}",
    ]


def _state_to_json(state: FundCurrencyState) -> dict[str, str]:
    return {
        "currency_type": state.currency_type,
        "native_deposit_balance": format(state.native_deposit_balance, "f"),
        "eur_deposit_balance": format(state.eur_deposit_balance, "f"),
        "native_profit_balance": format(state.native_profit_balance, "f"),
        "native_total_balance": format(state.native_total_balance, "f"),
    }


def write_fund_state_json(
    path: Path,
    *,
    tax_year: int,
    state_by_currency: dict[str, FundCurrencyState],
) -> None:
    payload = {
        "state_tax_year_end": tax_year,
        "state_by_currency": {
            currency: _state_to_json(state)
            for currency, state in sorted(state_by_currency.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_fund_state_json(path: Path) -> tuple[int | None, dict[str, FundCurrencyState]]:
    if not path.exists():
        raise GenericFundAnalyzerError(f"opening state JSON does not exist: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GenericFundAnalyzerError(f"invalid opening state JSON: {path}") from exc

    year_end_raw = payload.get("state_tax_year_end")
    year_end = int(year_end_raw) if year_end_raw is not None else None

    state_payload = payload.get("state_by_currency")
    if not isinstance(state_payload, dict):
        raise GenericFundAnalyzerError(
            f"opening state JSON must contain object 'state_by_currency': {path}"
        )

    state_by_currency: dict[str, FundCurrencyState] = {}
    for currency_raw, values in state_payload.items():
        currency = str(currency_raw).strip().upper()
        if currency == "":
            raise GenericFundAnalyzerError(f"opening state JSON contains empty currency key: {path}")
        if not isinstance(values, dict):
            raise GenericFundAnalyzerError(
                f"opening state JSON currency entry must be an object for currency={currency}: {path}"
            )

        currency_type = str(values.get("currency_type", "")).strip().lower()
        if currency_type not in {"fiat", "crypto"}:
            raise GenericFundAnalyzerError(
                f"opening state JSON invalid currency_type for currency={currency}: {path}"
            )

        deposit_native_raw = values.get("native_deposit_balance")
        deposit_eur_raw = values.get("eur_deposit_balance")
        profit_native_raw = values.get("native_profit_balance")
        if deposit_native_raw is None or deposit_eur_raw is None or profit_native_raw is None:
            raise GenericFundAnalyzerError(
                f"opening state JSON must contain native_deposit_balance, eur_deposit_balance, "
                f"native_profit_balance for currency={currency}: {path}"
            )
        try:
            deposit_native = Decimal(str(deposit_native_raw))
            deposit_eur = Decimal(str(deposit_eur_raw))
            profit_native = Decimal(str(profit_native_raw))
        except Exception as exc:  # noqa: BLE001
            raise GenericFundAnalyzerError(
                f"opening state JSON contains invalid decimals for currency={currency}: {path}"
            ) from exc

        state_by_currency[currency] = FundCurrencyState(
            currency=currency,
            currency_type=currency_type,
            native_deposit_balance=deposit_native,
            eur_deposit_balance=deposit_eur,
            native_profit_balance=profit_native,
        )

    return year_end, state_by_currency


__all__ = [name for name in globals() if not name.startswith("__")]
