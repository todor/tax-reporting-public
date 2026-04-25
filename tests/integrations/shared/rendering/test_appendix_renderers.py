from __future__ import annotations

from decimal import Decimal

from integrations.shared.rendering.appendix13 import Appendix13Part2Entry, render_appendix13_part2
from integrations.shared.rendering.appendix5 import Appendix5Table2Entry, render_appendix5_table2
from integrations.shared.rendering.appendix6 import (
    Appendix6Part1CodeTotal,
    Appendix6Part1CompanyRow,
    Appendix6Part2TaxableTotal,
    Appendix6RenderData,
    render_appendix6,
)
from integrations.shared.rendering.appendix8 import (
    Appendix8Part1Row,
    Appendix8Part3Row,
    Appendix8RenderData,
    render_appendix8,
)
from integrations.shared.rendering.appendix9 import Appendix9Part2Row, render_appendix9_part2
from integrations.shared.rendering.common import Money, format_money


def test_format_money_values() -> None:
    assert format_money(Money(Decimal("123.456"), "EUR")) == "123.46 EUR"
    assert format_money(Money(Decimal("-12.3"), "EUR")) == "-12.30 EUR"
    assert format_money(Money(Decimal("0"), "EUR")) == "0.00 EUR"
    assert format_money(Money(Decimal("15.5"), "USD")) == "15.50 USD"


def test_render_appendix5_table2_suppresses_zero_only_entries() -> None:
    lines = render_appendix5_table2(
        [
            Appendix5Table2Entry(
                code="5082",
                sale_value=Money(Decimal("0"), "EUR"),
                acquisition_value=Money(Decimal("0"), "EUR"),
                profit=Money(Decimal("0"), "EUR"),
                loss=Money(Decimal("0"), "EUR"),
                net_result=Money(Decimal("0"), "EUR"),
                trade_count=0,
            )
        ]
    )
    assert lines == []


def test_render_appendix5_table2_renders_multi_code_consistently() -> None:
    lines = render_appendix5_table2(
        [
            Appendix5Table2Entry(
                code="508",
                sale_value=Money(Decimal("100"), "EUR"),
                acquisition_value=Money(Decimal("90"), "EUR"),
                profit=Money(Decimal("10"), "EUR"),
                loss=Money(Decimal("0"), "EUR"),
                net_result=Money(Decimal("10"), "EUR"),
                trade_count=2,
            ),
            Appendix5Table2Entry(
                code="5082",
                sale_value=Money(Decimal("0"), "EUR"),
                acquisition_value=Money(Decimal("0"), "EUR"),
                profit=Money(Decimal("0"), "EUR"),
                loss=Money(Decimal("0"), "EUR"),
                net_result=Money(Decimal("0"), "EUR"),
                trade_count=0,
            ),
        ]
    )
    text = "\n".join(lines)
    assert "Приложение 5" in text
    assert "Таблица 2" in text
    assert "- Код 508" in text
    assert "  Продажна цена: 100.00 EUR" in text
    assert "  Цена на придобиване: 90.00 EUR" in text
    assert "  Печалба: 10.00 EUR" in text
    assert "  Загуба: 0.00 EUR" in text
    assert "код 508:" not in text
    assert "(EUR)" not in text
    assert "  Информативни" in text
    assert "код 5082" not in text


def test_render_appendix6_renders_parts_and_suppresses_empty() -> None:
    lines = render_appendix6(
        Appendix6RenderData(
            part1_company_rows=[
                Appendix6Part1CompanyRow(
                    payer_name="Платец",
                    payer_eik="123456789",
                    code="603",
                    amount=Money(Decimal("12.34"), "EUR"),
                )
            ],
            part1_code_totals=[
                Appendix6Part1CodeTotal(code="603", amount=Money(Decimal("12.34"), "EUR")),
                Appendix6Part1CodeTotal(code="606", amount=Money(Decimal("0"), "EUR")),
            ],
            part2_taxable_totals=[
                Appendix6Part2TaxableTotal(code="603", amount=Money(Decimal("12.34"), "EUR")),
                Appendix6Part2TaxableTotal(code="606", amount=Money(Decimal("0"), "EUR")),
            ],
            part3_withheld_tax=Money(Decimal("0"), "EUR"),
        )
    )
    text = "\n".join(lines)
    assert "Приложение 6" in text
    assert "Част I" in text
    assert "Част II" in text
    assert "Част III" not in text
    assert "- Обща сума на доходите с код 603: 12.34 EUR" in text
    assert "  Размер на дохода: 12.34 EUR" in text
    assert "код 606" not in text


