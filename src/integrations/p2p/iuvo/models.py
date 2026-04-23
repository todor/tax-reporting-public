from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from integrations.p2p.shared.appendix6_models import P2PAnalysisRunResult, P2PAnalyzerError


class IuvoAnalyzerError(P2PAnalyzerError):
    """Base error for Iuvo analyzer failures."""


@dataclass(slots=True)
class IuvoSummaryMetrics:
    reporting_year: int
    statement_period: str
    interest_income_eur: Decimal
    late_fees_eur: Decimal
    secondary_market_gains_eur: Decimal
    campaign_rewards_eur: Decimal
    interest_income_iuvosave_eur: Decimal
    secondary_market_fees_eur: Decimal
    secondary_market_losses_eur: Decimal
    early_withdraw_fees_iuvosave_eur: Decimal


AnalysisResult = P2PAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
