from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from integrations.crypto.kraken import report_analyzer as analyzer

DEFAULT_HEADER = [
    "txid",
    "refid",
    "time",
    "type",
    "subtype",
    "aclass",
    "subclass",
    "asset",
    "wallet",
    "amount",
    "fee",
    "balance",
    "Review Status",
    "Cost Basis (EUR)",
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
    txid: str,
    refid: str,
    time: str,
    tx_type: str,
    subtype: str,
    aclass: str,
    subclass: str,
    asset: str,
    wallet: str,
    amount: str,
    fee: str = "0",
    balance: str = "0",
    review_status: str = "",
    cost_basis_eur: str = "",
) -> dict[str, str]:
    return {
        "txid": txid,
        "refid": refid,
        "time": time,
        "type": tx_type,
        "subtype": subtype,
        "aclass": aclass,
        "subclass": subclass,
        "asset": asset,
        "wallet": wallet,
        "amount": amount,
        "fee": fee,
        "balance": balance,
        "Review Status": review_status,
        "Cost Basis (EUR)": cost_basis_eur,
    }


def write_kraken_csv(
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
    file_name: str = "kraken.csv",
) -> analyzer.AnalysisResult:
    input_csv = tmp_path / file_name
    write_kraken_csv(
        input_csv,
        rows=rows,
        header=header,
        preamble_lines=preamble_lines,
    )

    effective_rates = rates if rates is not None else {"EUR": Decimal("1"), "USD": Decimal("1")}
    provider = rate_provider(effective_rates)
    return analyzer.analyze_kraken_report(
        input_csv=input_csv,
        tax_year=tax_year,
        opening_state_json=opening_state_json,
        output_dir=tmp_path / "out",
        eur_unit_rate_provider=provider,
    )