def test_render_appendix8_uses_currency_suffix_for_native_and_eur_values() -> None:
    lines = render_appendix8(
        Appendix8RenderData(
            part1_rows=[
                Appendix8Part1Row(
                    asset_type="Акции",
                    country="Германия",
                    quantity="10.1234",
                    acquisition_date="31.12.2025",
                    acquisition_native=Money(Decimal("1000"), "USD"),
                    acquisition_eur=Money(Decimal("920"), "EUR"),
                    native_currency_label="USD",
                )
            ],
            part3_rows=[
                Appendix8Part3Row(
                    payer="COMPANY",
                    country="Ирландия",
                    code="8141",
                    treaty_method="3",
                    gross_income=Money(Decimal("5"), "EUR"),
                    foreign_tax=Money(Decimal("0"), "EUR"),
                    allowable_credit=Money(Decimal("0"), "EUR"),
                    recognized_credit=Money(Decimal("0"), "EUR"),
                    tax_due=Money(Decimal("0.25"), "EUR"),
                )
            ],
        )
    )
    text = "\n".join(lines)
    assert "Приложение 8" in text
    assert "Част І, Акции" in text
    assert "Обща цена на придобиване в съответната валута: 1000.00 USD" in text
    assert "В EUR: 920.00 EUR" in text
    assert "Част III," in text
    assert "Код вид доход: 8141" in text
    assert "Документално доказана цена на придобиване: " in text
    assert "Брутен размер на дохода: 5.00 EUR" in text
    assert "Дължим данък, подлежащ на внасяне: 0.25 EUR" in text


def test_render_appendix9_part2_basic() -> None:
    lines = render_appendix9_part2(
        [
            Appendix9Part2Row(
                country="Ирландия",
                code="603",
                gross_income=Money(Decimal("10.20"), "EUR"),
                tax_base=Money(Decimal("10.20"), "EUR"),
                foreign_tax=Money(Decimal("2.05"), "EUR"),
                allowable_credit=Money(Decimal("1.02"), "EUR"),
                recognized_credit=Money(Decimal("1.02"), "EUR"),
                document_ref="R-185 / Activity Statement",
            )
        ]
    )
    text = "\n".join(lines)
    assert "Приложение 9" in text
    assert "Част II" in text
    assert "Брутен размер на дохода (включително платеният данък): 10.20 EUR" in text
    assert "Нормативно определени разходи: 0.00 EUR" in text
    assert "Размер на признатия данъчен кредит: 1.02 EUR" in text
    assert "№ и дата на документа за дохода и съответния данък: R-185 / Activity Statement" in text


def test_render_appendix13_part2_basic() -> None:
    lines = render_appendix13_part2(
        [
            Appendix13Part2Entry(
                code="5081",
                gross_income=Money(Decimal("9319.39"), "EUR"),
                acquisition_value=Money(Decimal("9759.21"), "EUR"),
                profit=Money(Decimal("384.86"), "EUR"),
                loss=Money(Decimal("824.68"), "EUR"),
                net_result=Money(Decimal("-439.82"), "EUR"),
                trade_count=3,
            )
        ]
    )
    text = "\n".join(lines)
    assert "Приложение 13" in text
    assert "Част ІІ" in text
    assert "- Код 5081" in text
    assert "Брутен размер на дохода: 9319.39 EUR" in text
    assert "Цена на придобиване: 9759.21 EUR" in text
    assert "- печалба: 384.86 EUR" in text
    assert "- нетен резултат: -439.82 EUR" in text
    assert "(EUR)" not in text
