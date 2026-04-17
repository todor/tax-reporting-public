from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from integrations.coinbase import report_analyzer as analyzer

DEFAULT_HEADER = [
    "Timestamp",
    "Transaction Type",
    "Asset",
    "Quantity Transacted",
    "Price Currency",
    "Price at Transaction",
    "Subtotal",
    "Total",
    "Fees and/or Spread",
    "Notes",
    "Review Status",
    "Purchase Price",
]


def rate_provider(rates: dict[str, Decimal]) -> Callable[[str, datetime], Decimal]:
    normalized = {key.upper(): value for key, value in rates.items()}

    def provider(currency: str, _timestamp: datetime) -> Decimal:
        key = currency.strip().upper()
        if key not in normalized:
            raise AssertionError(f"Missing test EUR rate for currency={key}")
        return normalized[key]

    return provider


def row(
    *,
    timestamp: str,
    tx_type: str,
    asset: str,
    qty: str,
    price_currency: str = "EUR",
    price_at_transaction: str = "",
    subtotal: str = "",
    total: str = "",
    fees: str = "",
    notes: str = "",
    review_status: str = "",
    purchase_price: str = "",
) -> dict[str, str]:
    return {
        "Timestamp": timestamp,
        "Transaction Type": tx_type,
        "Asset": asset,
        "Quantity Transacted": qty,
        "Price Currency": price_currency,
        "Price at Transaction": price_at_transaction,
        "Subtotal": subtotal,
        "Total": total,
        "Fees and/or Spread": fees,
        "Notes": notes,
        "Review Status": review_status,
        "Purchase Price": purchase_price,
    }


def write_coinbase_csv(
    path: Path,
    *,
    rows: list[dict[str, str]],
    header: list[str] | None = None,
    preamble_lines: list[str] | None = None,
) -> None:
    actual_header = header if header is not None else DEFAULT_HEADER
    with path.open("w", encoding="utf-8", newline="") as handle:
        if preamble_lines:
            for line in preamble_lines:
                handle.write(line.rstrip("\n") + "\n")
        writer = csv.DictWriter(handle, fieldnames=actual_header)
        writer.writeheader()
        for item in rows:
            writer.writerow(item)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def run(
    tmp_path: Path,
    *,
    rows: list[dict[str, str]],
    tax_year: int = 2025,
    opening_state_json: Path | None = None,
    rates: dict[str, Decimal] | None = None,
    preamble_lines: list[str] | None = None,
    header: list[str] | None = None,
    file_name: str = "input.csv",
) -> analyzer.AnalysisResult:
    input_csv = tmp_path / file_name
    write_coinbase_csv(
        input_csv,
        rows=rows,
        header=header,
        preamble_lines=preamble_lines,
    )

    effective_rates = rates if rates is not None else {"EUR": Decimal("1")}
    provider = rate_provider(effective_rates)
    return analyzer.analyze_coinbase_report(
        input_csv=input_csv,
        tax_year=tax_year,
        opening_state_json=opening_state_json,
        output_dir=tmp_path / "out",
        eur_unit_rate_provider=provider,
    )
