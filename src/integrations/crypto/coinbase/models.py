from __future__ import annotations

from dataclasses import dataclass

from integrations.crypto.shared.crypto_ir_models import CsvRow, IrAnalysisRunResult, LoadedCsv


class CoinbaseAnalyzerError(Exception):
    """Base error for Coinbase analyzer failures."""


class CsvValidationError(CoinbaseAnalyzerError):
    """Raised when CSV structure is invalid or required fields are missing."""


class FxConversionError(CoinbaseAnalyzerError):
    """Raised when EUR conversion cannot be resolved."""


@dataclass(slots=True)
class CsvSchema:
    timestamp: str
    transaction_type: str
    asset: str
    quantity_transacted: str
    price_currency: str
    subtotal: str
    total: str
    notes: str
    fees: str | None
    review_status: str | None
    cost_basis_eur: str | None


LoadedCoinbaseCsv = LoadedCsv[CsvSchema]
AnalysisResult = IrAnalysisRunResult


__all__ = [
    "AnalysisResult",
    "CoinbaseAnalyzerError",
    "CsvRow",
    "CsvSchema",
    "CsvValidationError",
    "FxConversionError",
    "LoadedCoinbaseCsv",
]
