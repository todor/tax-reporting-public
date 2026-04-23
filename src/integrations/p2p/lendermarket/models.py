from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from integrations.p2p.shared.appendix6_models import P2PAnalysisRunResult, P2PAnalyzerError


class LendermarketAnalyzerError(P2PAnalyzerError):
    """Base error for Lendermarket analyzer failures."""


@dataclass(slots=True)
class LendermarketSummaryMetrics:
    reporting_year: int
    statement_period: str
    payments_received_eur: Decimal
    principal_amount_eur: Decimal
    interest_eur: Decimal
    late_payment_fees_eur: Decimal
    pending_payment_interest_eur: Decimal
    campaign_rewards_and_bonuses_eur: Decimal


AnalysisResult = P2PAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
