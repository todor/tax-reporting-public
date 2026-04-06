from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.binance import futures_pnl_analyzer as analyzer

HEADER = ["User ID", "Time", "Account", "Operation", "Coin", "Change", "Remark"]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _base_row(
    *,
    time: str,
    operation: str,
    coin: str = "BNFCR",
    change: str = "0",
    user_id: str = "u1",
    account: str = "USD-M",
    remark: str = "",
) -> dict[str, str]:
    return {
        "User ID": user_id,
        "Time": time,
        "Account": account,
        "Operation": operation,
        "Coin": coin,
        "Change": change,
        "Remark": remark,
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fx_provider(ts: datetime) -> Decimal:
    assert ts.tzinfo is not None
    return Decimal("0.5")


def test_positive_change_contributes_to_profit(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [_base_row(time="2025-01-01 00:00:00", operation="Realized Profit and Loss", change="12.5")],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )

    summary = _read_json(result.summary_json_path)
    assert summary["profit_usd"] == "12.5"
    assert summary["loss_usd"] == "0"
    assert summary["profit_eur"] == "6.25"
    assert summary["loss_eur"] == "0.00"


def test_negative_change_contributes_to_loss(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [_base_row(time="2025-01-01 00:00:00", operation="Funding Fee", change="-3.2")],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )

    summary = _read_json(result.summary_json_path)
    assert summary["profit_usd"] == "0"
    assert summary["loss_usd"] == "3.2"
    assert summary["profit_eur"] == "0.00"
    assert summary["loss_eur"] == "1.60"


def test_mixed_rows_aggregate_correctly(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [
            _base_row(time="2025-01-01 00:00:00", operation="Realized Profit and Loss", change="10"),
            _base_row(time="2025-01-01 01:00:00", operation="Fee", change="-2"),
            _base_row(time="2025-01-01 02:00:00", operation="Funding Fee", change="-1"),
        ],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )

    summary = _read_json(result.summary_json_path)
    assert summary["profit_usd"] == "10"
    assert summary["loss_usd"] == "3"
    assert summary["net_result_usd"] == "7"
    assert summary["profit_eur"] == "5.00"
    assert summary["loss_eur"] == "1.50"
    assert summary["net_result_eur"] == "3.50"


def test_fee_and_funding_rows_use_sign_only(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [
            _base_row(time="2025-01-02 00:00:00", operation="Fee", change="4"),
            _base_row(time="2025-01-02 01:00:00", operation="Funding Fee", change="-1"),
        ],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )
    summary = _read_json(result.summary_json_path)
    assert summary["profit_usd"] == "4"
    assert summary["loss_usd"] == "1"


def test_zero_change_row_does_not_affect_totals(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [
            _base_row(time="2025-01-01 00:00:00", operation="Realized Profit and Loss", change="0"),
            _base_row(time="2025-01-01 01:00:00", operation="Realized Profit and Loss", change="5"),
        ],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )

    summary = _read_json(result.summary_json_path)
    assert summary["profit_usd"] == "5"
    assert summary["loss_usd"] == "0"
    detailed_rows = _read_csv(result.detailed_csv_path)
    assert len(detailed_rows) == 2
    assert detailed_rows[0]["profit_usd"] == "0"
    assert detailed_rows[0]["loss_usd"] == "0"


def test_invalid_coin_raises_with_row_number(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [_base_row(time="2025-01-01 00:00:00", operation="Fee", coin="USDT", change="-1")],
    )

    with pytest.raises(analyzer.UnexpectedCurrencyError, match="row 1: unexpected currency"):
        _ = analyzer.analyze_futures_pnl_report(
            input_csv=input_csv,
            tax_year=2025,
            output_dir=tmp_path / "out",
            eur_rate_provider=_fx_provider,
        )


def test_invalid_change_raises_with_row_number(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [_base_row(time="2025-01-01 00:00:00", operation="Fee", change="nope")],
    )

    with pytest.raises(analyzer.FuturesPnlAnalyzerError, match="row 1: invalid Change"):
        _ = analyzer.analyze_futures_pnl_report(
            input_csv=input_csv,
            tax_year=2025,
            output_dir=tmp_path / "out",
            eur_rate_provider=_fx_provider,
        )


def test_fx_applied_per_row(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [
            _base_row(time="2025-01-01 00:00:00", operation="Fee", change="2"),
            _base_row(time="2025-01-02 00:00:00", operation="Fee", change="2"),
        ],
    )

    def dynamic_fx(ts: datetime) -> Decimal:
        day = ts.day
        return Decimal("0.5") if day == 1 else Decimal("0.75")

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=dynamic_fx,
    )

    detailed = _read_csv(result.detailed_csv_path)
    assert detailed[0]["amount_eur"] == "1.00000000"
    assert detailed[1]["amount_eur"] == "1.50000000"
    summary = _read_json(result.summary_json_path)
    assert summary["profit_eur"] == "2.50"


