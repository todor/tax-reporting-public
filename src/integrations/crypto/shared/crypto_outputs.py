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

from .crypto_ir_models import (
    GenericCryptoAnalyzerError,
    IrAnalysisRunResult,
    IrAnalysisSummary,
    IrEnrichedRow,
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


def _should_render_appendix5(summary: IrAnalysisSummary) -> bool:
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


def _row_to_csv_dict(row: IrEnrichedRow) -> dict[str, str]:
    ir = row.ir_row
    return {
        "Timestamp": ir.timestamp.isoformat(),
        "Operation ID": ir.operation_id,
        "Transaction Type": ir.transaction_type,
        "Asset": ir.asset,
        "Asset Type": ir.asset_type,
        "Quantity": fmt_decimal(ir.quantity),
        "Proceeds (EUR)": _fmt_opt(ir.proceeds_eur),
        "Fee (EUR)": _fmt_opt(ir.fee_eur),
        "Cost Basis (EUR)": _fmt_opt(ir.cost_basis_eur),
        "Review Status": ir.review_status or "",
        "Source Exchange": ir.source_exchange or "",
        "Source Row": "" if ir.source_row_number is None else str(ir.source_row_number),
        "Source Transaction Type": ir.source_transaction_type or "",
        "Operation Leg": ir.operation_leg or "",
        "Purchase Price (EUR)": _fmt_opt(row.purchase_price_eur, quant=DECIMAL_EIGHT),
        "Sale Price (EUR)": _fmt_opt(row.sale_price_eur, quant=DECIMAL_EIGHT),
        "Profit Win (EUR)": _fmt_opt(row.profit_win_eur, quant=DECIMAL_EIGHT),
        "Profit Loss (EUR)": _fmt_opt(row.profit_loss_eur, quant=DECIMAL_EIGHT),
        "Net Profit (EUR)": _fmt_opt(row.net_profit_eur, quant=DECIMAL_EIGHT),
    }


def write_enriched_ir_csv(path: Path, *, rows: list[IrEnrichedRow]) -> None:
    payload = [_row_to_csv_dict(item) for item in rows]
    fieldnames = list(payload[0].keys()) if payload else [
        "Timestamp",
        "Operation ID",
        "Transaction Type",
        "Asset",
        "Asset Type",
        "Quantity",
        "Proceeds (EUR)",
        "Fee (EUR)",
        "Cost Basis (EUR)",
        "Review Status",
        "Source Exchange",
        "Source Row",
        "Source Transaction Type",
        "Operation Leg",
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


def build_declaration_text(*, summary: IrAnalysisSummary) -> str:
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
                    code="5082",
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
    technical_lines.append(
        f"- manual check overrides (Review Status non-empty): {summary.manual_check_overrides_rows}"
    )
    technical_lines.append(f"- ignored fiat Deposit/Withdraw rows: {summary.ignored_fiat_deposit_withdraw_rows}")

    if technical_lines:
        lines.append(TECHNICAL_DETAILS_SEPARATOR)
        lines.append("")
        lines.extend(technical_lines)
    return "\n".join(lines).rstrip() + "\n"


def write_declaration_text(path: Path, *, summary: IrAnalysisSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_declaration_text(summary=summary), encoding="utf-8")


def build_ir_run_cli_summary_lines(
    *,
    result: IrAnalysisRunResult,
) -> list[str]:
    return [
        f"STATUS: {'MANUAL CHECK REQUIRED' if result.summary.manual_check_required else 'SUCCESS'}",
        f"Enriched IR CSV: {result.output_csv_path}",
        f"Declaration TXT: {result.declaration_txt_path}",
        f"Year-end state JSON: {result.year_end_state_json_path}",
    ]


def write_holdings_state_json(
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
                "average_price_eur": (
                    format((abs(total_cost_eur) / abs(quantity)) if quantity != ZERO else ZERO, "f")
                ),
            }
            for asset, (quantity, total_cost_eur) in sorted(holdings_by_asset.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_holdings_state_json(path: Path) -> tuple[int | None, dict[str, tuple[Decimal, Decimal]]]:
    if not path.exists():
        raise GenericCryptoAnalyzerError(f"opening state JSON does not exist: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GenericCryptoAnalyzerError(f"invalid opening state JSON: {path}") from exc

    year_end_raw = payload.get("state_tax_year_end")
    year_end = int(year_end_raw) if year_end_raw is not None else None

    holdings_payload = payload.get("holdings_by_asset")
    if not isinstance(holdings_payload, dict):
        raise GenericCryptoAnalyzerError(
            f"opening state JSON must contain object 'holdings_by_asset': {path}"
        )

    holdings: dict[str, tuple[Decimal, Decimal]] = {}
    for asset_raw, values in holdings_payload.items():
        asset = str(asset_raw).strip().upper()
        if asset == "":
            raise GenericCryptoAnalyzerError(f"opening state JSON contains empty asset key: {path}")
        if not isinstance(values, dict):
            raise GenericCryptoAnalyzerError(
                f"opening state JSON asset entry must be an object for asset={asset}: {path}"
            )
        quantity_raw = values.get("quantity")
        total_cost_raw = values.get("total_cost_eur")
        if quantity_raw is None or total_cost_raw is None:
            raise GenericCryptoAnalyzerError(
                f"opening state JSON asset entry must contain quantity and total_cost_eur for asset={asset}: {path}"
            )
        try:
            quantity = Decimal(str(quantity_raw))
            total_cost_eur = Decimal(str(total_cost_raw))
        except Exception as exc:  # noqa: BLE001
            raise GenericCryptoAnalyzerError(
                f"opening state JSON contains invalid decimals for asset={asset}: {path}"
            ) from exc
        holdings[asset] = (quantity, total_cost_eur)

    return year_end, holdings


__all__ = [name for name in globals() if not name.startswith("__")]
