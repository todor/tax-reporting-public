from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.crypto.kraken import report_analyzer as analyzer
from integrations.crypto.kraken.kraken_parser import load_kraken_csv
from integrations.crypto.kraken.kraken_to_ir import load_and_map_kraken_csv_to_ir
from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from tests.integrations.crypto.kraken import support as h


def test_parser_loads_schema_and_rows(tmp_path: Path) -> None:
    input_csv = tmp_path / "kraken.csv"
    h.write_kraken_csv(
        input_csv,
        rows=[
            h.row(
                txid="t1",
                refid="r1",
                time="2025-01-01 12:30:00",
                tx_type="deposit",
                subtype="",
                aclass="currency",
                subclass="fiat",
                asset="EUR",
                wallet="spot",
                amount="100",
                fee="0",
                balance="100",
            )
        ],
    )

    loaded = load_kraken_csv(input_csv)
    assert loaded.preamble_rows_ignored == 0
    assert loaded.schema.time == "time"
    assert loaded.schema.amount == "amount"
    assert len(loaded.rows) == 1

    timestamp = load_and_map_kraken_csv_to_ir(
        input_csv=str(input_csv),
        summary=IrAnalysisSummary(),
        eur_unit_rate_provider=h.rate_provider({"EUR": Decimal("1"), "USD": Decimal("1")}),
    ).ir_rows[0].timestamp
    assert timestamp.tzinfo == timezone.utc


def test_missing_required_column_fails(tmp_path: Path) -> None:
    input_csv = tmp_path / "kraken.csv"
    header = [col for col in h.DEFAULT_HEADER if col != "amount"]
    h.write_kraken_csv(
        input_csv,
        header=header,
        rows=[
            {
                "txid": "t1",
                "refid": "r1",
                "time": "2025-01-01 12:30:00",
                "type": "deposit",
                "subtype": "",
                "aclass": "currency",
                "subclass": "fiat",
                "asset": "EUR",
                "wallet": "spot",
                "fee": "0",
                "balance": "100",
                "Review Status": "",
                "Cost Basis (EUR)": "",
            }
        ],
    )

    with pytest.raises(analyzer.KrakenAnalyzerError, match="header row was not found|missing required columns"):
        _ = analyzer.analyze_kraken_report(
            input_csv=input_csv,
            tax_year=2025,
            output_dir=tmp_path / "out",
            eur_unit_rate_provider=h.rate_provider({"EUR": Decimal("1"), "USD": Decimal("1")}),
        )


def test_invalid_timestamp_fails(tmp_path: Path) -> None:
    with pytest.raises(analyzer.KrakenAnalyzerError, match="invalid time format"):
        _ = h.run(
            tmp_path,
            rows=[
                h.row(
                    txid="t1",
                    refid="r1",
                    time="2025/01/01 12:30:00",
                    tx_type="deposit",
                    subtype="",
                    aclass="currency",
                    subclass="fiat",
                    asset="EUR",
                    wallet="spot",
                    amount="100",
                )
            ],
            rates={"EUR": Decimal("1"), "USD": Decimal("1")},
        )


def test_invalid_numeric_fails(tmp_path: Path) -> None:
    with pytest.raises(analyzer.KrakenAnalyzerError, match="invalid amount"):
        _ = h.run(
            tmp_path,
            rows=[
                h.row(
                    txid="t1",
                    refid="r1",
                    time="2025-01-01 12:30:00",
                    tx_type="deposit",
                    subtype="",
                    aclass="currency",
                    subclass="fiat",
                    asset="EUR",
                    wallet="spot",
                    amount="bad",
                )
            ],
            rates={"EUR": Decimal("1"), "USD": Decimal("1")},
        )
