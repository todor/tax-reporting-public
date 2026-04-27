from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.fund.finexify import report_analyzer as analyzer
from tests.integrations.fund.finexify import support as h

TECHNICAL_DETAILS_SEPARATOR = "------------------------------ Technical Details ------------------------------"


def test_opening_state_carry_forward(tmp_path: Path) -> None:
    result_2025 = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="120", date="2025-12-01"),
            h.row(tx_type="Withdraw", currency="USDC", amount="60", date="2025-12-15"),
        ],
        tax_year=2025,
        rates={"USDC": Decimal("1")},
        file_name="finexify_2025.csv",
    )

    result_2026 = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Balance", currency="USDC", amount="70", date="2026-01-10"),
            h.row(tx_type="Withdraw", currency="USDC", amount="35", date="2026-02-01"),
        ],
        tax_year=2026,
        opening_state_json=result_2025.year_end_state_json_path,
        rates={"USDC": Decimal("1")},
        file_name="finexify_2026.csv",
    )

    app5 = result_2026.summary.appendix_5
    assert app5.rows == 1
    assert app5.sale_price_eur == Decimal("35")
    assert app5.purchase_price_eur == Decimal("25")
    assert app5.wins_eur == Decimal("10")


def test_eur_conversion_timing_deposit_vs_withdraw(tmp_path: Path) -> None:
    class TimedProvider:
        def __call__(self, currency: str, _currency_type: str, timestamp):
            if currency.upper() != "USDC":
                raise AssertionError("unexpected currency")
            if timestamp.year == 2025 and timestamp.month == 1:
                return Decimal("0.8")
            return Decimal("0.9")

    rows = [
        h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
        h.row(tx_type="Balance", currency="USDC", amount="100", date="2025-02-01"),
        h.row(tx_type="Withdraw", currency="USDC", amount="100", date="2025-03-01"),
    ]

    input_csv = tmp_path / "finexify.csv"
    h.write_finexify_csv(input_csv, rows=rows)

    from integrations.fund.finexify import report_analyzer as analyzer

    result = analyzer.analyze_finexify_report(
        input_csv=input_csv,
        tax_year=2025,
        output_dir=tmp_path / "out",
        eur_unit_rate_provider=TimedProvider(),
    )

    app5 = result.summary.appendix_5
    assert app5.purchase_price_eur == Decimal("80")
    assert app5.sale_price_eur == Decimal("90")
    assert app5.wins_eur == Decimal("10")


def test_declaration_txt_contains_summary_and_warnings(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Unsupported", currency="", amount="", date=""),
        ],
        rates={"USDC": Decimal("1")},
    )

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "Приложение 5" not in text
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text
    assert "неподдържани/неясни записа" in text
    assert TECHNICAL_DETAILS_SEPARATOR in text
    assert "Audit Data" in text


def test_year_end_state_json_keeps_native_and_eur_balances(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="130", date="2025-02-01"),
            h.row(tx_type="Withdraw", currency="USDC", amount="65", date="2025-03-01"),
        ],
        rates={"USDC": Decimal("1")},
    )

    payload = json.loads(result.year_end_state_json_path.read_text(encoding="utf-8"))
    usdc = payload["state_by_currency"]["USDC"]
    assert Decimal(usdc["native_deposit_balance"]) == Decimal("50")
    assert Decimal(usdc["native_profit_balance"]) == Decimal("15")
    assert Decimal(usdc["eur_deposit_balance"]) == Decimal("50")


