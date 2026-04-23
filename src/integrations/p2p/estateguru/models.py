from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from integrations.p2p.shared.appendix6_models import P2PAnalysisRunResult, P2PAnalyzerError


class EstateguruAnalyzerError(P2PAnalyzerError):
    """Base error for Estateguru analyzer failures."""


@dataclass(slots=True)
class EstateguruSummaryMetrics:
    reporting_year: int
    statement_period: str
    interest_eur: Decimal
    bonus_borrower_eur: Decimal
    penalty_eur: Decimal
    indemnity_eur: Decimal
    bonus_eg_eur: Decimal
    secondary_market_profit_loss_eur: Decimal
    sale_fee_eur: Decimal
    aum_fee_eur: Decimal
    total_eur: Decimal


AnalysisResult = P2PAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
