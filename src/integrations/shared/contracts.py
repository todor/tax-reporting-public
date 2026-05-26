from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

from .spb8 import SPB8Row
from .cli_helpers import CliMode

DiagnosticSeverity = Literal["INFO", "WARNING", "MANUAL_REVIEW", "ERROR"]
AnalyzerStatus = Literal["OK", "WARNING", "NEEDS_REVIEW", "ERROR"]
ReportDetailVisibility = Literal["MAIN", "DIAGNOSTICS", "DEBUG"]
AppendixValue = Decimal | int | str


@dataclass(slots=True)
class AnalysisDiagnostic:
    """Structured analyzer diagnostic rendered by the shared report boundary.

    Expected analyzer issues should always set `code` and structured `params`.
    Free-form messages without a code are treated as defensive legacy fallback.
    """

    severity: DiagnosticSeverity
    message: str
    analyzer_alias: str
    code: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    technical_message_en: str | None = None


class UserFacingTaxError(Exception):
    """Expected user-actionable failure rendered through shared report messages."""

    def __init__(
        self,
        *,
        code: str,
        params: dict[str, Any] | None = None,
        technical_message_en: str | None = None,
    ) -> None:
        self.code = code
        self.params = params or {}
        self.technical_message_en = technical_message_en
        super().__init__(technical_message_en or code)


@dataclass(slots=True)
class AppendixRecord:
    appendix: str
    part: str | None = None
    table: str | None = None
    code: str | None = None
    values: dict[str, AppendixValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MainReportNote:
    """Human-facing note/setting that must be visible in individual and aggregate reports.

    Default categories render as compact audit/config/check summaries near the top.
    Use category="methodology" for detailed bottom methodology notes.
    """

    section_title: str
    text: str
    analyzer_alias: str | None = None
    source_path: Path | None = None
    category: str = "info"


@dataclass(frozen=True, slots=True)
class AnalyzerReportDetail:
    """Structured analyzer detail with explicit aggregate visibility."""

    key: str
    title: str
    lines: tuple[str, ...]
    visibility: ReportDetailVisibility
    analyzer_alias: str | None = None
    source_path: Path | None = None
    category: str = "diagnostics"

    def __post_init__(self) -> None:
        if self.visibility not in {"MAIN", "DIAGNOSTICS", "DEBUG"}:
            raise ValueError(f"unsupported report detail visibility: {self.visibility!r}")
        if not self.key.strip():
            raise ValueError("report detail key must not be empty")
        if not self.title.strip():
            raise ValueError("report detail title must not be empty")


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    """Analyzer-generated support artifact surfaced by shared report renderers."""

    artifact_type: str
    label: str
    path: Path
    show_in_main: bool = False
    show_in_diagnostics: bool = True

    def __post_init__(self) -> None:
        if not self.artifact_type.strip():
            raise ValueError("generated artifact type must not be empty")
        if not self.label.strip():
            raise ValueError("generated artifact label must not be empty")


@dataclass(slots=True)
class TaxAnalysisResult:
    analyzer_alias: str
    input_path: Path
    tax_year: int
    output_paths: dict[str, Path]
    appendices: list[AppendixRecord]
    diagnostics: list[AnalysisDiagnostic]
    spb8_rows: list[SPB8Row] = field(default_factory=list)
    spb8_notes: list[str] = field(default_factory=list)
    spb8_corporate_actions_present: bool = False
    main_report_notes: list[MainReportNote] = field(default_factory=list)
    report_details: list[AnalyzerReportDetail] = field(default_factory=list)
    generated_artifacts: list[GeneratedArtifact] = field(default_factory=list)
    policy_notes: list[str] = field(default_factory=list)
    policy_audit_lines: list[str] = field(default_factory=list)

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
    supports_opening_state: bool = False