def test_order_is_preserved_in_detailed_output(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [
            _base_row(time="2025-01-02 00:00:00", operation="Fee", change="-1", remark="second"),
            _base_row(time="2025-01-01 00:00:00", operation="Fee", change="2", remark="first"),
            _base_row(time="2025-01-03 00:00:00", operation="Fee", change="-3", remark="third"),
        ],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )
    detailed = _read_csv(result.detailed_csv_path)
    assert [row["remark"] for row in detailed] == ["second", "first", "third"]
    assert [row["original_row_number"] for row in detailed] == ["1", "2", "3"]


def test_missing_required_columns_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    with bad.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["User ID", "Time", "Account", "Operation", "Coin", "Remark"])
        writer.writerow(["1", "2025-01-01 00:00:00", "USD-M", "Fee", "BNFCR", "x"])

    with pytest.raises(analyzer.CsvValidationError, match="missing required columns"):
        _ = analyzer.analyze_futures_pnl_report(
            input_csv=bad,
            tax_year=2025,
            output_dir=tmp_path / "out",
            eur_rate_provider=_fx_provider,
        )


def test_empty_dataset_produces_zero_outputs(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [_base_row(time="2025-01-01 00:00:00", operation="Transfer", change="100")],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=_fx_provider,
    )
    summary = _read_json(result.summary_json_path)
    assert summary["processed_rows"] == 0
    assert summary["ignored_rows"] == 1
    assert summary["profit_eur"] == "0.00"
    detailed = _read_csv(result.detailed_csv_path)
    assert detailed == []


def test_cli_prints_metrics_and_output_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [_base_row(time="2025-01-01 00:00:00", operation="Fee", change="1")],
    )

    monkeypatch.setattr(
        analyzer,
        "_default_eur_rate_provider",
        lambda _cache_dir: (lambda _ts: Decimal("1")),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "futures_pnl_analyzer.py",
            "--input",
            str(input_csv),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    exit_code = analyzer.main()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "processed_rows: 1" in output
    assert "Detailed CSV:" in output
    assert "Tax text file:" in output
    assert "Summary file:" in output


def test_time_without_timezone_is_treated_as_utc() -> None:
    parsed = analyzer._parse_time("2025-01-01 10:00:00", row_number=1)  # noqa: SLF001
    assert parsed.tzinfo == timezone.utc


def test_two_digit_year_time_format_is_supported() -> None:
    parsed = analyzer._parse_time("25-04-16 02:02:41", row_number=9)  # noqa: SLF001
    assert parsed == datetime(2025, 4, 16, 2, 2, 41, tzinfo=timezone.utc)


def test_tax_text_groups_eur_usd_and_processing_sections(tmp_path: Path) -> None:
    input_csv = tmp_path / "input.csv"
    _write_csv(
        input_csv,
        [
            _base_row(time="2025-01-01 00:00:00", operation="Fee", change="2"),
            _base_row(time="2025-01-01 01:00:00", operation="Fee", change="-1"),
        ],
    )

    result = analyzer.analyze_futures_pnl_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_rate_provider=lambda _ts: Decimal("1"),
    )

    text = result.tax_text_path.read_text(encoding="utf-8")
    assert "нетна печалба (EUR): 1.00" in text
    assert "\n\nprofit_usd:" in text
    assert "\n\nprocessed_rows: 2" in text
