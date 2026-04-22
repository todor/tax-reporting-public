from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from integrations.p2p.shared.appendix6_models import P2PAnalysisRunResult, P2PAnalyzerError


class AfrangaAnalyzerError(P2PAnalyzerError):
    """Base error for Afranga analyzer failures."""


@dataclass(slots=True)
class AfrangaSummaryMetrics:
    reporting_year: int
    statement_period: str
    interest_received_eur: Decimal
    late_interest_received_eur: Decimal
    bonuses_eur: Decimal
    secondary_market_result_eur: Decimal


AnalysisResult = P2PAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
