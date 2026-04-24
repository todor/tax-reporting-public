from __future__ import annotations

from decimal import Decimal

from integrations.p2p.shared.appendix6_models import Appendix6Part1Row, InformativeRow, P2PAppendix6Result
from integrations.p2p.shared.appendix6_renderer import build_appendix6_text

TECHNICAL_DETAILS_SEPARATOR = "------------------------------ Technical Details ------------------------------"


def test_appendix6_renderer_renders_expected_sections() -> None:
    result = P2PAppendix6Result(
        platform="afranga",
        tax_year=2025,
        part1_rows=[
            Appendix6Part1Row(
                payer_name="Стик Кредит АД",
                payer_eik="202557159",
                code="603",
                amount=Decimal("126.66"),
            )
        ],
        aggregate_code_603=Decimal("23.76"),
        aggregate_code_606=Decimal("210.11"),
        taxable_code_603=Decimal("150.42"),
        taxable_code_606=Decimal("210.11"),
        withheld_tax=Decimal("12.67"),
        informative_rows=[
            InformativeRow("Secondary-market mode used", "appendix_6"),
            InformativeRow("Net Sum from Appendix (EUR)", Decimal("58.28")),
        ],
    )

    text = build_appendix6_text(result=result)

    assert "Приложение 6" in text
    assert "Част I" in text
    assert "- Ред 1.1" in text
    assert "  ЕИК: 202557159" in text
    assert "  Наименование: Стик Кредит АД" in text
    assert "- Обща сума на доходите с код 603: 23.76" in text
    assert "- Облагаем доход по чл. 35, код 603: 150.42" in text
    assert "- Удържан и/или внесен окончателен данък за доходи: 12.67" in text
    assert "Информативни" in text
    assert "- Използван режим за вторичен пазар: appendix_6" in text
    assert TECHNICAL_DETAILS_SEPARATOR in text
    assert "Audit Data" in text
    assert "- platform: afranga" in text


def test_appendix6_renderer_separates_manual_check_and_informational_messages() -> None:
    result = P2PAppendix6Result(
        platform="test",
        tax_year=2025,
        part1_rows=[],
        aggregate_code_603=Decimal("1"),
        aggregate_code_606=Decimal("2"),
        taxable_code_603=Decimal("1"),
        taxable_code_606=Decimal("2"),
        withheld_tax=Decimal("0"),
        warnings=["mismatch in required report field"],
        informational_messages=["secondary market <= 0, omitted from code 606"],
    )

    text = build_appendix6_text(result=result)
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text
    assert "- Има причина за ръчна проверка (1); вижте \"Technical Details\" -> \"Processing Notes\"." in text
    assert "Бележки по обработката" in text
    assert "- Налична е допълнителна обработваща бележка (1); вижте \"Technical Details\" -> \"Processing Notes\"." in text
    assert TECHNICAL_DETAILS_SEPARATOR in text
    assert "Processing Notes" in text
    assert "- [UNTRANSLATED] mismatch in required report field" in text
    assert "- [UNTRANSLATED] secondary market <= 0, omitted from code 606" in text


def test_appendix6_renderer_suppresses_zero_only_tax_sections_and_informative_block() -> None:
    result = P2PAppendix6Result(
        platform="test",
        tax_year=2025,
        part1_rows=[],
        aggregate_code_603=Decimal("0"),
        aggregate_code_606=Decimal("0"),
        taxable_code_603=Decimal("0"),
        taxable_code_606=Decimal("0"),
        withheld_tax=Decimal("0"),
        informative_rows=[
            InformativeRow("Net Sum from Appendix (EUR)", Decimal("0")),
            InformativeRow("Secondary-market mode used", ""),
        ],
        warnings=["unmapped parser detail"],
    )

    text = build_appendix6_text(result=result)
    assert "Приложение 6" not in text
    assert "Част I" not in text
    assert "Част II" not in text
    assert "Част III" not in text
    assert "Информативни" not in text
    assert "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!" in text
    assert TECHNICAL_DETAILS_SEPARATOR in text
