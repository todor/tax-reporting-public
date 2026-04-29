from __future__ import annotations

from dataclasses import dataclass

from integrations.fund.shared.fund_ir_models import FundAnalysisRunResult, LoadedCsv


class FinexifyAnalyzerError(Exception):
    """Base error for Finexify analyzer failures."""


class CsvValidationError(FinexifyAnalyzerError):
    """Raised when CSV structure is invalid or required fields are missing."""


@dataclass(slots=True)
class CsvSchema:
    tx_type: str
    cryptocurrency: str
    amount: str
    date: str
    source: str


LoadedFinexifyCsv = LoadedCsv[CsvSchema]
AnalysisResult = FundAnalysisRunResult


__all__ = [name for name in globals() if not name.startswith("__")]
