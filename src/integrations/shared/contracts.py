from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

from .cli_helpers import CliMode

DiagnosticSeverity = Literal["INFO", "WARNING", "MANUAL_REVIEW", "ERROR"]
AnalyzerStatus = Literal["OK", "WARNING", "NEEDS_REVIEW", "ERROR"]
AppendixValue = Decimal | int | str


@dataclass(slots=True)
class AnalysisDiagnostic:
    severity: DiagnosticSeverity
    message: str
    analyzer_alias: str


@dataclass(slots=True)
class AppendixRecord:
    appendix: str
    part: str | None = None
    table: str | None = None
    code: str | None = None
    values: dict[str, AppendixValue] = field(default_factory=dict)


@dataclass(slots=True)
class TaxAnalysisResult:
    analyzer_alias: str
    input_path: Path
    tax_year: int
    output_paths: dict[str, Path]
    appendices: list[AppendixRecord]
    diagnostics: list[AnalysisDiagnostic]

    @property
    def status(self) -> AnalyzerStatus:
        has_warning = False
        has_manual_review = False
        for diagnostic in self.diagnostics:
            if diagnostic.severity == "ERROR":
                return "ERROR"
            if diagnostic.severity == "MANUAL_REVIEW":
                has_manual_review = True
            elif diagnostic.severity == "WARNING":
                has_warning = True
        if has_manual_review:
            return "NEEDS_REVIEW"
        if has_warning:
            return "WARNING"
        return "OK"


@dataclass(slots=True)
class AnalyzerRunContext:
    input_path: Path
    tax_year: int
    output_dir: Path
    log_level: str
    options: dict[str, Any]


@dataclass(slots=True)
class AnalyzerDefinition:
    alias: str
    group: str
    aliases: tuple[str, ...]
    description: str
    default_output_dir: Path
    input_suffixes: tuple[str, ...]
    detection_token_sets: tuple[tuple[str, ...], ...]
    add_arguments: Callable[[argparse.ArgumentParser, CliMode], None]
    build_options: Callable[[argparse.Namespace, CliMode, dict[str, Any]], dict[str, Any]]
    run: Callable[[AnalyzerRunContext], TaxAnalysisResult]