def test_opening_state_year_validation_rules(tmp_path: Path) -> None:
    rows = [h.row(tx_type="Deposit", currency="USDC", amount="100", date="2025-01-01")]

    valid_state = tmp_path / "state_valid_2024.json"
    valid_state.write_text(
        json.dumps({"state_tax_year_end": 2024, "state_by_currency": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    _ = h.run(
        tmp_path,
        tax_year=2025,
        rows=rows,
        opening_state_json=valid_state,
        rates={"USDC": Decimal("1")},
        file_name="valid.csv",
    )

    older_valid_state = tmp_path / "state_valid_2022.json"
    older_valid_state.write_text(
        json.dumps({"state_tax_year_end": 2022, "state_by_currency": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    _ = h.run(
        tmp_path,
        tax_year=2025,
        rows=rows,
        opening_state_json=older_valid_state,
        rates={"USDC": Decimal("1")},
        file_name="older_valid.csv",
    )

    same_year_state = tmp_path / "state_invalid_2025.json"
    same_year_state.write_text(
        json.dumps({"state_tax_year_end": 2025, "state_by_currency": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(analyzer.FinexifyAnalyzerError, match="must be strictly less than tax_year"):
        _ = h.run(
            tmp_path,
            tax_year=2025,
            rows=rows,
            opening_state_json=same_year_state,
            rates={"USDC": Decimal("1")},
            file_name="same_year.csv",
        )

    future_state = tmp_path / "state_invalid_2026.json"
    future_state.write_text(
        json.dumps({"state_tax_year_end": 2026, "state_by_currency": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(analyzer.FinexifyAnalyzerError, match="must be strictly less than tax_year"):
        _ = h.run(
            tmp_path,
            tax_year=2025,
            rows=rows,
            opening_state_json=future_state,
            rates={"USDC": Decimal("1")},
            file_name="future.csv",
        )

    missing_year_state = tmp_path / "state_missing_year.json"
    missing_year_state.write_text(
        json.dumps({"state_by_currency": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(analyzer.FinexifyAnalyzerError, match="missing state_tax_year_end"):
        _ = h.run(
            tmp_path,
            tax_year=2025,
            rows=rows,
            opening_state_json=missing_year_state,
            rates={"USDC": Decimal("1")},
            file_name="missing_year.csv",
        )

    invalid_year_state = tmp_path / "state_invalid_year_type.json"
    invalid_year_state.write_text(
        json.dumps({"state_tax_year_end": "abc", "state_by_currency": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(analyzer.FinexifyAnalyzerError, match="invalid state_tax_year_end"):
        _ = h.run(
            tmp_path,
            tax_year=2025,
            rows=rows,
            opening_state_json=invalid_year_state,
            rates={"USDC": Decimal("1")},
            file_name="invalid_year.csv",
        )


def test_opening_state_filters_pre_state_and_future_rows(tmp_path: Path) -> None:
    opening_state = tmp_path / "state_2022.json"
    opening_state.write_text(
        json.dumps(
            {
                "state_tax_year_end": 2022,
                "state_by_currency": {
                    "USDC": {
                        "currency_type": "crypto",
                        "native_deposit_balance": "100",
                        "eur_deposit_balance": "100",
                        "native_profit_balance": "0",
                        "native_total_balance": "100",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = h.run(
        tmp_path,
        tax_year=2025,
        opening_state_json=opening_state,
        rows=[
            h.row(tx_type="Deposit", currency="USDC", amount="50", date="2021-01-01"),
            h.row(tx_type="Deposit", currency="USDC", amount="10", date="2022-01-01"),
            h.row(tx_type="Deposit", currency="USDC", amount="20", date="2023-01-01"),
            h.row(tx_type="Balance", currency="USDC", amount="150", date="2024-01-01"),
            h.row(tx_type="Withdraw", currency="USDC", amount="75", date="2025-01-01"),
            h.row(tx_type="Deposit", currency="USDC", amount="5", date="2026-01-01"),
        ],
        rates={"USDC": Decimal("1")},
        file_name="since_inception.csv",
    )

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("75")
    assert app5.purchase_price_eur == Decimal("60")
    assert app5.wins_eur == Decimal("15")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 1

    state = result.summary.state_by_currency["USDC"]
    assert state.native_deposit_balance == Decimal("60")
    assert state.eur_deposit_balance == Decimal("60")
    assert state.native_profit_balance == Decimal("15")

    assert result.summary.rows_ignored_before_or_equal_opening_state_year == 2
    assert result.summary.rows_ignored_after_tax_year == 1
    assert result.summary.rows_applied_to_ledger == 3
    assert result.summary.rows_included_in_tax_year == 1
