from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from integrations.p2p.shared.appendix6_models import P2PAnalysisRunResult, P2PAnalyzerError


class BondoraGoGrowAnalyzerError(P2PAnalyzerError):
    """Base error for Bondora Go & Grow analyzer failures."""


@dataclass(slots=True)
class BondoraGoGrowSummaryMetrics:
    reporting_year: int
    statement_period: str
    capital_invested_eur: Decimal
    capital_withdrawn_eur: Decimal
    withdrawal_fees_eur: Decimal
    profit_realized_eur: Decimal
    interest_accrued_eur: Decimal
    net_profit_eur: Decimal
    bonus_income_eur: Decimal


AnalysisResult = P2PAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
