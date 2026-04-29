from __future__ import annotations

from dataclasses import dataclass

from integrations.crypto.shared.crypto_ir_models import CsvRow, IrAnalysisRunResult, LoadedCsv


class KrakenAnalyzerError(Exception):
    """Base error for Kraken analyzer failures."""


class CsvValidationError(KrakenAnalyzerError):
    """Raised when CSV structure is invalid or required fields are missing."""


class FxConversionError(KrakenAnalyzerError):
    """Raised when EUR conversion cannot be resolved."""


@dataclass(slots=True)
class CsvSchema:
    txid: str
    refid: str
    time: str
    type: str
    subtype: str
    aclass: str
    subclass: str
    asset: str
    wallet: str
    amount: str
    fee: str
    balance: str
    review_status: str | None
    cost_basis_eur: str | None


LoadedKrakenCsv = LoadedCsv[CsvSchema]
AnalysisResult = IrAnalysisRunResult


__all__ = [
    "AnalysisResult",
    "CsvRow",
    "CsvSchema",
    "CsvValidationError",
    "FxConversionError",
    "KrakenAnalyzerError",
    "LoadedKrakenCsv",
]
