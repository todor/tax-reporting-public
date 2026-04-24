from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.fund.shared.fund_ir_models import FundAnalysisSummary, GenericFundAnalyzerError
from integrations.fund.shared.fund_outputs import build_declaration_text


def test_build_declaration_text_uses_passed_appendix_code() -> None:
    summary = FundAnalysisSummary()
    summary.appendix_5.sale_price_eur = Decimal("10")
    summary.appendix_5.purchase_price_eur = Decimal("7")
    summary.appendix_5.wins_eur = Decimal("3")
    summary.appendix_5.losses_eur = Decimal("0")
    summary.appendix_5.rows = 1

    text = build_declaration_text(summary=summary, appendix_5_declaration_code="508")
    assert "код 508" in text
    assert "код 5082" not in text


def test_build_declaration_text_fails_on_empty_appendix_code() -> None:
    summary = FundAnalysisSummary()
    with pytest.raises(GenericFundAnalyzerError, match="missing declaration code"):
        build_declaration_text(summary=summary, appendix_5_declaration_code="  ")

