"""Unified analyzer contracts, discovery, auto-detection, and aggregation."""

from .aggregation import aggregate_appendix_records, render_aggregated_report
from .autodetect import (
    DetectionItem,
    DetectionResult,
    InputDetectionError,
    detect_analyzer_inputs,
    parse_analyzer_input_overrides,
)
from .cli_helpers import CliMode, add_mode_argument, option_value, resolved_cache_dir
from .contracts import (
    AnalysisDiagnostic,
    AnalyzerDefinition,
    AnalyzerRunContext,
    AnalyzerStatus,
    AppendixRecord,
    TaxAnalysisResult,
)
from .registry import AnalyzerRegistry, AnalyzerRegistryError, discover_analyzer_registry

__all__ = [
    "AnalysisDiagnostic",
    "AnalyzerDefinition",
    "AnalyzerRegistry",
    "AnalyzerRegistryError",
    "AnalyzerRunContext",
    "AnalyzerStatus",
    "AppendixRecord",
    "CliMode",
    "DetectionItem",
    "DetectionResult",
    "InputDetectionError",
    "TaxAnalysisResult",
    "add_mode_argument",
    "aggregate_appendix_records",
    "detect_analyzer_inputs",
    "discover_analyzer_registry",
    "option_value",
    "parse_analyzer_input_overrides",
    "render_aggregated_report",
    "resolved_cache_dir",
]
