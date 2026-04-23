from __future__ import annotations

from decimal import Decimal

from integrations.p2p.shared.appendix6_models import Appendix6Part1Row, InformativeRow, P2PAppendix6Result
from integrations.p2p.shared.appendix6_renderer import build_appendix6_text


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
    assert "Одитни данни" in text
    assert "- Secondary-market mode used: appendix_6" in text


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
    assert "- mismatch in required report field" in text
    assert "Бележки по обработката" in text
    assert "- secondary market <= 0, omitted from code 606" in text
