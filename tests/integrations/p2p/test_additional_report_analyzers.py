from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.p2p.bondora_go_grow import report_analyzer as bondora_analyzer
from integrations.p2p.estateguru import report_analyzer as estateguru_analyzer
from integrations.p2p.iuvo import report_analyzer as iuvo_analyzer
from integrations.p2p.lendermarket import report_analyzer as lendermarket_analyzer
from integrations.p2p.robocash import report_analyzer as robocash_analyzer
from tests.integrations.p2p.support import write_text_pdf


def _pages_for(integration: str) -> list[list[str]]:
    if integration == "estateguru":
        return [[
            "Income Statement",
            "Selected period 01.01.2025 - 31.12.2025",
            "Total € 100.00 € 5.00 € 2.00 € 1.00 € 4.00 € -3.00 € 0.50 € -0.25 € 109.25",
        ]]
    if integration == "lendermarket":
        return [[
            "Tax statement for operations on Lendermarket from 01.01.2025 - 31.12.2025",
            "Payments Received 1200.00 EUR",
            "- Principal Amount 1000.00 EUR",
            "- Interest 190.00 EUR",
            "- Late Payment Fees 10.00 EUR",
            "- Pending Payment interest 0.00 EUR",
            "- Campaign rewards and bonuses 3.50 EUR",
        ]]
    if integration == "iuvo":
        return [
            [
                "Your income for the period 2025-01-01 - 2025-12-31, generated on iuvo marketplace is:",
                "Interest income 70.00 EUR",
                "Late fees 5.00 EUR",
                "Secondary market gains 10.00 EUR",
                "Campaign rewards 2.00 EUR",
                "Interest income iuvoSAVE 30.00 EUR",
            ],
            [
                "Your expenses for the period 2025-01-01 - 2025-12-31 in relation to your investment activity on iuvo are:",
                "Secondary market fees -1.00 EUR",
                "Secondary market losses -3.00 EUR",
                "Early withdraw fees iuvoSAVE 0.00 EUR",
            ],
        ]
    if integration == "robocash":
        return [[
            "Tax report for the year ended 31.12.2025",
            "Earned interest €767.61",
            "Earned income from bonuses €11.00",
            "Taxes withheld €0.00",
        ]]
    if integration == "bondora_go_grow":
        return [[
            "Go & Grow Tax Report – 01/01/2025 - 12/31/2025",
            "Go & Grow",
            "1€",
            "2€",
            "0.50€",
            "3€",
            "4€",
            "5€",
            "Total",
            "Other income",
            "Bonusincome received on Bondora account*",
            "6€",
            "Grand Total",
        ]]
    raise AssertionError(f"unknown integration: {integration}")


CASES = [
    (
        "estateguru",
        estateguru_analyzer.analyze_estateguru_report,
        Decimal("103.00"),
        Decimal("9.00"),
    ),
    (
        "lendermarket",
        lendermarket_analyzer.analyze_lendermarket_report,
        Decimal("200.00"),
        Decimal("3.50"),
    ),
    (
        "iuvo",
        iuvo_analyzer.analyze_iuvo_report,
        Decimal("105.00"),
        Decimal("8.00"),
    ),
    (
        "robocash",
        robocash_analyzer.analyze_robocash_report,
        Decimal("767.61"),
        Decimal("11.00"),
    ),
    (
        "bondora_go_grow",
        bondora_analyzer.analyze_bondora_go_grow_report,
        Decimal("4"),
        Decimal("6"),
    ),
]


@pytest.mark.parametrize("integration,analyze,expected_603,expected_606", CASES)
def test_report_analyzers_end_to_end(
    tmp_path: Path,
    integration: str,
    analyze,
    expected_603: Decimal,
    expected_606: Decimal,
) -> None:
    pdf_path = write_text_pdf(tmp_path / f"{integration}.pdf", pages=_pages_for(integration))

    run_result = analyze(
        input_pdf=pdf_path,
        tax_year=2025,
        output_dir=tmp_path / "out" / integration,
    )

    assert run_result.result.aggregate_code_603 == expected_603
    assert run_result.result.aggregate_code_606 == expected_606
    assert run_result.output_txt_path.exists()

    text = run_result.output_txt_path.read_text(encoding="utf-8")
    assert "Приложение 6" in text
    assert "Част I" in text
    assert "Част II" in text
    assert "Част III" in text


@pytest.mark.parametrize("integration,analyze,_,__", CASES)
def test_report_analyzers_fail_for_appendix_5_mode(
    tmp_path: Path,
    integration: str,
    analyze,
    _: Decimal,
    __: Decimal,
) -> None:
    pdf_path = write_text_pdf(tmp_path / f"{integration}.pdf", pages=_pages_for(integration))

    with pytest.raises(Exception, match="not supported yet"):
        _ = analyze(
            input_pdf=pdf_path,
            tax_year=2025,
            output_dir=tmp_path / "out" / integration,
            secondary_market_mode="appendix_5",
        )
