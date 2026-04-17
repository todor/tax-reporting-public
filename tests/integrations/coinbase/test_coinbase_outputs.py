from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from integrations.coinbase import report_analyzer as analyzer
from tests.integrations.coinbase import support as h


def test_end_to_end_on_coinbase_since_inception_fixture(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "Coinbase Report - since inception.csv"
    input_csv = tmp_path / fixture.name
    shutil.copy(fixture, input_csv)

    result = analyzer.analyze_coinbase_report(
        input_csv=input_csv,
        output_dir=tmp_path / "out",
        eur_unit_rate_provider=h.rate_provider({"EUR": Decimal("1"), "USD": Decimal("0.8")}),
    )

    assert result.output_csv_path.exists()
    assert result.declaration_txt_path.exists()

    out_rows = h.read_csv(result.output_csv_path)
    assert len(out_rows) == 9
    assert "Subtotal (EUR)" in out_rows[0]
    assert "Total (EUR)" in out_rows[0]
    assert "Purchase Price (EUR)" in out_rows[0]
    assert "Sale Price (EUR)" in out_rows[0]

    app5 = result.summary.appendix_5
    assert app5.sale_price_eur == Decimal("3150")
    assert app5.purchase_price_eur == Decimal("3040")
    assert app5.wins_eur == Decimal("110")
    assert app5.losses_eur == Decimal("0")
    assert app5.rows == 3

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "СТАТУС: NOT REQUIRED" in text
    assert "- продажна цена (EUR) - код 5082: 3150.00" in text
    assert "- цена на придобиване (EUR) - код 5082: 3040.00" in text
    assert "- печалба (EUR) - код 5082: 110.00" in text
    assert "- загуба (EUR) - код 5082: 0.00" in text
    assert "- нетен резултат (EUR): 110.00" in text
    assert "- брой сделки: 3" in text
    assert "ИНСТРУКЦИЯ ЗА СЛЕДВАЩ АНАЛИЗАТОР" in text
    assert "Purchase Price (EUR)" in text
    assert "TAXABLE Send" in text


def test_manual_check_summary_is_rendered_when_required(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Send",
                asset="BTC",
                qty="0.1",
                subtotal="€15",
                total="€15",
                review_status="MAYBE",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Learning Reward",
                asset="ETH",
                qty="0.01",
                subtotal="€20",
                total="€20",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!" in text
    assert "СТАТУС: REQUIRED" in text
    assert "неподдържани/неясни записа" in text
    assert "Send запис без валиден Review Status" in text


def test_manual_check_summary_uses_plural_for_multiple_send_invalid_statuses(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Send",
                asset="BTC",
                qty="0.1",
                subtotal="€15",
                total="€15",
                review_status="MAYBE",
            ),
            h.row(
                timestamp="2025-01-03 00:00:00 UTC",
                tx_type="Send",
                asset="BTC",
                qty="0.1",
                subtotal="€15",
                total="€15",
                review_status="UNKNOWN",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert "има 2 Send записа без валиден Review Status" in text


def test_next_analyzer_instruction_is_omitted_without_taxable_send(tmp_path: Path) -> None:
    result = h.run(
        tmp_path,
        rows=[
            h.row(
                timestamp="2025-01-01 00:00:00 UTC",
                tx_type="Buy",
                asset="BTC",
                qty="1",
                subtotal="€100",
                total="€100",
            ),
            h.row(
                timestamp="2025-01-02 00:00:00 UTC",
                tx_type="Sell",
                asset="BTC",
                qty="0.1",
                subtotal="€15",
                total="€15",
            ),
        ],
        rates={"EUR": Decimal("1")},
    )

    text = result.declaration_txt_path.read_text(encoding="utf-8")
    assert result.summary.taxable_send_rows == 0
    assert "ИНСТРУКЦИЯ ЗА СЛЕДВАЩ АНАЛИЗАТОР" not in text


def test_cli_stdout_formats_totals_with_two_decimals(
    monkeypatch,
    capsys,
) -> None:
    summary = analyzer.AnalysisSummary()
    summary.appendix_5.sale_price_eur = Decimal("123.4567")
    summary.appendix_5.purchase_price_eur = Decimal("100")
    summary.appendix_5.wins_eur = Decimal("23.4567")
    summary.appendix_5.losses_eur = Decimal("0")
    fake_result = analyzer.AnalysisResult(
        input_csv_path=Path("/tmp/in.csv"),
        output_csv_path=Path("/tmp/out.csv"),
        declaration_txt_path=Path("/tmp/out.txt"),
        summary=summary,
    )

    monkeypatch.setattr(analyzer, "analyze_coinbase_report", lambda **_kwargs: fake_result)
    monkeypatch.setattr(
        "sys.argv",
        [
            "report_analyzer.py",
            "--input",
            "/tmp/in.csv",
        ],
    )

    exit_code = analyzer.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sale_price_eur: 123.46" in output
    assert "purchase_price_eur: 100.00" in output
    assert "wins_eur: 23.46" in output
    assert "losses_eur: 0.00" in output
    assert "net_result_eur: 23.46" in output
