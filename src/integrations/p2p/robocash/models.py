from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from integrations.p2p.shared.appendix6_models import P2PAnalysisRunResult, P2PAnalyzerError


class RobocashAnalyzerError(P2PAnalyzerError):
    """Base error for Robocash analyzer failures."""


@dataclass(slots=True)
class RobocashSummaryMetrics:
    reporting_year: int
    earned_interest_eur: Decimal
    earned_bonus_income_eur: Decimal
    taxes_withheld_eur: Decimal


AnalysisResult = P2PAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
