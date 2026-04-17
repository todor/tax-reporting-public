from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .constants import DECIMAL_TWO
from .models import AnalysisSummary


def fmt_decimal(value: Decimal, *, quant: Decimal | None = None) -> str:
    if quant is not None:
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(value, "f")


def write_modified_csv(
    path: Path,
    *,
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_declaration_text(*, summary: AnalysisSummary) -> str:
    lines: list[str] = []

    lines.append("!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!")
    if summary.manual_check_required:
        lines.append("СТАТУС: REQUIRED")
        for reason in summary.manual_check_reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("СТАТУС: NOT REQUIRED")
        lines.append("- няма записи, които изискват ръчна проверка")

    lines.append("")

    bucket = summary.appendix_5
    lines.append("Приложение 5")
    lines.append("Таблица 2")
    lines.append(f"- продажна цена (EUR) - код 5082: {fmt_decimal(bucket.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(
        f"- цена на придобиване (EUR) - код 5082: {fmt_decimal(bucket.purchase_price_eur, quant=DECIMAL_TWO)}"
    )
    lines.append(f"- печалба (EUR) - код 5082: {fmt_decimal(bucket.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR) - код 5082: {fmt_decimal(bucket.losses_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- нетен резултат (EUR): {fmt_decimal(bucket.net_result_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {bucket.rows}")
    lines.append("")

    lines.append(f"- обработени редове: {summary.processed_rows}")
    lines.append(f"- игнорирани fiat Deposit/Withdraw: {summary.ignored_fiat_deposit_withdraw_rows}")
    lines.append(f"- предупреждения: {len(summary.warnings)}")
    for warning in summary.warnings:
        lines.append(f"  {warning}")
    if summary.taxable_send_rows > 0:
        lines.append("")
        lines.append("ИНСТРУКЦИЯ ЗА СЛЕДВАЩ АНАЛИЗАТОР")
        lines.append(
            "- За TAXABLE Send събития използвайте modified CSV и по-специално колоната "
            "'Purchase Price (EUR)' като вход за анализатора на другата платформа."
        )

    return "\n".join(lines).rstrip() + "\n"


def write_declaration_text(path: Path, *, summary: AnalysisSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_declaration_text(summary=summary), encoding="utf-8")


__all__ = [name for name in globals() if not name.startswith("__")]
