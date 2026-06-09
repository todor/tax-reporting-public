from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

import report_analyzer
import report_analyzer.cli as report_cli
from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from integrations.fund.shared.fund_ir_models import FundAnalysisSummary
from integrations.ibkr.appendices.declaration_text import analysis_settings_main_report_notes
from integrations.ibkr.models import AnalysisSummary as IbkrAnalysisSummary
from integrations.shared.aggregation import render_aggregated_report
from integrations.shared.autodetect import DetectionItem, InputDetectionError, detect_analyzer_inputs
from integrations.shared.contracts import (
    AnalysisDiagnostic,
    AnalyzerReportDetail,
    AnalyzerDefinition,
    AnalyzerRunContext,
    AppendixRecord,
    GeneratedArtifact,
    MainReportNote,
    TaxAnalysisResult,
)
from integrations.shared.cli_helpers import CliMode
from integrations.shared.registry import AnalyzerRegistry
from integrations.shared.result_builders import (
    build_binance_futures_result,
    build_crypto_result,
    build_fund_result,
    build_p2p_result,
)
from integrations.shared.reporting import render_action_items, render_diagnostics_report, render_main_report
from integrations.shared.rendering.common import TECHNICAL_DETAILS_SEPARATOR
from integrations.shared.spb8 import SPB8Row
from integrations.p2p.shared.appendix6_models import InformativeRow, P2PAppendix6Result


@dataclass(slots=True)
class _RunCapture:
    contexts: list[AnalyzerRunContext]


def _ibkr_corporate_actions_diagnostic(*, count: int = 1) -> AnalysisDiagnostic:
    return AnalysisDiagnostic(
        severity="MANUAL_REVIEW",
        analyzer_alias="ibkr",
        code="IBKR_CORPORATE_ACTIONS_REVIEW_REQUIRED",
        message="IBKR Corporate Actions require manual review.",
        params={"count": count, "supported_scope": "unsupported_or_partially_supported"},
    )


def _make_registry(*definitions: AnalyzerDefinition) -> AnalyzerRegistry:
    by_alias = {definition.alias: definition for definition in definitions}
    alias_lookup: dict[str, str] = {}
    for definition in definitions:
        alias_lookup[definition.alias] = definition.alias
        for alias in definition.aliases:
            alias_lookup[alias] = definition.alias
    return AnalyzerRegistry(by_alias=by_alias, alias_lookup=alias_lookup)


def _make_fake_definition(
    *,
    alias: str,
    group: str,
    tmp_path: Path,
    run_capture: _RunCapture,
    appendices: list[AppendixRecord] | None = None,
    diagnostics: list[AnalysisDiagnostic] | None = None,
    spb8_rows: list[SPB8Row] | None = None,
    spb8_notes: list[str] | None = None,
    main_report_notes: list[MainReportNote] | None = None,
    raw_report_text: str = "ok\n",
    aggregate_mode_option_name: str | None = None,
    supported_aggregate_overrides: frozenset[str] = frozenset(),
    supports_opening_state: bool = False,
) -> AnalyzerDefinition:
    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        if mode == "single":
            parser.add_argument("--mode", default="single_default")
            return
        if aggregate_mode_option_name:
            parser.add_argument(f"--{aggregate_mode_option_name}", type=str)

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        if mode == "single":
            options = {"mode": args.mode}
            if hasattr(args, "opening_state_json"):
                options["opening_state_json"] = args.opening_state_json
            return options
        if not aggregate_mode_option_name:
            return {"mode": "aggregate_default"}
        raw = getattr(args, aggregate_mode_option_name.replace("-", "_"))
        if raw is not None:
            return {"mode": raw}
        return {"mode": str(group_options.get("p2p_secondary_market_mode", "appendix_6"))}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        output_path = context.output_dir / f"{alias}_declaration.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(raw_report_text, encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias=alias,
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": output_path},
            appendices=list(appendices or []),
            diagnostics=list(diagnostics or []),
            spb8_rows=list(spb8_rows or []),
            spb8_notes=list(spb8_notes or []),
            main_report_notes=list(main_report_notes or []),
        )

    return AnalyzerDefinition(
        alias=alias,
        group=group,
        aliases=(),
        description=f"{alias} fake analyzer",
        default_output_dir=tmp_path / alias,
        input_suffixes=(".csv", ".pdf"),
        detection_token_sets=((alias,),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
        supports_opening_state=supports_opening_state,
        supported_aggregate_overrides=supported_aggregate_overrides,
    )


def _render_aggregate_main_and_diagnostics(
    *,
    tax_year: int,
    results: list[TaxAnalysisResult],
    tmp_path: Path,
) -> tuple[str, str]:
    detected_inputs: dict[str, list[Path]] = {}
    detected_input_items: list[tuple[Path, str, str]] = []
    for result in results:
        detected_inputs.setdefault(result.analyzer_alias, []).append(result.input_path)
        detected_input_items.append((result.input_path, result.analyzer_alias, "test"))
    raw = render_aggregated_report(
        tax_year=tax_year,
        detected_inputs=detected_inputs,
        detected_input_items=detected_input_items,
        ignored_inputs=[],
        ignored_input_items=[],
        analyzer_results=results,
        analyzer_errors={},
    )
    main = render_main_report(
        status="OK",
        tax_year=tax_year,
        raw_declaration_text=raw,
        diagnostics=[],
        diagnostics_path=tmp_path / "aggregated.diagnostics.txt",
    )
    diagnostics = render_diagnostics_report(
        title="aggregate diagnostics",
        status="OK",
        raw_declaration_text=raw,
        diagnostics=[],
    )
    return main, diagnostics


def _assert_aggregate_preserves_report_detail_ids(
    *,
    results: list[TaxAnalysisResult],
    main: str,
    diagnostics: str,
) -> None:
    for result in results:
        for detail in result.report_details:
            for line in detail.lines:
                if not line:
                    continue
                if detail.visibility == "MAIN":
                    assert line in main
                    assert line not in diagnostics
                else:
                    assert line in diagnostics
                    assert line not in main


def test_generated_row_level_audit_artifact_renders_in_aggregate_outputs(tmp_path: Path) -> None:
    artifact_path = tmp_path / "coinbase_enriched_2025.csv"
    result = TaxAnalysisResult(
        analyzer_alias="coinbase",
        input_path=(tmp_path / "coinbase.csv").resolve(),
        tax_year=2025,
        output_paths={
            "declaration_txt": tmp_path / "coinbase_declaration.txt",
            "diagnostics_txt": tmp_path / "coinbase_declaration.diagnostics.txt",
        },
        appendices=[],
        diagnostics=[],
        generated_artifacts=[
            GeneratedArtifact(
                artifact_type="row_level_audit_csv",
                label="row-level audit CSV",
                path=artifact_path,
                show_in_main=True,
                show_in_diagnostics=True,
            )
        ],
    )

    main, diagnostics = _render_aggregate_main_and_diagnostics(
        tax_year=2025,
        results=[result],
        tmp_path=tmp_path,
    )

    assert "Помощни файлове за проверка" in main
    assert "CSV файл за проверка на обработените редове" in main
    assert str(artifact_path.resolve()) in main
    assert "file://" not in main

    assert "Generated artifacts" not in diagnostics
    assert f"- row_level_audit_csv: {artifact_path.resolve()}" in diagnostics
    assert f"coinbase — {result.input_path.name}: row-level audit CSV" not in diagnostics
    assert "Debug artifacts\n  - row_level_audit_csv" not in diagnostics


def test_generated_artifact_sections_are_omitted_when_empty(tmp_path: Path) -> None:
    result = TaxAnalysisResult(
        analyzer_alias="coinbase",
        input_path=(tmp_path / "coinbase.csv").resolve(),
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "coinbase_declaration.txt"},
        appendices=[],
        diagnostics=[],
    )

    main, diagnostics = _render_aggregate_main_and_diagnostics(
        tax_year=2025,
        results=[result],
        tmp_path=tmp_path,
    )

    assert "Помощни файлове за проверка" not in main
    assert "Generated artifacts" not in diagnostics
    assert "row_level_audit_csv" not in diagnostics


def test_individual_diagnostics_render_generated_artifacts_only_when_present(tmp_path: Path) -> None:
    artifact_path = tmp_path / "rows_audit.csv"

    rendered = render_diagnostics_report(
        title="coinbase analyzer diagnostics",
        status="OK",
        raw_declaration_text="",
        diagnostics=[],
        generated_artifacts=[
            GeneratedArtifact(
                artifact_type="row_level_audit_csv",
                label="row-level audit CSV",
                path=artifact_path,
                show_in_main=True,
                show_in_diagnostics=True,
            )
        ],
    )

    assert "Generated artifacts" in rendered
    assert f"- row_level_audit_csv: {artifact_path.resolve()}" in rendered
    assert "Debug artifacts" not in rendered
    assert "file://" not in rendered

    without_artifacts = render_diagnostics_report(
        title="coinbase analyzer diagnostics",
        status="OK",
        raw_declaration_text="",
        diagnostics=[],
    )
    assert "Generated artifacts" not in without_artifacts


def test_shared_result_builders_expose_row_level_audit_artifacts_generically(tmp_path: Path) -> None:
    artifact_path = tmp_path / "kraken_enriched.csv"

    result = build_crypto_result(
        analyzer_alias="kraken",
        input_path=tmp_path / "kraken.csv",
        tax_year=2025,
        output_paths={
            "enriched_ir_csv": artifact_path,
            "declaration_txt": tmp_path / "kraken.txt",
        },
        summary=IrAnalysisSummary(),
    )

    assert result.generated_artifacts == [
        GeneratedArtifact(
            artifact_type="row_level_audit_csv",
            label="row-level audit CSV",
            path=artifact_path.resolve(),
            show_in_main=True,
            show_in_diagnostics=True,
        )
    ]


def test_aggregate_report_detail_visibility_renders_by_classification(tmp_path: Path) -> None:
    result = TaxAnalysisResult(
        analyzer_alias="kraken",
        input_path=tmp_path / "kraken.csv",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "kraken_declaration.txt"},
        appendices=[],
        diagnostics=[],
        report_details=[
            AnalyzerReportDetail(
                key="main_note",
                title="Main detail",
                lines=("MAIN visible note",),
                visibility="MAIN",
                analyzer_alias="kraken",
            ),
            AnalyzerReportDetail(
                key="diagnostics_note",
                title="Diagnostics detail",
                lines=("DIAGNOSTICS visible note",),
                visibility="DIAGNOSTICS",
                analyzer_alias="kraken",
            ),
            AnalyzerReportDetail(
                key="debug_note",
                title="Debug detail",
                lines=("DEBUG artifact path: /tmp/debug.json",),
                visibility="DEBUG",
                analyzer_alias="kraken",
            ),
        ],
    )

    main, diagnostics = _render_aggregate_main_and_diagnostics(tax_year=2025, results=[result], tmp_path=tmp_path)

    assert "MAIN visible note" in main
    assert "DIAGNOSTICS visible note" not in main
    assert "DEBUG artifact path" not in main
    assert "MAIN visible note" not in diagnostics
    assert "DIAGNOSTICS visible note" in diagnostics
    assert "DEBUG artifact path: /tmp/debug.json" in diagnostics
    with pytest.raises(ValueError):
        AnalyzerReportDetail(
            key="bad",
            title="Bad",
            lines=("x",),
            visibility="UNKNOWN",  # type: ignore[arg-type]
        )


def test_aggregate_rendering_preserves_all_structured_report_detail_ids(tmp_path: Path) -> None:
    ibkr_result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=tmp_path / "ibkr_one.csv",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "ibkr_declaration.txt"},
        appendices=[],
        diagnostics=[],
        report_details=[
            AnalyzerReportDetail(
                key="ibkr_main_assumption",
                title="IBKR main assumption",
                lines=("IBKR MAIN invariant note",),
                visibility="MAIN",
                analyzer_alias="ibkr",
            ),
            AnalyzerReportDetail(
                key="ibkr_market_counters",
                title="IBKR market counters",
                lines=("IBKR DIAGNOSTICS invariant counter: 17",),
                visibility="DIAGNOSTICS",
                analyzer_alias="ibkr",
            ),
        ],
    )
    kraken_result = TaxAnalysisResult(
        analyzer_alias="kraken",
        input_path=tmp_path / "kraken_one.csv",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "kraken_declaration.txt"},
        appendices=[],
        diagnostics=[],
        report_details=[
            AnalyzerReportDetail(
                key="kraken_opening_state",
                title="Kraken opening state",
                lines=("Kraken MAIN invariant note",),
                visibility="MAIN",
                analyzer_alias="kraken",
            ),
            AnalyzerReportDetail(
                key="kraken_debug_state",
                title="Kraken debug state",
                lines=("Kraken DEBUG invariant path: /tmp/kraken-debug.json",),
                visibility="DEBUG",
                analyzer_alias="kraken",
            ),
        ],
    )

    results = [ibkr_result, kraken_result]
    main, diagnostics = _render_aggregate_main_and_diagnostics(
        tax_year=2025,
        results=results,
        tmp_path=tmp_path,
    )

    _assert_aggregate_preserves_report_detail_ids(results=results, main=main, diagnostics=diagnostics)


def test_aggregate_main_ignored_input_summary_uses_detection_classification(tmp_path: Path) -> None:
    ordinary = tmp_path / "notes.txt"
    related = tmp_path / "ibkr-not-an-activity.csv"
    noise = tmp_path / ".DS_Store"
    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        detected_input_items=[],
        ignored_inputs=[(ordinary, "no analyzer alias matched"), (related, "looked related"), (noise, "noise")],
        ignored_input_items=[
            DetectionItem(path=ordinary, analyzer_alias=None, reason="no analyzer alias matched"),
            DetectionItem(
                path=related,
                analyzer_alias="ibkr",
                reason="looked related to supported analyzer(s) [ibkr] but no analyzer input pattern matched",
                related_to_supported_analyzer=True,
                ignored_kind="related_unmatched",
            ),
            DetectionItem(
                path=noise,
                analyzer_alias=None,
                reason="known OS/editor service file ignored",
                known_noise=True,
                ignored_kind="known_noise",
            ),
        ],
        analyzer_results=[],
        analyzer_errors={},
    )
    main = render_main_report(
        status="WARNING",
        tax_year=2025,
        raw_declaration_text=raw,
        diagnostics=[
            AnalysisDiagnostic(
                severity="WARNING",
                analyzer_alias="aggregate",
                code="IGNORED_RELATED_INPUT",
                message="Ignored files looked related to supported analyzers.",
                params={"count": 1},
            )
        ],
        diagnostics_path=tmp_path / "aggregated.diagnostics.txt",
    )
    diagnostics = render_diagnostics_report(
        title="aggregate diagnostics",
        status="WARNING",
        raw_declaration_text=raw,
        diagnostics=[],
    )

    assert "Файлове, намерени в папката, но неанализирани: 2" in main
    assert "notes.txt" in main
    assert "ibkr-not-an-activity.csv" in main
    assert ".DS_Store" not in main
    assert "ВНИМАНИЕ" in main
    assert str(ordinary.resolve()) not in main
    assert str(ordinary.resolve()) in diagnostics
    assert "related_to_supported_analyzer: yes" in diagnostics
    assert "known_noise: yes" in diagnostics


def test_aggregate_main_suppresses_include_pattern_ignored_inputs(tmp_path: Path) -> None:
    selected = tmp_path / "[tax-analyzer] kraken.csv"
    excluded = tmp_path / "Binance-Futures-Trade-History.csv"
    noise = tmp_path / ".DS_Store"
    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"kraken": [selected]},
        detected_input_items=[(selected, "kraken", "auto-detected from filename tokens")],
        ignored_inputs=[
            (excluded, "does not match include-pattern '*[[]tax-analyzer[]]*'"),
            (noise, "known OS/editor service file ignored"),
        ],
        ignored_input_items=[
            DetectionItem(
                path=excluded,
                analyzer_alias=None,
                reason="does not match include-pattern '*[[]tax-analyzer[]]*'",
                ignored_kind="include_pattern",
            ),
            DetectionItem(
                path=noise,
                analyzer_alias=None,
                reason="known OS/editor service file ignored",
                known_noise=True,
                ignored_kind="known_noise",
            ),
        ],
        analyzer_results=[],
        analyzer_errors={},
    )
    main = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=raw,
        diagnostics=[],
        diagnostics_path=tmp_path / "aggregated.diagnostics.txt",
    )
    diagnostics = render_diagnostics_report(
        title="aggregate diagnostics",
        status="OK",
        raw_declaration_text=raw,
        diagnostics=[],
    )

    assert "Игнорирани входни файлове" not in main
    assert "Binance-Futures-Trade-History.csv" not in main
    assert "does not match include-pattern" in diagnostics
    assert "kind: include_pattern" in diagnostics


def test_aggregate_diagnostics_separates_input_categories_and_decision_rows(tmp_path: Path) -> None:
    analyzer_input = tmp_path / "ibkr.csv"
    auxiliary_input = tmp_path / "spb8-input-file.csv"
    ignored = tmp_path / ".DS_Store"
    result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=analyzer_input,
        tax_year=2025,
        output_paths={
            "declaration_txt": tmp_path / "ibkr_declaration.txt",
            "diagnostics_txt": tmp_path / "ibkr_declaration.diagnostics.txt",
        },
        appendices=[],
        diagnostics=[],
        report_details=[
            AnalyzerReportDetail(
                key="input_detection",
                title="Input detection context",
                lines=(
                    f"- full input path: {analyzer_input}",
                    "- analyzer alias: ibkr",
                    "- detection reason: auto-detected from filename tokens",
                ),
                visibility="DIAGNOSTICS",
                analyzer_alias="ibkr",
                source_path=analyzer_input,
                category="audit",
            )
        ],
        policy_audit_lines=[
            "- Futures policy: Mark-to-Market Performance Summary / cash-settled MTM",
            "- Futures MTM rows count: 3",
            "- CFD financing rows included in tax year: 9",
        ],
    )
    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"ibkr": [analyzer_input]},
        detected_input_items=[
            (analyzer_input, "ibkr", "auto-detected from filename tokens"),
            (auxiliary_input, "spb8-input", "auto-detected from filename tokens"),
        ],
        ignored_inputs=[(ignored, "known OS/editor service file ignored")],
        ignored_input_items=[
            DetectionItem(
                path=ignored,
                analyzer_alias=None,
                reason="known OS/editor service file ignored",
                known_noise=True,
                ignored_kind="known_noise",
            )
        ],
        analyzer_results=[result],
        analyzer_errors={},
    )
    diagnostics = render_diagnostics_report(
        title="aggregate diagnostics",
        status="OK",
        raw_declaration_text=raw,
        diagnostics=[],
        tax_year=2025,
        extra_lines=[
            "- analyzer_input_count: 1",
            "- auxiliary_input_count: 1",
            "- ignored_input_count: 1",
            "- successful_analyzer_input_count: 1",
            "- failed_analyzer_input_count: 0",
            "- warning_count: 0",
            "- error_count: 0",
        ],
    )

    assert "- analyzer_input_count: 1" in diagnostics
    assert "- auxiliary_input_count: 1" in diagnostics
    assert "- ignored_input_count: 1" in diagnostics
    assert "Analyzer inputs\n- " in diagnostics
    assert f"{analyzer_input} -> ibkr" in diagnostics
    assert "Auxiliary/manual inputs\n- " in diagnostics
    assert f"{auxiliary_input} -> spb8-input" in diagnostics
    assert "Per-input diagnostics summary" in diagnostics
    assert "spb8-input-file.csv\n- input_path" not in diagnostics
    per_input_block = diagnostics.split("ibkr: ibkr.csv", 1)[1]
    interpretation = per_input_block.split("Tax calculation summary", 1)[0]
    assert "full input path" not in interpretation
    assert "analyzer alias" not in interpretation
    decisions = per_input_block.split("Tax treatment decisions", 1)[1].split("Validation / sanity checks", 1)[0]
    calculation = per_input_block.split("Tax calculation summary", 1)[1].split("Tax treatment decisions", 1)[0]
    assert "Futures policy: Mark-to-Market" in decisions
    assert "Futures MTM rows count: 3" not in decisions
    assert "CFD financing rows included in tax year: 9" not in decisions
    assert "Futures MTM rows count: 3" in calculation
    assert "CFD financing rows included in tax year: 9" in calculation


def test_aggregate_diagnostics_do_not_render_localized_main_report_notes(tmp_path: Path) -> None:
    input_path = tmp_path / "kraken.csv"
    result = TaxAnalysisResult(
        analyzer_alias="kraken",
        input_path=input_path,
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "kraken.txt"},
        appendices=[],
        diagnostics=[],
        main_report_notes=[
            MainReportNote(
                section_title="Анализаторни допускания и проверки",
                text='kraken — kraken.csv: Начално състояние: отчетът се третира като "since inception".',
                analyzer_alias="kraken",
                source_path=input_path,
                category="setting",
            )
        ],
        report_details=[
            AnalyzerReportDetail(
                key="localized_main_note",
                title="Localized main note",
                lines=("Потребителска бележка за основния отчет.",),
                visibility="MAIN",
                analyzer_alias="kraken",
                source_path=input_path,
            ),
            AnalyzerReportDetail(
                key="technical_summary",
                title="Technical summary",
                lines=(
                    "- opening_state: none (since inception)",
                    "- rows included in declaration (tax year): 61",
                    "- manual check overrides (Review Status non-empty): 1",
                ),
                visibility="DIAGNOSTICS",
                analyzer_alias="kraken",
                source_path=input_path,
            ),
        ],
    )
    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"kraken": [input_path]},
        detected_input_items=[(input_path, "kraken", "auto-detected from filename tokens")],
        ignored_inputs=[],
        ignored_input_items=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    diagnostics = render_diagnostics_report(
        title="aggregate diagnostics",
        status="OK",
        raw_declaration_text=raw,
        diagnostics=[],
        tax_year=2025,
    )

    assert "Потребителска бележка" not in diagnostics
    assert "Начално състояние" not in diagnostics
    assert "kraken — kraken.csv" not in diagnostics
    assert "opening_state: none (since inception)" in diagnostics
    assert "rows included in declaration (tax year): 61" in diagnostics
    assert "manual check overrides (Review Status non-empty): 1" in diagnostics


def test_p2p_individual_main_notes_propagate_to_aggregate_main(tmp_path: Path) -> None:
    input_path = tmp_path / "iuvo_report.pdf"
    p2p = P2PAppendix6Result(
        platform="iuvo",
        tax_year=2025,
        part1_rows=[],
        aggregate_code_603=Decimal("0"),
        aggregate_code_606=Decimal("0"),
        taxable_code_603=Decimal("0"),
        taxable_code_606=Decimal("0"),
        withheld_tax=Decimal("0"),
        informative_rows=[
            InformativeRow("Secondary-market mode used", "appendix_6"),
            InformativeRow("Early withdraw fees iuvoSAVE", Decimal("2")),
        ],
        informational_messages=[
            "Iuvo Early withdraw fees iuvoSAVE is informational only and is not mapped to Appendix 6 totals",
        ],
    )
    result = build_p2p_result(
        analyzer_alias="iuvo",
        input_path=input_path,
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "iuvo.txt"},
        result=p2p,
    )

    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"iuvo": [input_path]},
        detected_input_items=[(input_path, "iuvo", "test")],
        ignored_inputs=[],
        ignored_input_items=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    main = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=raw,
        diagnostics=[],
        diagnostics_path=tmp_path / "aggregated.diagnostics.txt",
    )

    assert "Iuvo: Early withdraw fees iuvoSAVE са само информативни" in main
    assert "Информативни стойности в индивидуалния отчет" not in main
    assert "P2P вторичен пазар: използван режим appendix_6" in main
    settings_section = main.split("Настройки и данъчни допускания", 1)[1].split("Специфични бележки от анализа", 1)[0]
    assert "iuvo —" not in settings_section
    assert "Специфични бележки от анализа" in main
    raw_diagnostics = render_diagnostics_report(
        title="aggregate diagnostics",
        status="OK",
        raw_declaration_text=raw,
        diagnostics=[],
    )
    assert "Early withdraw fees iuvoSAVE: 2.00" in raw_diagnostics


def test_main_report_groups_action_items_by_severity_and_avoids_duplicate_notes(tmp_path: Path) -> None:
    input_path = tmp_path / "Lendermarket_v1_report_2024.pdf"
    p2p_result = build_p2p_result(
        analyzer_alias="lendermarket",
        input_path=input_path,
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "lendermarket.txt"},
        result=P2PAppendix6Result(
            platform="lendermarket",
            tax_year=2025,
            part1_rows=[],
            aggregate_code_603=Decimal("0"),
            aggregate_code_606=Decimal("0"),
            taxable_code_603=Decimal("0"),
            taxable_code_606=Decimal("0"),
            withheld_tax=Decimal("0"),
            warnings=["reporting year in PDF (2024) differs from requested tax year (2025)"],
        ),
    )
    error = AnalysisDiagnostic(
        severity="ERROR",
        analyzer_alias="binance_futures",
        code="MISSING_REQUIRED_COLUMNS",
        message="Missing required columns.",
        params={
            "filename": "Binance-Futures-Trade-History.csv",
            "columns": ["User ID", "Account", "Operation"],
        },
    )
    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"lendermarket": [input_path]},
        detected_input_items=[(input_path, "lendermarket", "test")],
        ignored_inputs=[],
        ignored_input_items=[],
        analyzer_results=[p2p_result],
        analyzer_errors={},
    )
    main = render_main_report(
        status="ERROR",
        tax_year=2025,
        raw_declaration_text=raw,
        diagnostics=[error, *p2p_result.diagnostics],
        diagnostics_path=tmp_path / "aggregated.diagnostics.txt",
    )

    assert "Грешки\n- Грешка: файлът Binance-Futures-Trade-History.csv" in main
    assert "Изискват ръчен преглед\n- Lendermarket: отчетната година" in main
    action_section = main.split("Какво трябва да направите", 1)[1].split("ВНИМАНИЕ:", 1)[0]
    assert "Предупреждения" not in action_section
    assert main.count("отчетната година в отчета (2024)") == 1
    settings_section = main.split("Настройки и данъчни допускания", 1)[1].split("Приложение", 1)[0]
    assert "lendermarket —" not in settings_section
    assert "Review Status overrides" not in main
    assert "Ръчно зададени статуси" not in main


def test_single_analyzer_mode_runs_selected_analyzer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="ibkr", group="broker", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(fake)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    input_file = tmp_path / "ibkr.csv"
    input_file.write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "ibkr",
            "--input",
            str(input_file),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 0
    assert "STATUS: SUCCESS" in stdout
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].input_path == input_file.resolve()
    assert run_capture.contexts[0].options["display_currency"] == "EUR"
    output_path = tmp_path / "out" / "ibkr_declaration.txt"
    diagnostics_path = tmp_path / "out" / "ibkr_declaration.diagnostics.txt"
    assert output_path.exists()
    assert diagnostics_path.exists()
    declaration = output_path.read_text(encoding="utf-8")
    diagnostics = diagnostics_path.read_text(encoding="utf-8")
    assert "✅ Статус: УСПЕШЕН" in declaration
    assert "Данъчна година: 2025" in declaration
    assert "Бележки и допускания" not in declaration
    assert "Изчисления и визуализация" in declaration
    assert "Диагностика" in declaration
    assert TECHNICAL_DETAILS_SEPARATOR not in declaration
    assert "Diagnostics" in diagnostics
    assert "Run summary" in diagnostics
    assert "Technical Details" not in diagnostics


def test_single_stateful_analyzer_uses_generic_opening_state_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    input_file = tmp_path / "kraken.csv"
    state_file = tmp_path / "kraken.state.json"
    input_file.write_text("x\n", encoding="utf-8")
    state_file.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "kraken",
            "--input",
            str(input_file),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--opening-state-json",
            str(state_file),
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].options["opening_state_json"] == str(state_file.resolve())
    diagnostics = (tmp_path / "out" / "kraken_declaration.diagnostics.txt").read_text(encoding="utf-8")
    assert f"opening_state: {state_file.resolve()} (CLI override)" in diagnostics


def test_single_stateful_analyzer_without_opening_state_is_since_inception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    input_file = tmp_path / "kraken.csv"
    input_file.write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "kraken",
            "--input",
            str(input_file),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    assert run_capture.contexts[0].options["opening_state_json"] is None
    diagnostics = (tmp_path / "out" / "kraken_declaration.diagnostics.txt").read_text(encoding="utf-8")
    assert "opening_state: none (since inception)" in diagnostics


def test_analyzer_specific_opening_state_flags_are_not_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=_RunCapture(contexts=[]),
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))

    parser = report_analyzer.build_parser()
    help_text = parser.format_help()

    assert "--opening-state-json" in help_text
    assert "--kraken-opening-state-json" not in help_text


def test_aggregate_help_shows_generic_options_and_override_convention() -> None:
    parser = report_analyzer.build_parser()

    help_text = parser.format_help()

    assert "--tax-exempt-mode {execution_exchange,listing_exchange}" in help_text
    assert "--eu-regulated-exchange EU_REGULATED_EXCHANGE" in help_text
    assert "--closed-world" in help_text
    assert "--no-net-cfd-financing" in help_text
    assert "--negative-pil-mode {always-net,ignore,position-aware}" in help_text
    assert "--positive-wht-mode {current-year-net,prior-year-correction}" in help_text
    assert "--p2p-secondary-market-mode {appendix_5,appendix_6}" in help_text
    assert "--spb8-csv-decimal-separator {auto,dot,comma}" in help_text
    assert "--csv-decimal-separator {auto,dot,comma}" not in help_text
    assert "--list-aggregate-overrides" in help_text
    assert "--afranga-secondary-market-mode" not in help_text
    assert "--<analyzer-alias>-<option>" in help_text
    assert "In analyzer-specific commands" in help_text
    assert "unprefixed option" in help_text
    assert "To see the supported analyzer-prefixed options" in help_text
    assert "the analyzer-prefixed value wins" in help_text
    assert "configured only once" in help_text


def test_list_aggregate_overrides_prints_supported_matrix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = report_analyzer.main(["--list-aggregate-overrides"])

    stdout = capsys.readouterr().out

    assert code == 0
    assert "Supported aggregate analyzer overrides:" in stdout
    assert "ibkr:" in stdout
    assert "--ibkr-tax-exempt-mode" in stdout
    assert "--ibkr-eu-regulated-exchange" in stdout
    assert "--ibkr-closed-world" in stdout
    assert "--ibkr-skip-period-validation" not in stdout
    assert "--ibkr-no-net-cfd-financing" in stdout
    assert "--ibkr-negative-pil-mode" in stdout
    assert "--ibkr-positive-wht-mode" in stdout
    assert "--ibkr-appendix8-dividend-list-mode" in stdout
    assert "--ibkr-csv-decimal-separator" in stdout
    assert "Tax-exempt mode" in stdout
    assert "Appendix 8 dividend list mode" in stdout
    assert "CSV decimal separator" in stdout
    assert "equivalent to" not in stdout
    assert "interactive-brokers" not in stdout
    assert "interactivebrokers" not in stdout
    assert "afranga:" in stdout
    assert "--afranga-p2p-secondary-market-mode" in stdout
    assert "P2P secondary market mode" in stdout
    assert "bondora_go_grow:" in stdout
    assert "--bondora-go-grow-p2p-secondary-market-mode" in stdout
    assert "--bondora-p2p-secondary-market-mode" in stdout
    assert "--go-grow-p2p-secondary-market-mode" in stdout
    assert "estateguru:" in stdout
    assert "--estateguru-p2p-secondary-market-mode" in stdout
    assert "iuvo:" in stdout
    assert "--iuvo-p2p-secondary-market-mode" in stdout
    assert "coinbase:" in stdout
    assert "--coinbase-csv-decimal-separator" in stdout
    assert "kraken:" in stdout
    assert "--kraken-csv-decimal-separator" in stdout
    assert "finexify:" in stdout
    assert "--finexify-csv-decimal-separator" in stdout
    assert "binance_futures:" in stdout
    assert "--binance-futures-csv-decimal-separator" in stdout
    assert "--binance-csv-decimal-separator" in stdout
    assert "lendermarket:" in stdout
    assert "--lendermarket-p2p-secondary-market-mode" in stdout
    assert "robocash:" in stdout
    assert "--robocash-p2p-secondary-market-mode" in stdout


def test_listed_aggregate_overrides_match_validation_registry() -> None:
    registry = report_analyzer.discover_analyzer_registry()
    lines = report_cli._aggregate_override_lines(registry=registry)
    listed_flags = {
        line.strip().split()[0]
        for line in lines
        if line.strip().startswith("--")
    }
    expected_flags: set[str] = set()
    for definition in registry.definitions():
        aliases = (definition.alias,) if definition.alias == "ibkr" else (definition.alias, *definition.aliases)
        for alias in aliases:
            prefix = alias.replace("_", "-")
            for option in report_cli._OVERRIDABLE_AGGREGATE_OPTIONS:
                if option.group_key in definition.supported_aggregate_overrides:
                    expected_flags.add(f"--{prefix}-{option.flag}")

    assert listed_flags == expected_flags


def test_concrete_ibkr_help_shows_unprefixed_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = report_analyzer.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["ibkr", "--help"])
    help_text = capsys.readouterr().out

    assert "--tax-exempt-mode" in help_text
    assert "--eu-regulated-exchange" in help_text
    assert "--closed-world" in help_text
    assert "--skip-period-validation" not in help_text
    assert "--no-net-cfd-financing" in help_text
    assert "--negative-pil-mode" in help_text
    assert "--positive-wht-mode" in help_text
    assert "--appendix8-dividend-list-mode" in help_text
    assert "--csv-decimal-separator" in help_text
    assert "--ibkr-tax-exempt-mode" not in help_text


def test_concrete_p2p_help_shows_unprefixed_p2p_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = report_analyzer.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["afranga", "--help"])
    help_text = capsys.readouterr().out

    assert "--p2p-secondary-market-mode" in help_text
    assert "--afranga-p2p-secondary-market-mode" not in help_text


def test_concrete_csv_analyzer_help_shows_unprefixed_decimal_separator_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = report_analyzer.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["coinbase", "--help"])
    help_text = capsys.readouterr().out

    assert "--csv-decimal-separator {auto,dot,comma}" in help_text
    assert "--coinbase-csv-decimal-separator" not in help_text


@pytest.mark.parametrize("legacy_value", ["execution", "listed", "listed_symbol"])
def test_aggregate_tax_exempt_mode_rejects_old_value_aliases(legacy_value: str) -> None:
    parser = report_analyzer.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--tax-exempt-mode", legacy_value])


def test_aggregate_analyzer_specific_override_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        return dict(group_options)

    fake = _make_fake_definition(
        alias="t212",
        group="broker",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supported_aggregate_overrides=frozenset(
            {
                "tax_exempt_mode",
                "negative_pil_mode",
                "positive_wht_mode",
                "appendix8_dividend_list_mode",
                "csv_decimal_separator",
            }
        ),
    )
    fake = AnalyzerDefinition(
        alias=fake.alias,
        group=fake.group,
        aliases=("trading212",),
        description=fake.description,
        default_output_dir=fake.default_output_dir,
        input_suffixes=fake.input_suffixes,
        detection_token_sets=fake.detection_token_sets,
        add_arguments=fake.add_arguments,
        build_options=build_options,
        run=fake.run,
        supported_aggregate_overrides=fake.supported_aggregate_overrides,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "t212_report.csv").write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--no-spb8",
            "--tax-exempt-mode",
            "listing_exchange",
            "--trading212-tax-exempt-mode",
            "execution_exchange",
            "--negative-pil-mode",
            "position-aware",
            "--t212-negative-pil-mode=ignore",
            "--positive-wht-mode",
            "current-year-net",
            "--t212-positive-wht-mode",
            "prior-year-correction",
            "--appendix8-dividend-list-mode",
            "company",
            "--t212-appendix8-dividend-list-mode",
            "country",
            "--t212-csv-decimal-separator",
            "comma",
        ]
    )

    assert code == 0
    assert run_capture.contexts[0].options["tax_exempt_mode"] == "execution_exchange"
    assert run_capture.contexts[0].options["negative_pil_mode"] == "ignore"
    assert run_capture.contexts[0].options["positive_wht_mode"] == "prior-year-correction"
    assert run_capture.contexts[0].options["appendix8_dividend_list_mode"] == "country"
    assert run_capture.contexts[0].options["csv_decimal_separator"] == "comma"


def test_aggregate_unprefixed_csv_decimal_separator_is_rejected() -> None:
    parser = report_analyzer.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--csv-decimal-separator", "comma"])


def test_aggregate_repeatable_override_replaces_aggregate_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        return dict(group_options)

    fake = _make_fake_definition(
        alias="future",
        group="broker",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supported_aggregate_overrides=frozenset({"eu_regulated_exchange"}),
    )
    fake = AnalyzerDefinition(
        alias=fake.alias,
        group=fake.group,
        aliases=(),
        description=fake.description,
        default_output_dir=fake.default_output_dir,
        input_suffixes=fake.input_suffixes,
        detection_token_sets=fake.detection_token_sets,
        add_arguments=fake.add_arguments,
        build_options=build_options,
        run=fake.run,
        supported_aggregate_overrides=fake.supported_aggregate_overrides,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "future_report.csv").write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--no-spb8",
            "--eu-regulated-exchange",
            "GENERIC",
            "--future-eu-regulated-exchange",
            "ONE",
            "--future-eu-regulated-exchange=TWO,THREE",
        ]
    )

    assert code == 0
    assert run_capture.contexts[0].options["eu_regulated_exchange"] == ["ONE", "TWO,THREE"]


def test_aggregate_override_unknown_analyzer_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _make_fake_definition(
        alias="t212",
        group="broker",
        tmp_path=tmp_path,
        run_capture=_RunCapture(contexts=[]),
        supported_aggregate_overrides=frozenset({"tax_exempt_mode"}),
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))

    code = report_analyzer.main(["--unknown-tax-exempt-mode", "listing_exchange"])
    stdout = capsys.readouterr().out

    assert code == 2
    assert "Unsupported analyzer override: --unknown-tax-exempt-mode" in stdout
    assert "Use --list-aggregate-overrides" in stdout


def test_aggregate_override_unsupported_for_known_analyzer_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _make_fake_definition(alias="t212", group="broker", tmp_path=tmp_path, run_capture=_RunCapture(contexts=[]))
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))

    code = report_analyzer.main(["--t212-tax-exempt-mode", "listing_exchange"])
    stdout = capsys.readouterr().out

    assert code == 2
    assert "Unsupported analyzer override: --t212-tax-exempt-mode" in stdout
    assert "The option tax-exempt-mode is not supported by analyzer t212." in stdout
    assert "Use --list-aggregate-overrides" in stdout


def test_aggregate_ibkr_report_alias_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _make_fake_definition(
        alias="ibkr",
        group="broker",
        tmp_path=tmp_path,
        run_capture=_RunCapture(contexts=[]),
        supported_aggregate_overrides=frozenset({"tax_exempt_mode"}),
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))

    code = report_analyzer.main(["--ibkr-report-alias", "acc1"])
    stdout = capsys.readouterr().out

    assert code == 2
    assert "Unsupported analyzer override: --ibkr-report-alias" in stdout
    assert "The option report-alias is not supported by analyzer ibkr." in stdout
    assert "Use --list-aggregate-overrides" in stdout


def test_aggregate_old_generated_p2p_override_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _make_fake_definition(
        alias="afranga",
        group="p2p",
        tmp_path=tmp_path,
        run_capture=_RunCapture(contexts=[]),
        supported_aggregate_overrides=frozenset({"p2p_secondary_market_mode"}),
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))

    code = report_analyzer.main(["--afranga-secondary-market-mode", "appendix_5"])
    stdout = capsys.readouterr().out

    assert code == 2
    assert "Unsupported analyzer override: --afranga-secondary-market-mode" in stdout
    assert "The option secondary-market-mode is not supported by analyzer afranga." in stdout
    assert "Use --list-aggregate-overrides" in stdout


def test_single_analyzer_report_strips_duplicate_legacy_review_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        output_path = context.output_dir / "lendermarket_declaration.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(
                [
                    "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!",
                    "- old duplicate",
                    "",
                    "Бележки по обработката",
                    "- reporting year in PDF (2023) differs from requested tax year (2025)",
                    "",
                    "Приложение 6",
                ]
            ),
            encoding="utf-8",
        )
        return TaxAnalysisResult(
            analyzer_alias="lendermarket",
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": output_path},
            appendices=[],
            diagnostics=[
                AnalysisDiagnostic(
                    severity="MANUAL_REVIEW",
                    message="reporting year in PDF (2023) differs from requested tax year (2025)",
                    analyzer_alias="lendermarket",
                )
            ],
        )

    definition = AnalyzerDefinition(
        alias="lendermarket",
        group="p2p",
        aliases=(),
        description="lendermarket fake analyzer",
        default_output_dir=tmp_path / "lendermarket",
        input_suffixes=(".csv",),
        detection_token_sets=(("lendermarket",),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(definition))
    input_file = tmp_path / "lendermarket.csv"
    input_file.write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "lendermarket",
            "--input",
            str(input_file),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    report = (tmp_path / "out" / "lendermarket_declaration.txt").read_text(encoding="utf-8")
    assert report.startswith("🔎 Статус: ИЗИСКВА РЪЧЕН ПРЕГЛЕД\nДанъчна година: 2025")
    assert "Бележки по обработката" not in report
    assert "old duplicate" not in report
    assert report.count("отчетната година в PDF (2023)") == 1
    assert "Приложение 6" in report


def test_diagnostics_report_renders_common_fields_readably() -> None:
    rendered = render_diagnostics_report(
        title="Test diagnostics",
        status="ERROR",
        raw_declaration_text="",
        diagnostics=[
            AnalysisDiagnostic(
                severity="WARNING",
                message="extra context",
                analyzer_alias="coinbase",
                params={"analyzer": "coinbase", "foo": {"items": ["a", "b"]}},
            ),
            AnalysisDiagnostic(
                severity="ERROR",
                message="missing required columns",
                analyzer_alias="binance_futures",
                code="MISSING_REQUIRED_COLUMNS",
                params={
                    "path": "/tmp/Binance-Futures-Trade-History.csv",
                    "filename": "Binance-Futures-Trade-History.csv",
                    "columns": ["User ID", "Account", "Operation"],
                },
            ),
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                message="reporting year in PDF (2023) differs from requested tax year (2025)",
                analyzer_alias="lendermarket",
                params={
                    "source_file": "/tmp/Lendermarket_v1_report_2023.pdf",
                    "report_path": "/tmp/lendermarket_v1_report_2023_declaration.txt",
                },
            ),
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                message="SPB-8 required values are missing.",
                analyzer_alias="spb8",
                code="SPB8_MISSING_VALUES",
                params={
                    "missing_values": [
                        {
                            "platform": "binance_futures",
                            "country": "Франция",
                            "currency": "EUR",
                            "type": "03",
                            "missing": ["start_amount", "end_amount"],
                        }
                    ]
                },
            ),
        ],
    )

    assert "Structured diagnostics" not in rendered
    assert "Diagnostic messages\n[ERROR] [binance_futures] MISSING_REQUIRED_COLUMNS" in rendered
    assert "message: missing required columns" in rendered
    assert "file: /tmp/Binance-Futures-Trade-History.csv" in rendered
    assert "filename: Binance-Futures-Trade-History.csv" not in rendered
    assert "missing columns:\n  - User ID\n  - Account\n  - Operation" in rendered
    assert "params:" not in rendered
    assert "\n\n[MANUAL_REVIEW] [lendermarket]" in rendered
    assert "source file: /tmp/Lendermarket_v1_report_2023.pdf" in rendered
    assert "report: /tmp/lendermarket_v1_report_2023_declaration.txt" in rendered
    assert "missing values:\n  - platform: binance_futures" in rendered
    assert "    country: Франция" in rendered
    assert "    missing:\n      - start_amount\n      - end_amount" in rendered
    assert "context:\n  foo:" in rendered
    assert "items:\n      - a\n      - b" in rendered
    assert rendered.index("[ERROR]") < rendered.index("[MANUAL_REVIEW]") < rendered.index("[WARNING]")


def test_diagnostics_report_renders_scalar_lists_compactly_with_samples() -> None:
    rendered = render_diagnostics_report(
        title="Test diagnostics",
        status="ERROR",
        raw_declaration_text="",
        diagnostics=[
            AnalysisDiagnostic(
                severity="ERROR",
                message="IBKR Activity Statement contains realized disposal activity but no ClosedLot rows.",
                analyzer_alias="ibkr",
                code="IBKR_INCOMPLETE_CLOSED_LOTS",
                params={
                    "closing_trade_count": 12,
                    "closing_trade_rows": [222, 226, 231, 234, 235, 236, 239, 241, 244, 249, 252, 255],
                    "realized_summary_count": 3,
                    "realized_summary_rows": [223, 227, 232],
                },
            )
        ],
    )

    assert "closing_trade_count: 12" in rendered
    assert "closing_trade_rows_sample: [222, 226, 231, 234, 235, 236, 239, 241, 244, 249, ...]" in rendered
    assert "realized_summary_count: 3" in rendered
    assert "realized_summary_rows: [223, 227, 232]" in rendered
    assert "closing_trade_rows:\n    - 222" not in rendered


def test_ibkr_row_level_diagnostics_are_grouped_for_main_report() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "row 6691: Forex ignored: missing Review Status (taxable forex not supported) "
                "(symbol=EUR.USD, execution_exchange=IDEALFX)"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "row 6692: Forex ignored: missing Review Status (taxable forex not supported) "
                "(symbol=EUR.USD, execution_exchange=IDEALFX)"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "row 6606: unsupported Trades Asset Category 'Futures' was skipped; "
                "unsupported Trades schema was not parsed"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="MANUAL_REVIEW",
            message="има 3 записа с изисквана ръчна проверка",
            analyzer_alias="ibkr",
        ),
    ]

    rendered = "\n".join(render_action_items(diagnostics))

    assert "Предупреждение за ibkr" not in rendered
    assert "IBKR: има 2 Forex реда, които не са включени автоматично." in rendered
    assert "Засегнати редове: 6691, 6692" in rendered
    assert "IBKR: има 1 реда от неподдържани Trades категории, които са пропуснати." in rendered
    assert "има 3 реда с изисквана ръчна проверка" not in rendered


def test_ibkr_manual_review_rows_render_concrete_examples_in_main_and_diagnostics() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "row 699: Unmapped listing exchange (open-world mode) "
                "(symbol=ECCC, listing_exchange=<missing from Financial Instrument Information>, "
                "mapped_classification=MISSING, execution_exchange=NYSE)"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "row 702: Unmapped listing exchange (open-world mode) "
                "(symbol=ECCC, listing_exchange=FOOEX, mapped_classification=UNMAPPED, execution_exchange=NYSE)"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="MANUAL_REVIEW",
            message="има 2 записа с изисквана ръчна проверка",
            analyzer_alias="ibkr",
        ),
    ]

    main = "\n".join(render_action_items(diagnostics))
    technical = render_diagnostics_report(
        title="IBKR diagnostics",
        status="NEEDS_REVIEW",
        raw_declaration_text="",
        diagnostics=diagnostics,
    )

    assert main.count("IBKR:") == 1
    assert "IBKR_MANUAL_REVIEW_ROWS" in main
    assert "Категория: ръчен преглед." in main
    assert "борсата на листване от IBKR Financial Instrument Information липсва или не е мапната" in main
    assert "Важно: execution_exchange показва къде е изпълнена сделката" in main
    assert (
        "row 699, section=Trades, symbol=ECCC, "
        "listing_exchange=<missing from Financial Instrument Information>, "
        "mapped_classification=MISSING, execution_exchange=NYSE"
    ) in main
    assert (
        "row 702, section=Trades, symbol=ECCC, listing_exchange=FOOEX, "
        "mapped_classification=UNMAPPED, execution_exchange=NYSE"
    ) in main
    assert "Прегледайте детайлите в диагностичния файл" not in main
    assert "Проверете Financial Instrument Information реда за съответния символ" in main
    assert "[WARNING] [ibkr] UNCLASSIFIED_WARNING_GROUP" not in technical
    assert "[MANUAL_REVIEW] [ibkr] IBKR_MANUAL_REVIEW_ROWS" in technical
    assert "examples:\n  -\n    row: 699" in technical
    assert "listing_exchange: <missing from Financial Instrument Information>" in technical
    assert "listing_exchange: FOOEX" in technical
    assert "mapped_classification: UNMAPPED" in technical


def test_ibkr_open_position_reconciliation_warnings_are_not_duplicated_as_unclassified() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "OPEN_POSITION_TRADE_QTY_MISMATCH: asset=Stocks symbol=1YD prior_qty=60 "
                "trade_delta_qty=-150 expected_open_qty=-90 actual_open_qty=0 diff=-90"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="WARNING",
            message=(
                "OPEN_POSITION_TRADE_QTY_MISMATCH: asset=Stocks symbol=AAPL prior_qty=29.449 "
                "trade_delta_qty=-0.449 expected_open_qty=29.000 actual_open_qty=0 diff=29.000"
            ),
            analyzer_alias="ibkr",
        ),
        AnalysisDiagnostic(
            severity="MANUAL_REVIEW",
            message="има 2 записа с изисквана ръчна проверка",
            analyzer_alias="ibkr",
        ),
    ]

    main = "\n".join(render_action_items(diagnostics))
    technical = render_diagnostics_report(
        title="IBKR diagnostics",
        status="NEEDS_REVIEW",
        raw_declaration_text="",
        diagnostics=diagnostics,
    )

    assert "IBKR: има 2 несъответствия между Open Positions и Trades." in main
    assert "IBKR_MANUAL_REVIEW_ROWS" not in main
    assert "UNCLASSIFIED_WARNING_GROUP" not in main
    assert "[MANUAL_REVIEW] [ibkr] IBKR_OPEN_POSITION_RECONCILIATION_MISMATCH" in technical
    assert "[MANUAL_REVIEW] [ibkr] IBKR_MANUAL_REVIEW_ROWS" not in technical
    assert "[WARNING] [ibkr] UNCLASSIFIED_WARNING_GROUP" not in technical
    assert "count: 2" in technical
    assert "symbol: 1YD" in technical
    assert "trade_delta_qty: -150" in technical


def test_ibkr_option_unhandled_rows_are_specific_and_include_examples() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            analyzer_alias="ibkr",
            code="IBKR_OPTIONS_UNHANDLED_ROWS",
            message="Equity/index option rows require review because no attached ClosedLot was found.",
            params={
                "count": 2,
                "rows": [
                    {
                        "row": "10",
                        "section": "Trades",
                        "symbol": "SPY 20DEC24 585 P",
                        "date": "2024-12-20",
                        "code": "Ep",
                        "reason": "expiry-style option row without attached ClosedLot",
                    }
                ],
            },
        )
    ]

    main = "\n".join(render_action_items(diagnostics))
    technical = render_diagnostics_report(
        title="IBKR diagnostics",
        status="WARNING",
        raw_declaration_text="",
        diagnostics=diagnostics,
    )

    assert "IBKR_OPTIONS_UNHANDLED_ROWS" in main
    assert "опции изискват преглед" in main
    assert "без attached ClosedLot" in main
    assert "row 10, section=Trades, symbol=SPY 20DEC24 585 P" in main
    assert "[WARNING] [ibkr] IBKR_OPTIONS_UNHANDLED_ROWS" in technical
    assert "expiry-style option row without attached ClosedLot" in technical


def test_ibkr_positive_withholding_reversal_is_one_structured_action_item() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            message="Appendix 8 groups have non-positive net foreign tax after positive WHT corrections.",
            analyzer_alias="ibkr",
            code="IBKR_DIVIDEND_WHT_REVERSAL_REVIEW",
            params={"positive_wht_rows": 1, "non_positive_net_buckets": 1},
        ),
    ]

    main = "\n".join(render_action_items(diagnostics))
    technical = render_diagnostics_report(
        title="IBKR diagnostics",
        status="WARNING",
        raw_declaration_text="",
        diagnostics=diagnostics,
    )

    assert "UNCLASSIFIED" not in main
    assert main.count("IBKR: има Appendix 8 групи с нулев или отрицателен нетен чуждестранен данък") == 1
    assert "Положителни Withholding Tax редове: 1." not in main
    assert "Избран е режим current-year-net" not in main
    assert "--positive-wht-mode prior-year-correction" not in main
    assert "--ibkr-positive-wht-mode prior-year-correction" not in main
    assert "има Appendix 8 групи с нулев или отрицателен нетен чуждестранен данък" in main
    assert "UNCLASSIFIED" not in technical
    assert "[WARNING] [ibkr] IBKR_DIVIDEND_WHT_REVERSAL_REVIEW" in technical
    assert "message: Appendix 8 groups have non-positive net foreign tax after positive WHT corrections." in technical
    assert "message: Открит е положителен ред" not in technical
    assert "Нетният чуждестранен данък" not in technical


def test_ibkr_positive_withholding_prior_year_mode_warning_is_mode_aware() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            message="Appendix 8 groups have non-positive net foreign tax after positive WHT corrections.",
            analyzer_alias="ibkr",
            code="IBKR_DIVIDEND_WHT_REVERSAL_REVIEW",
            params={
                "positive_wht_rows": 1,
                "non_positive_net_buckets": 1,
                "positive_wht_mode": "prior-year-correction",
            },
        ),
    ]

    main = "\n".join(render_action_items(diagnostics))

    assert "IBKR: има Appendix 8 групи с нулев или отрицателен нетен чуждестранен данък" in main
    assert "Избран е режим prior-year-correction" not in main
    assert "с дата в текущата данъчна година" not in main
    assert "с дата в предходни години се показват отделно" not in main
    assert "--positive-wht-mode prior-year-correction" not in main


def test_ibkr_grouped_diagnostics_are_technical_and_structured() -> None:
    rendered = render_diagnostics_report(
        title="IBKR diagnostics",
        status="WARNING",
        raw_declaration_text="",
        diagnostics=[
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                message="има 17 Forex записа (TAXABLE/липсващ/непознат Review Status), които са изключени",
                analyzer_alias="ibkr",
            ),
            AnalysisDiagnostic(
                severity="WARNING",
                message=(
                    "row 6691: Forex ignored: missing Review Status (taxable forex not supported) "
                    "(symbol=EUR.USD, execution_exchange=IDEALFX)"
                ),
                analyzer_alias="ibkr",
            ),
            AnalysisDiagnostic(
                severity="WARNING",
                message=(
                    "row 6692: Forex ignored: missing Review Status (taxable forex not supported) "
                    "(symbol=EUR.USD, execution_exchange=IDEALFX)"
                ),
                analyzer_alias="ibkr",
            ),
            AnalysisDiagnostic(
                severity="WARNING",
                message=(
                    "row 6763: unknown dividend description requires manual review "
                    "(description='ECCC(US2698097035) Payment in Lieu of Dividend (Ordinary Dividend)')"
                ),
                analyzer_alias="ibkr",
            ),
        ],
    )

    assert rendered.count("FOREX_ROWS_IGNORED") == 1
    assert "[MANUAL_REVIEW] [ibkr] FOREX_ROWS_IGNORED" in rendered
    assert "message: Forex rows were ignored because taxable forex is not supported" in rendered
    assert "count: 17" in rendered
    assert "rows:\n  -\n    row: 6691" in rendered
    assert "execution_exchange: IDEALFX" in rendered
    assert "[WARNING] [ibkr] UNKNOWN_DIVIDEND_ROWS" in rendered
    assert "message: има 17 Forex" not in rendered
    assert "Payment in Lieu of Dividend" in rendered


def test_forex_policy_note_does_not_duplicate_actionable_diagnostic() -> None:
    raw_text = "\n".join(
        [
            "Forex операции",
            "- Forex сделки (конвертиране на валута или търговия) не се включват автоматично в Приложение 5/13 в тази версия.",
            "- Forex редове с Review Status=NON-TAXABLE се третират като нетаксируеми.",
            "- Forex редове с Review Status=TAXABLE, празен или непознат статус изискват ръчен преглед.",
            "",
            "Приложение 5",
            "Таблица 2",
            "- Код 508",
        ]
    )
    rendered = render_main_report(
        status="NEEDS_REVIEW",
        tax_year=2025,
        raw_declaration_text=raw_text,
        diagnostics_path=Path("/tmp/ibkr.diagnostics.txt"),
        diagnostics=[
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                message=(
                    "row 6691: Forex ignored: missing Review Status (taxable forex not supported) "
                    "(symbol=EUR.USD, execution_exchange=IDEALFX)"
                ),
                analyzer_alias="ibkr",
            )
        ],
    )

    assert "ВНИМАНИЕ: FOREX ОПЕРАЦИИ" not in rendered
    assert rendered.count("Forex реда, които не са включени автоматично") == 1
    assert "Засегнати редове: 6691" in rendered
    assert rendered.index("Какво трябва да направите") < rendered.index("Приложение 5")
    assert rendered.index("Приложение 5") < rendered.index("Forex операции")
    assert "брой Forex записи" not in rendered
    assert "общ обем" not in rendered
    assert "Forex редове с Review Status=NON-TAXABLE се третират като нетаксируеми." in rendered


def test_spb8_notes_are_counted_and_do_not_leave_duplicate_heading() -> None:
    raw_text = "\n".join(
        [
            "СПБ-8",
            "Данни за попълване",
            "- Тип на вземането: 03. Сметки, открити в чужбина",
            "  Матуритет: ",
            "  Държава: Ирландия",
            "  Валута: EUR",
            "  Размер в началото на отчетната година (в хиляди валутни единици): 1.00",
            "  Размер в края на отчетната година (в хиляди валутни единици): 2.00",
            "",
            "Бележки към СПБ-8",
            "- CFD позициите не се включват в СПБ-8.",
        ]
    )

    rendered = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=raw_text,
        diagnostics=[],
        diagnostics_path=Path("/tmp/report.diagnostics.txt"),
    )

    assert "- Информационни бележки: 4" in rendered
    assert rendered.splitlines().count("СПБ-8") == 1
    assert "Данни за попълване" in rendered
    assert "- CFD позициите не се включват в СПБ-8." in rendered


def test_aggregate_spb8_rows_and_notes_render_under_one_heading() -> None:
    result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=Path("/tmp/ibkr.csv"),
        tax_year=2025,
        output_paths={},
        appendices=[],
        diagnostics=[],
        policy_notes=[
            "CFD позициите не се декларират в Приложение 8, защото не представляват реално притежание на акции/дялове.",
            "При CFD не се използва пълният notional/номинал на договора като продажна цена или цена на придобиване.",
            "CFD financing / CFD interest корекциите са включени в Приложение 5.",
        ],
        policy_audit_lines=["- CFD financing policy: netted_to_appendix_5"],
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
        spb8_rows=[
            SPB8Row(
                "kraken",
                "kraken",
                "03",
                "Ирландия",
                "EUR",
                Decimal("1000"),
                Decimal("2000"),
            )
        ],
        spb8_notes=["CFD позициите не се включват в СПБ-8."],
    )

    assert rendered.splitlines().count("СПБ-8") == 2
    assert "Данни за попълване" in rendered
    assert "Бележки към СПБ-8" not in rendered
    assert "- CFD позициите не се включват в СПБ-8." in rendered
    assert rendered.index("Данни за попълване") < rendered.index("Методологични бележки")
    assert rendered.index("Методологични бележки") < rendered.rindex("СПБ-8")
    assert "CFD и PIL" in rendered
    assert "- CFD позициите не се декларират в Приложение 8" in rendered
    assert "- При CFD не се използва пълният notional/номинал на договора" in rendered
    assert "- CFD financing / CFD interest корекциите са включени в Приложение 5." in rendered
    assert "Policy details" not in rendered
    assert "Tax treatment decisions" in rendered
    assert "- CFD financing policy: netted_to_appendix_5" in rendered


def test_aggregate_report_renders_ibkr_corporate_actions_as_global_manual_review() -> None:
    diagnostic = _ibkr_corporate_actions_diagnostic(count=2)
    rendered = render_main_report(
        status="NEEDS_REVIEW",
        tax_year=2025,
        raw_declaration_text=render_aggregated_report(
            tax_year=2025,
            detected_inputs={},
            ignored_inputs=[],
            analyzer_results=[
                TaxAnalysisResult(
                    analyzer_alias="ibkr",
                    input_path=Path("/tmp/ibkr.csv"),
                    tax_year=2025,
                    output_paths={},
                    appendices=[],
                    diagnostics=[diagnostic],
                    spb8_corporate_actions_present=True,
                )
            ],
            analyzer_errors={},
            spb8_notes=[
                "Откритите IBKR Corporate Actions може да влияят на коректността на СПБ-8 количествата, "
                "защото могат да променят ISIN-и, количества или позиции. "
                "Вижте съответното предупреждение в секцията „Изискват ръчен преглед“."
            ],
        ),
        diagnostics=[diagnostic],
        diagnostics_path=Path("/tmp/report.diagnostics.txt"),
    )

    assert "Изискват ръчен преглед" in rendered
    assert rendered.count("IBKR: открити са Corporate Actions в Activity Statement CSV") == 1
    assert "Засегнати редове: 2." in rendered
    assert "корпоративните събития може да влияят както на СПБ-8 количествата" in rendered
    assert "Прегледайте секцията Corporate Actions в IBKR отчета ръчно." in rendered
    assert "СПБ-8\n- Откритите IBKR Corporate Actions" in rendered
    assert "Попълнете липсващите стойности в генерирания SPB-8 input файл:" not in rendered
    assert diagnostic.params["supported_scope"] == "unsupported_or_partially_supported"


def test_aggregate_report_does_not_emit_corporate_actions_warning_for_crypto() -> None:
    rendered = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=render_aggregated_report(
            tax_year=2025,
            detected_inputs={},
            ignored_inputs=[],
            analyzer_results=[
                TaxAnalysisResult(
                    analyzer_alias="kraken",
                    input_path=Path("/tmp/kraken.csv"),
                    tax_year=2025,
                    output_paths={},
                    appendices=[],
                    diagnostics=[],
                )
            ],
            analyzer_errors={},
        ),
        diagnostics=[],
        diagnostics_path=Path("/tmp/report.diagnostics.txt"),
    )

    assert "Corporate Actions" not in rendered
    assert "корпоративните събития" not in rendered


def test_aggregate_report_renders_generic_main_report_notes_near_top() -> None:
    results = [
        TaxAnalysisResult(
            analyzer_alias="alpha",
            input_path=Path("/tmp/alpha.csv"),
            tax_year=2025,
            output_paths={},
            appendices=[
                AppendixRecord(
                    appendix="5",
                    table="2",
                    code="5082",
                    values={"trade_count": 1},
                )
            ],
                diagnostics=[],
                main_report_notes=[
                    MainReportNote(
                        section_title="Alpha — режими, класификации и проверки",
                        text="Alpha използва режим A.",
                        analyzer_alias="alpha",
                        category="setting",
                )
            ],
        ),
        TaxAnalysisResult(
            analyzer_alias="beta",
            input_path=Path("/tmp/beta.csv"),
            tax_year=2025,
            output_paths={},
            appendices=[],
                diagnostics=[],
                main_report_notes=[
                    MainReportNote(
                        section_title="Beta — режими, класификации и проверки",
                        text="Beta използва режим B.",
                        analyzer_alias="beta",
                        category="setting",
                )
            ],
        ),
    ]

    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=results,
        analyzer_errors={},
    )

    assert "Настройки и данъчни допускания" in rendered
    assert "Alpha — режими, класификации и проверки\n- Alpha използва режим A." in rendered
    assert "Beta — режими, класификации и проверки\n- Beta използва режим B." in rendered
    assert rendered.index("Настройки и данъчни допускания") < rendered.index("Приложение 5")


def test_aggregate_report_renders_prior_year_corrections_after_spb8_before_helpers(tmp_path: Path) -> None:
    artifact_path = tmp_path / "ibkr_modified_2025.csv"
    result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=tmp_path / "ibkr.csv",
        tax_year=2025,
        output_paths={},
        appendices=[
            AppendixRecord(
                appendix="8",
                part="III",
                code="8141",
                values={
                    "payer": "Alpha Corp",
                    "country": "САЩ",
                    "treaty_method": "1",
                    "gross_income_eur": Decimal("100"),
                    "foreign_tax_eur": Decimal("15"),
                    "allowable_credit_eur": Decimal("5"),
                    "recognized_credit_eur": Decimal("5"),
                    "tax_due_eur": Decimal("0"),
                },
            ),
            AppendixRecord(
                appendix="9",
                part="II",
                code="603",
                values={
                    "country": "Ирландия",
                    "gross_income_eur": Decimal("10"),
                    "expenses_eur": Decimal("0"),
                    "social_security_eur": Decimal("0"),
                    "taxable_income_eur": Decimal("10"),
                    "foreign_tax_eur": Decimal("2"),
                    "allowable_credit_eur": Decimal("1"),
                    "recognized_credit_eur": Decimal("1"),
                },
            ),
        ],
        diagnostics=[],
        spb8_rows=[
            SPB8Row(
                "ibkr",
                "ibkr",
                "04",
                "САЩ",
                "USD",
                Decimal("1"),
                Decimal("2"),
                isin="US1111111111",
            )
        ],
        main_report_notes=[
            MainReportNote(
                section_title="Корекции към предходни години",
                text=(
                    "Приложение 8, Част III\n"
                    "- Година: 2024\n"
                    "  - Alpha Corp; САЩ; код 8141; метод 1; корекция 10.00000000 EUR; редове 12\n\n"
                    "Тази секция не се попълва в текущата декларация за 2025; използва се само за "
                    "проверка/корекция на вече подадени декларации за предходни години."
                ),
                analyzer_alias="ibkr",
                category="appendix8_corrections",
            ),
            MainReportNote(
                section_title="Alpha methodology",
                text="Long explanation.",
                analyzer_alias="ibkr",
                category="methodology",
            ),
        ],
        generated_artifacts=[
            GeneratedArtifact(
                artifact_type="row_level_audit_csv",
                label="row-level audit CSV",
                path=artifact_path,
                show_in_main=True,
                show_in_diagnostics=True,
            )
        ],
    )

    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"ibkr": [tmp_path / "ibkr.csv"]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
        spb8_rows=result.spb8_rows,
    )

    assert rendered.index("Приложение 8") < rendered.index("Приложение 9")
    assert rendered.index("Приложение 9") < rendered.index("Корекции към предходни години")
    assert rendered.index("Корекции към предходни години") < rendered.index("СПБ-8")
    assert rendered.index("СПБ-8") < rendered.index("Помощни файлове за проверка")
    assert rendered.index("Помощни файлове за проверка") < rendered.index("Методологични бележки")
    assert "Тази секция не се попълва в текущата декларация за 2025" in rendered
    assert "Бележки към СПБ-8" not in rendered


def test_aggregate_report_renders_generic_methodology_notes_at_bottom(tmp_path: Path) -> None:
    result = TaxAnalysisResult(
        analyzer_alias="alpha",
        input_path=Path("/tmp/alpha.csv"),
        tax_year=2025,
        output_paths={},
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="5082",
                values={
                    "sale_value_eur": Decimal("10"),
                    "acquisition_value_eur": Decimal("6"),
                    "profit_eur": Decimal("4"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 1,
                    "net_result_eur": Decimal("4"),
                },
            )
        ],
        diagnostics=[],
        main_report_notes=[
            MainReportNote(
                section_title="Alpha — режими, класификации и проверки",
                text="Alpha compact audit summary.",
                analyzer_alias="alpha",
                category="setting",
            ),
            MainReportNote(
                section_title="Alpha detailed methodology",
                text="Alpha long methodology explains how the compact summary affects declaration values.",
                analyzer_alias="alpha",
                category="methodology",
            ),
        ],
    )

    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    rendered = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=raw,
        diagnostics=[],
        diagnostics_path=tmp_path / "aggregated.diagnostics.txt",
    )

    assert "Настройки и данъчни допускания" in rendered
    assert "Alpha compact audit summary." in rendered
    assert "Подробни методологични бележки" in rendered
    assert (
        "Следващите бележки поясняват използваните режими и методи от горното обобщение"
        in rendered
    )
    assert "Alpha detailed methodology" in rendered
    assert "Alpha long methodology explains how the compact summary affects declaration values." in rendered
    assert rendered.index("Alpha compact audit summary.") < rendered.index("Приложение 5")
    assert rendered.index("Приложение 5") < rendered.index("Подробни методологични бележки")
    assert "Методологични бележки" not in rendered


def test_aggregate_report_deduplicates_identical_main_report_notes() -> None:
    note = MainReportNote(
        section_title="Alpha — режими, класификации и проверки",
        text="Един и същ режим за няколко входа.",
        analyzer_alias="alpha",
        category="setting",
    )
    results = [
        TaxAnalysisResult(
            analyzer_alias="alpha",
            input_path=Path("/tmp/alpha-one.csv"),
            tax_year=2025,
            output_paths={},
            appendices=[],
            diagnostics=[],
            main_report_notes=[note],
        ),
        TaxAnalysisResult(
            analyzer_alias="alpha",
            input_path=Path("/tmp/alpha-two.csv"),
            tax_year=2025,
            output_paths={},
            appendices=[],
            diagnostics=[],
            main_report_notes=[note],
        ),
    ]

    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=results,
        analyzer_errors={},
    )
    main = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=rendered,
        diagnostics=[],
        diagnostics_path=Path("/tmp/aggregate.diagnostics.txt"),
    )

    assert main.count("Един и същ режим за няколко входа.") == 1


def test_aggregate_report_deduplicates_ibkr_tax_exempt_mode_setting() -> None:
    notes = analysis_settings_main_report_notes(
        IbkrAnalysisSummary(tax_year=2025, tax_exempt_mode="listing_exchange")
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=[
            TaxAnalysisResult(
                analyzer_alias="ibkr",
                input_path=Path("/tmp/ibkr.csv"),
                tax_year=2025,
                output_paths={},
                appendices=[],
                diagnostics=[],
                main_report_notes=notes,
            )
        ],
        analyzer_errors={},
    )

    assert "IBKR — класификация на пазари" in rendered
    assert "Режим за данъчно освобождаване: listing_exchange." in rendered
    assert "Класификация на IBKR сделките за данъчно освобождаване: listing_exchange." not in rendered
    assert "борсата на изпълнение е само информативна" in rendered


def test_aggregate_report_consolidates_ibkr_market_classification_notes() -> None:
    results = [
        TaxAnalysisResult(
            analyzer_alias="ibkr",
            input_path=Path(f"/tmp/ibkr_{index}.csv"),
            tax_year=2025,
            output_paths={},
            appendices=[],
            diagnostics=[],
            main_report_notes=analysis_settings_main_report_notes(summary),
        )
        for index, summary in enumerate(
            [
                IbkrAnalysisSummary(
                    tax_year=2025,
                    tax_exempt_mode="listing_exchange",
                    exchange_classification_mode="OPEN_WORLD MODE",
                    encountered_eu_regulated_exchanges=set(),
                    encountered_non_eu_exchanges={"NASDAQ"},
                ),
                IbkrAnalysisSummary(
                    tax_year=2025,
                    tax_exempt_mode="listing_exchange",
                    exchange_classification_mode="OPEN_WORLD MODE",
                    encountered_eu_regulated_exchanges={"IBIS2"},
                    encountered_non_eu_exchanges={"NYSE"},
                ),
            ]
        )
    ]

    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=results,
        analyzer_errors={},
    )

    market_section = rendered.split("IBKR — класификация на пазари", 1)[1].split("\n\n", 1)[0]
    assert market_section.count("Разпознати регулирани пазари от ЕС в отчета") == 1
    assert "Разпознати регулирани пазари от ЕС в отчета: IBIS2." in market_section
    assert "Разпознати регулирани пазари от ЕС в отчета: няма." not in market_section
    assert "Разпознати пазари извън ЕС: NASDAQ, NYSE." in market_section


def test_ibkr_instrument_method_summaries_are_top_only_and_methodology_is_bottom(tmp_path: Path) -> None:
    notes = analysis_settings_main_report_notes(
        IbkrAnalysisSummary(
            tax_year=2025,
            tax_exempt_mode="listing_exchange",
            cfd_trade_rows=1,
            cfd_financing_rows=1,
            futures_mtm_rows=1,
            option_closedlot_rows=1,
        )
    )
    result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=Path("/tmp/ibkr.csv"),
        tax_year=2025,
        output_paths={},
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="508",
                values={
                    "sale_value_eur": Decimal("10"),
                    "acquisition_value_eur": Decimal("6"),
                    "profit_eur": Decimal("4"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 1,
                    "net_result_eur": Decimal("4"),
                },
            )
        ],
        diagnostics=[],
        main_report_notes=notes,
        policy_notes=["CFD detailed methodology."],
    )
    raw = render_aggregated_report(
        tax_year=2025,
        detected_inputs={},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    rendered = render_main_report(
        status="OK",
        tax_year=2025,
        raw_declaration_text=raw,
        diagnostics=[],
        diagnostics_path=tmp_path / "aggregate.diagnostics.txt",
    )

    assert "IBKR — използвани методи за инструменти" in rendered
    assert "CFD/PIL: използва се реализиран икономически резултат" in rendered
    assert "Фючърси: използва се Mark-to-Market Performance Summary" in rendered
    assert "Опции: използват се реализирани резултати" in rendered
    assert "Подробни методологични бележки" in rendered
    assert "CFD и PIL" in rendered
    assert "CFD detailed methodology." in rendered
    assert "Фючърси — IBKR daily cash-settled MTM" in rendered
    assert "Опции върху акции и индекси" in rendered
    assert rendered.index("IBKR — използвани методи за инструменти") < rendered.index("Приложение 5")
    assert rendered.index("Приложение 5") < rendered.index("Подробни методологични бележки")
    assert rendered.index("CFD и PIL") < rendered.index("Фючърси — IBKR daily cash-settled MTM")
    top_area = rendered[: rendered.index("Приложение 5")]
    assert "IBKR фючърсите се отчитат по дневна mark-to-market сетълмент логика" not in top_area


def test_known_family_warnings_use_structured_diagnostics_not_unclassified(tmp_path: Path) -> None:
    crypto_summary = IrAnalysisSummary()
    crypto_summary.warnings.extend(
        [
            "row 7: unsupported Transaction Type='Airdrop'; excluded from tax calculations",
            "row 7: unsupported Transaction Type='Airdrop'; excluded from tax calculations",
        ]
    )
    crypto_result = build_crypto_result(
        analyzer_alias="kraken",
        input_path=tmp_path / "kraken.csv",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "kraken.txt"},
        summary=crypto_summary,
    )

    fund_summary = FundAnalysisSummary()
    fund_summary.warnings.append("row 12: unsupported Type='Bonus'; excluded from tax calculations (amount=10)")
    fund_result = build_fund_result(
        analyzer_alias="finexify",
        input_path=tmp_path / "finexify.csv",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "finexify.txt"},
        summary=fund_summary,
        declaration_code="5082",
    )

    p2p_result = build_p2p_result(
        analyzer_alias="lendermarket",
        input_path=tmp_path / "lendermarket.pdf",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "lendermarket.txt"},
        result=P2PAppendix6Result(
            platform="lendermarket",
            tax_year=2025,
            part1_rows=[],
            aggregate_code_603=Decimal("0"),
            aggregate_code_606=Decimal("0"),
            taxable_code_603=Decimal("0"),
            taxable_code_606=Decimal("0"),
            withheld_tax=Decimal("0"),
            warnings=["secondary market amount requires manual review for loan L-42"],
        ),
    )

    binance_result = build_binance_futures_result(
        analyzer_alias="binance_futures",
        input_path=tmp_path / "binance.csv",
        tax_year=2025,
        output_paths={"declaration_txt": tmp_path / "binance.txt"},
        sale_value_eur=Decimal("0"),
        acquisition_value_eur=Decimal("0"),
        profit_eur=Decimal("0"),
        loss_eur=Decimal("0"),
        trade_count=0,
        warnings=["row 7: unsupported income type=funding fee"],
    )

    diagnostics = [
        *crypto_result.diagnostics,
        *fund_result.diagnostics,
        *p2p_result.diagnostics,
        *binance_result.diagnostics,
    ]
    main = "\n".join(render_action_items(diagnostics))
    technical = render_diagnostics_report(
        title="family diagnostics",
        status="NEEDS_REVIEW",
        raw_declaration_text="",
        diagnostics=diagnostics,
    )

    assert "UNCLASSIFIED" not in technical
    assert "unsupported Transaction Type='Airdrop'" not in main
    assert "unsupported Type='Bonus'" not in main
    assert "secondary market amount requires manual review" not in main
    assert "unsupported income type=funding fee" not in main
    assert "Kraken: има 1 неподдържани крипто транзакции" in main
    assert "Finexify: има 1 неподдържани fund реда" in main
    assert "Lendermarket: има 1 запис от вторичен пазар" in main
    assert "Binance Futures: има 1 Binance Futures funding fee реда" in main
    assert "[MANUAL_REVIEW] [kraken] CRYPTO_UNSUPPORTED_TRANSACTION_TYPE" in technical
    assert "[MANUAL_REVIEW] [finexify] FUND_UNSUPPORTED_ROW_TYPE" in technical
    assert "[MANUAL_REVIEW] [lendermarket] P2P_SECONDARY_MARKET_REVIEW_REQUIRED" in technical
    assert "[WARNING] [binance_futures] BINANCE_FUTURES_FUNDING_FEE_REVIEW_REQUIRED" in technical
    assert "unsupported Transaction Type='Airdrop'" in technical
    assert "secondary market amount requires manual review for loan L-42" in technical


def test_unclassified_fallback_keeps_raw_details_only_in_diagnostics() -> None:
    diagnostics = [
        AnalysisDiagnostic(
            severity="WARNING",
            message="unexpected raw English warning that has no structured code",
            analyzer_alias="coinbase",
        )
    ]

    main = "\n".join(render_action_items(diagnostics))
    technical = render_diagnostics_report(
        title="fallback diagnostics",
        status="WARNING",
        raw_declaration_text="",
        diagnostics=diagnostics,
    )

    assert "unexpected raw English warning" not in main
    assert "Coinbase: UNCLASSIFIED_WARNING_GROUP - има 1 предупреждения, които изискват преглед." in main
    assert "Причина: диагностиката няма структуриран код" in main
    assert "[WARNING] [coinbase] UNCLASSIFIED_WARNING_GROUP" in technical
    assert "unexpected raw English warning that has no structured code" in technical


def test_list_analyzers_outputs_builtin_aliases(capsys: pytest.CaptureFixture[str]) -> None:
    code = report_analyzer.main(["--list-analyzers"])
    stdout = capsys.readouterr().out

    assert code == 0
    assert "ibkr" in stdout
    assert "kraken" in stdout


def test_single_analyzer_mode_passes_display_currency_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="ibkr", group="broker", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(fake)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    input_file = tmp_path / "ibkr.csv"
    input_file.write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "ibkr",
            "--input",
            str(input_file),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--display-currency",
            "BGN",
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].options["display_currency"] == "BGN"


@pytest.mark.parametrize(
    "module_name",
    [
        "integrations.ibkr.activity_statement_analyzer",
        "integrations.crypto.binance.futures_pnl_analyzer",
        "integrations.crypto.coinbase.report_analyzer",
        "integrations.crypto.kraken.report_analyzer",
        "integrations.fund.finexify.report_analyzer",
        "integrations.p2p.afranga.report_analyzer",
        "integrations.p2p.estateguru.report_analyzer",
        "integrations.p2p.lendermarket.report_analyzer",
        "integrations.p2p.iuvo.report_analyzer",
        "integrations.p2p.robocash.report_analyzer",
        "integrations.p2p.bondora_go_grow.report_analyzer",
    ],
)
def test_standalone_analyzer_modules_do_not_expose_main(module_name: str) -> None:
    module = importlib.import_module(module_name)

    assert not hasattr(module, "main")


def test_auto_detection_uses_alias_tokens_and_include_pattern(tmp_path: Path) -> None:
    run_capture = _RunCapture(contexts=[])
    coinbase = _make_fake_definition(alias="coinbase", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    kraken = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(coinbase, kraken)

    (tmp_path / "Coinbase Report.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "kraken_ledger.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "kraken_ledger.state.json").write_text("{}\n", encoding="utf-8")

    detection = detect_analyzer_inputs(
        input_dir=tmp_path,
        include_pattern="*.csv",
        registry=registry,
    )

    assert [path.name for path in detection.detected["coinbase"]] == ["Coinbase Report.csv"]
    assert [path.name for path in detection.detected["kraken"]] == ["kraken_ledger.csv"]
    ignored = {item.path.name: item.reason for item in detection.ignored_items}
    assert "notes.txt" in ignored
    assert "kraken_ledger.state.json" in ignored
    assert "state sidecar files" in ignored["kraken_ledger.state.json"]


def test_auto_detection_include_pattern_supports_escaped_literal_brackets(tmp_path: Path) -> None:
    run_capture = _RunCapture(contexts=[])
    afranga = _make_fake_definition(alias="afranga", group="p2p", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(afranga)

    (tmp_path / "[tax-analyzer] Afranga report.pdf").write_text("x\n", encoding="utf-8")
    (tmp_path / "Afranga report.pdf").write_text("x\n", encoding="utf-8")

    detection = detect_analyzer_inputs(
        input_dir=tmp_path,
        include_pattern="*[[]tax-analyzer[]]*",
        registry=registry,
    )

    assert [path.name for path in detection.detected["afranga"]] == ["[tax-analyzer] Afranga report.pdf"]


def test_auto_detection_allows_multiple_files_per_analyzer(tmp_path: Path) -> None:
    run_capture = _RunCapture(contexts=[])
    coinbase = _make_fake_definition(alias="coinbase", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(coinbase)

    (tmp_path / "coinbase_account_a.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "coinbase_account_b.csv").write_text("x\n", encoding="utf-8")

    detection = detect_analyzer_inputs(
        input_dir=tmp_path,
        include_pattern="*.csv",
        registry=registry,
    )

    assert [path.name for path in detection.detected["coinbase"]] == [
        "coinbase_account_a.csv",
        "coinbase_account_b.csv",
    ]


def test_auto_detection_fails_on_ambiguous_match(tmp_path: Path) -> None:
    run_capture = _RunCapture(contexts=[])
    left = _make_fake_definition(alias="alpha", group="misc", tmp_path=tmp_path, run_capture=run_capture)
    right = _make_fake_definition(alias="beta", group="misc", tmp_path=tmp_path, run_capture=run_capture)

    left = AnalyzerDefinition(
        alias=left.alias,
        group=left.group,
        aliases=left.aliases,
        description=left.description,
        default_output_dir=left.default_output_dir,
        input_suffixes=left.input_suffixes,
        detection_token_sets=(("shared",),),
        add_arguments=left.add_arguments,
        build_options=left.build_options,
        run=left.run,
    )
    right = AnalyzerDefinition(
        alias=right.alias,
        group=right.group,
        aliases=right.aliases,
        description=right.description,
        default_output_dir=right.default_output_dir,
        input_suffixes=right.input_suffixes,
        detection_token_sets=(("shared",),),
        add_arguments=right.add_arguments,
        build_options=right.build_options,
        run=right.run,
    )

    registry = _make_registry(left, right)
    (tmp_path / "shared.csv").write_text("x\n", encoding="utf-8")

    with pytest.raises(InputDetectionError, match="ambiguous analyzer mapping"):
        detect_analyzer_inputs(
            input_dir=tmp_path,
            include_pattern="*.csv",
            registry=registry,
        )


def test_group_param_and_analyzer_override_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(
        alias="afranga",
        group="p2p",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supported_aggregate_overrides=frozenset({"p2p_secondary_market_mode"}),
    )
    fake = AnalyzerDefinition(
        alias=fake.alias,
        group=fake.group,
        aliases=fake.aliases,
        description=fake.description,
        default_output_dir=fake.default_output_dir,
        input_suffixes=fake.input_suffixes,
        detection_token_sets=fake.detection_token_sets,
        add_arguments=fake.add_arguments,
        build_options=lambda args, mode, group_options: {
            "mode": str(group_options.get("p2p_secondary_market_mode", "appendix_6"))
        },
        run=fake.run,
        supported_aggregate_overrides=fake.supported_aggregate_overrides,
    )
    registry = _make_registry(fake)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    input_file = tmp_path / "afranga.pdf"
    input_file.write_text("x\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--p2p-secondary-market-mode",
            "appendix_6",
            "--afranga-p2p-secondary-market-mode",
            "appendix_5",
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].options["mode"] == "appendix_5"


def test_clean_output_safety_rejects_dangerous_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_rmtree(path):  # noqa: ANN001
        raise AssertionError(f"rmtree must not be called for dangerous path: {path}")

    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)

    dangerous_paths = [
        Path("/"),
        Path(Path.cwd().resolve().anchor),
        Path.home(),
        Path.cwd(),
        report_analyzer.PROJECT_ROOT,
    ]
    for path in dangerous_paths:
        with pytest.raises(InputDetectionError):
            report_analyzer._prepare_output_dir(output_dir=path, clean_output=True)


def test_aggregate_mode_sums_structured_appendix_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    a1 = _make_fake_definition(
        alias="coinbase",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="5082",
                values={
                    "sale_value_eur": Decimal("10"),
                    "acquisition_value_eur": Decimal("6"),
                    "profit_eur": Decimal("4"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 1,
                    "net_result_eur": Decimal("4"),
                },
            )
        ],
    )
    a2 = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="5082",
                values={
                    "sale_value_eur": Decimal("15"),
                    "acquisition_value_eur": Decimal("5"),
                    "profit_eur": Decimal("10"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 2,
                    "net_result_eur": Decimal("10"),
                },
            )
        ],
    )
    registry = _make_registry(a1, a2)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    (tmp_path / "coinbase.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "kraken.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--analyzer-input",
            f"coinbase={tmp_path / 'coinbase.csv'}",
            "--analyzer-input",
            f"kraken={tmp_path / 'kraken.csv'}",
        ]
    )

    assert code == 0
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "- Код 5082" in report
    assert "  Продажна цена: 25.00 EUR" in report
    assert "  Цена на придобиване: 11.00 EUR" in report
    assert "- Брой сделки: 3" in report


def test_aggregate_mode_processes_multiple_inputs_for_same_analyzer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        amount = Decimal("1") if "account_a" in context.input_path.name else Decimal("2")
        out = context.output_dir / "declaration.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok\n", encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias="coinbase",
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": out},
            appendices=[
                AppendixRecord(
                    appendix="5",
                    table="2",
                    code="5082",
                    values={
                        "sale_value_eur": amount,
                        "acquisition_value_eur": amount,
                        "profit_eur": Decimal("0"),
                        "loss_eur": Decimal("0"),
                        "trade_count": 1,
                        "net_result_eur": Decimal("0"),
                    },
                )
            ],
            diagnostics=[],
        )

    definition = AnalyzerDefinition(
        alias="coinbase",
        group="crypto",
        aliases=(),
        description="coinbase fake analyzer",
        default_output_dir=tmp_path / "coinbase",
        input_suffixes=(".csv",),
        detection_token_sets=(("coinbase",),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )
    registry = _make_registry(definition)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    (tmp_path / "coinbase_account_a.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "coinbase_account_b.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 2
    assert run_capture.contexts[0].output_dir != run_capture.contexts[1].output_dir
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "  Продажна цена: 3.00 EUR" in report
    assert "- Брой сделки: 2" in report


def test_aggregate_mode_supports_repeated_override_for_same_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    definition = _make_fake_definition(alias="coinbase", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(definition)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    input_a = tmp_path / "a.csv"
    input_b = tmp_path / "b.csv"
    input_a.write_text("x\n", encoding="utf-8")
    input_b.write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--analyzer-input",
            f"coinbase={input_a}",
            "--analyzer-input",
            f"coinbase={input_b}",
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 2
    run_inputs = sorted(context.input_path.name for context in run_capture.contexts)
    assert run_inputs == ["a.csv", "b.csv"]


def test_aggregate_single_stateful_input_accepts_simple_opening_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    definition = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(definition))
    (tmp_path / "kraken.csv").write_text("x\n", encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--opening-state-json",
            str(state_file),
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].options["opening_state_json"] == str(state_file.resolve())


def test_aggregate_simple_opening_state_fails_for_multiple_stateful_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    kraken = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    coinbase = _make_fake_definition(
        alias="coinbase",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(kraken, coinbase))
    (tmp_path / "kraken.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "coinbase.csv").write_text("x\n", encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--opening-state-json",
            str(state_file),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 2
    assert "exactly one stateful input" in stdout
    assert "input-file=state.json" in stdout
    assert not run_capture.contexts


def test_aggregate_mapped_opening_states_apply_per_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    kraken = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    coinbase = _make_fake_definition(
        alias="coinbase",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(kraken, coinbase))
    kraken_input = tmp_path / "kraken-main.csv"
    coinbase_input = tmp_path / "coinbase-main.csv"
    kraken_state = tmp_path / "kraken-main.state.json"
    coinbase_state = tmp_path / "coinbase-main.state.json"
    kraken_input.write_text("x\n", encoding="utf-8")
    coinbase_input.write_text("x\n", encoding="utf-8")
    kraken_state.write_text("{}\n", encoding="utf-8")
    coinbase_state.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--opening-state-json",
            f"kraken-main.csv={kraken_state}",
            "--opening-state-json",
            f"coinbase:coinbase-main.csv={coinbase_state}",
        ]
    )

    assert code == 0
    by_name = {context.input_path.name: context.options["opening_state_json"] for context in run_capture.contexts}
    assert by_name == {
        "coinbase-main.csv": str(coinbase_state.resolve()),
        "kraken-main.csv": str(kraken_state.resolve()),
    }


def test_aggregate_opening_state_sidecars_are_auto_detected_and_not_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    definition = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(definition))
    input_file = tmp_path / "kraken-report.csv"
    sidecar = tmp_path / "kraken-report.state.json"
    unmatched = tmp_path / "orphan.state.json"
    input_file.write_text("x\n", encoding="utf-8")
    sidecar.write_text("{}\n", encoding="utf-8")
    unmatched.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].input_path == input_file.resolve()
    assert run_capture.contexts[0].options["opening_state_json"] == str(sidecar.resolve())
    diagnostics = (tmp_path / "out" / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert f"{input_file.resolve()} -> kraken: Opening state: {sidecar.resolve()} (auto-detected sidecar)" in diagnostics
    assert "unmatched state sidecar ignored" in diagnostics
    assert str(unmatched.resolve()) in diagnostics
    assert "state sidecar files (*.state.json) are not analyzed as input reports" in diagnostics


def test_aggregate_cli_opening_state_mapping_overrides_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    definition = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(definition))
    input_file = tmp_path / "kraken-report.csv"
    sidecar = tmp_path / "kraken-report.state.json"
    override = tmp_path / "manual.json"
    input_file.write_text("x\n", encoding="utf-8")
    sidecar.write_text("{}\n", encoding="utf-8")
    override.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--opening-state-json",
            f"kraken-report.csv={override}",
        ]
    )

    assert code == 0
    assert run_capture.contexts[0].options["opening_state_json"] == str(override.resolve())
    diagnostics = (tmp_path / "out" / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert f"Opening state: {override.resolve()} (CLI override; sidecar {sidecar.resolve()} ignored)" in diagnostics


def test_aggregate_unmatched_opening_state_mapping_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    definition = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        supports_opening_state=True,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(definition))
    (tmp_path / "kraken.csv").write_text("x\n", encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text("{}\n", encoding="utf-8")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
            "--opening-state-json",
            f"missing.csv={state_file}",
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 2
    assert "did not match any detected stateful input" in stdout
    assert not run_capture.contexts


def test_aggregate_mode_continues_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    success = _make_fake_definition(alias="coinbase", group="crypto", tmp_path=tmp_path, run_capture=run_capture)

    def failing_run(_: AnalyzerRunContext) -> TaxAnalysisResult:
        raise RuntimeError("boom")

    failing = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    failing = AnalyzerDefinition(
        alias=failing.alias,
        group=failing.group,
        aliases=failing.aliases,
        description=failing.description,
        default_output_dir=failing.default_output_dir,
        input_suffixes=failing.input_suffixes,
        detection_token_sets=failing.detection_token_sets,
        add_arguments=failing.add_arguments,
        build_options=failing.build_options,
        run=failing_run,
    )
    registry = _make_registry(success, failing)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    (tmp_path / "coinbase.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "kraken.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--analyzer-input",
            f"coinbase={tmp_path / 'coinbase.csv'}",
            "--analyzer-input",
            f"kraken={tmp_path / 'kraken.csv'}",
        ]
    )

    assert code == 2
    stdout = capsys.readouterr().out
    assert "STATUS: ERROR" in stdout
    assert "Diagnostics:" in stdout
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "❌ Статус: ГРЕШКА" in report
    assert "възникна проблем при обработката с анализатора kraken" in report
    assert "boom" not in report
    assert "Per-analyzer status\ncoinbase\n- OK" in diagnostics
    assert "kraken\n- ERROR" in diagnostics
    assert "reason: generic analyzer error" in diagnostics
    assert "[ERROR] [kraken] GENERIC_ANALYZER_ERROR" in diagnostics
    assert "boom" in diagnostics
    assert "code: -" not in diagnostics


def test_known_missing_columns_error_is_actionable_in_main_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def failing_run(_: AnalyzerRunContext) -> TaxAnalysisResult:
        raise RuntimeError("missing required columns: ['User ID', 'Account', 'Operation']")

    failing = _make_fake_definition(
        alias="binance_futures",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
    )
    failing = AnalyzerDefinition(
        alias=failing.alias,
        group=failing.group,
        aliases=failing.aliases,
        description=failing.description,
        default_output_dir=failing.default_output_dir,
        input_suffixes=failing.input_suffixes,
        detection_token_sets=failing.detection_token_sets,
        add_arguments=failing.add_arguments,
        build_options=failing.build_options,
        run=failing_run,
    )
    registry = _make_registry(failing)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    (tmp_path / "binance_futures.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--analyzer-input",
            f"binance_futures={tmp_path / 'binance_futures.csv'}",
        ]
    )

    assert code == 2
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "няма задължителни колони" in report
    assert "- User ID" in report
    assert "- Account" in report
    assert "- Operation" in report
    assert "missing required columns" not in report
    assert "missing required columns" in diagnostics
    assert "missing columns:\n  - User ID\n  - Account\n  - Operation" in diagnostics
    assert "columns:" not in diagnostics.replace("missing columns:", "")
    assert "params:" not in diagnostics


def test_manual_review_rows_are_excluded_from_totals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    taxable = _make_fake_definition(
        alias="coinbase",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="5082",
                values={
                    "sale_value_eur": Decimal("5"),
                    "acquisition_value_eur": Decimal("2"),
                    "profit_eur": Decimal("3"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 1,
                    "net_result_eur": Decimal("3"),
                },
            )
        ],
    )
    review_only = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        appendices=[],
        diagnostics=[
            AnalysisDiagnostic(
                severity="MANUAL_REVIEW",
                message="manual row excluded",
                analyzer_alias="kraken",
            )
        ],
    )
    registry = _make_registry(taxable, review_only)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    (tmp_path / "coinbase.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "kraken.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--analyzer-input",
            f"coinbase={tmp_path / 'coinbase.csv'}",
            "--analyzer-input",
            f"kraken={tmp_path / 'kraken.csv'}",
        ]
    )

    assert code == 0
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "🔎 Статус: ИЗИСКВА РЪЧЕН ПРЕГЛЕД" in report
    assert "  Продажна цена: 5.00 EUR" in report
    assert "Kraken: има ред, изключен от декларационните суми" in report
    assert report.count("има ред, изключен от декларационните суми") == 1
    assert "manual row excluded" not in report
    assert "manual row excluded" in diagnostics


def test_render_aggregated_report_snapshot() -> None:
    input_path = Path("/tmp/coinbase.csv")
    output_path = Path("/tmp/coinbase.txt")
    result = TaxAnalysisResult(
        analyzer_alias="coinbase",
        input_path=input_path,
        tax_year=2025,
        output_paths={"declaration_txt": output_path},
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="5082",
                values={
                    "sale_value_eur": Decimal("11"),
                    "acquisition_value_eur": Decimal("9"),
                    "profit_eur": Decimal("2"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 2,
                    "net_result_eur": Decimal("2"),
                },
            )
        ],
        diagnostics=[],
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"coinbase": [input_path]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    assert "Приложение 5" in rendered
    assert "- Код 5082" in rendered
    assert "  Продажна цена: 11.00 EUR" in rendered
    assert "------------------------------ Technical Details ------------------------------" in rendered
    assert "- global status: OK" in rendered
    assert f"declaration: {output_path.resolve()}" in rendered


def test_render_aggregated_report_suppresses_zero_only_appendix_sections() -> None:
    result = TaxAnalysisResult(
        analyzer_alias="coinbase",
        input_path=Path("/tmp/coinbase.csv"),
        tax_year=2025,
        output_paths={"declaration_txt": Path("/tmp/coinbase.txt")},
        appendices=[],
        diagnostics=[
            AnalysisDiagnostic(
                severity="WARNING",
                message="fx fallback used",
                analyzer_alias="coinbase",
            )
        ],
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"coinbase": [Path("/tmp/coinbase.csv")]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
    )

    assert "Warnings/Errors summary" not in rendered
    assert "reason: fx fallback used" in rendered
    assert "Приложение 5" not in rendered
    assert "Приложение 13" not in rendered
    assert "Приложение 6" not in rendered
    assert "Приложение 8" not in rendered
    assert "Приложение 9" not in rendered


def test_cli_rejects_invalid_display_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="ibkr", group="broker", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(fake)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)

    input_file = tmp_path / "ibkr.csv"
    input_file.write_text("x\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        report_analyzer.main(
            [
                "ibkr",
                "--input",
                str(input_file),
                "--tax-year",
                "2025",
                "--display-currency",
                "USD",
            ]
        )


def test_render_aggregated_report_converts_to_bgn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.shared.rendering.display_currency.convert_amount",
        lambda amount, source_symbol, target_symbol, on_date, cache_dir=None: Decimal("1.95583"),
    )
    result = TaxAnalysisResult(
        analyzer_alias="coinbase",
        input_path=Path("/tmp/coinbase.csv"),
        tax_year=2025,
        output_paths={"declaration_txt": Path("/tmp/coinbase.txt")},
        appendices=[
            AppendixRecord(
                appendix="5",
                table="2",
                code="5082",
                values={
                    "sale_value_eur": Decimal("100"),
                    "acquisition_value_eur": Decimal("90"),
                    "profit_eur": Decimal("10"),
                    "loss_eur": Decimal("0"),
                    "trade_count": 1,
                    "net_result_eur": Decimal("10"),
                },
            )
        ],
        diagnostics=[],
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"coinbase": [Path("/tmp/coinbase.csv")]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
        display_currency="BGN",
    )
    assert "Продажна цена: 195.58 BGN" in rendered
    assert "Цена на придобиване: 176.02 BGN" in rendered
    assert "- Display currency: BGN" in rendered


def test_render_aggregated_report_appendix8_part1_includes_tax_year_end_acquisition_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.shared.rendering.display_currency.convert_amount",
        lambda amount, source_symbol, target_symbol, on_date, cache_dir=None: Decimal("1.95583"),
    )
    result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=Path("/tmp/ibkr.csv"),
        tax_year=2025,
        output_paths={"declaration_txt": Path("/tmp/ibkr.txt")},
        appendices=[
            AppendixRecord(
                appendix="8",
                part="I",
                values={
                    "asset_type": "Акции",
                    "country": "Германия",
                    "currency": "EUR",
                    "quantity": Decimal("49.2652"),
                    "acquisition_native": Decimal("4329.01"),
                    "acquisition_eur": Decimal("4329.01"),
                },
            )
        ],
        diagnostics=[],
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"ibkr": [Path("/tmp/ibkr.csv")]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
        display_currency="BGN",
    )
    assert "Дата и година на придобиване: 31.12.2025" in rendered
    assert "Обща цена на придобиване в съответната валута: 4329.01 EUR" in rendered
    assert "В BGN: 8466.81 BGN" in rendered
    assert "Забележка:" in rendered
    assert "Данните в Приложение 8, Част I са декларативни." in rendered
    assert "Не се изисква прикачване на файл към декларацията." in rendered
    assert "Запазете отчети (напр. broker statements) за целите на евентуална проверка от НАП." in rendered


def test_render_aggregated_report_appendix9_keeps_document_ref_empty_when_missing() -> None:
    result = TaxAnalysisResult(
        analyzer_alias="ibkr",
        input_path=Path("/tmp/ibkr.csv"),
        tax_year=2025,
        output_paths={"declaration_txt": Path("/tmp/ibkr.txt")},
        appendices=[
            AppendixRecord(
                appendix="9",
                part="II",
                code="603",
                values={
                    "country": "Ирландия",
                    "gross_income_eur": Decimal("10.20"),
                    "tax_base_eur": Decimal("10.20"),
                    "foreign_tax_eur": Decimal("2.05"),
                    "allowable_credit_eur": Decimal("1.02"),
                    "recognized_credit_eur": Decimal("1.02"),
                    "document_ref": "",
                },
            )
        ],
        diagnostics=[],
    )
    rendered = render_aggregated_report(
        tax_year=2025,
        detected_inputs={"ibkr": [Path("/tmp/ibkr.csv")]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    assert "№ и дата на документа за дохода и съответния данък: " in rendered
    assert "№ и дата на документа за дохода и съответния данък: -" not in rendered


def test_aggregate_mode_generates_spb8_template_from_detected_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    registry = _make_registry(fake)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 0
    assert "STATUS: MANUAL CHECK REQUIRED" in stdout
    assert "SPB-8 input file:" in stdout
    template = out_dir / "spb8-input-file.csv"
    assert template.read_text(encoding="utf-8").splitlines() == [
        "account name,platform,type,country,ISIN,currency,start amount,end amount",
        "kraken_report,kraken,03,Ирландия,-,EUR,,",
    ]
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "Третирането на крипто активи за СПБ-8" not in report
    assert "Използвана интерпретация за този отчет: крипто платформите са включени" in report
    assert "Какво трябва да направите" in report
    assert report.count("СПБ-8: липсват начални/крайни стойности") == 1
    assert "kraken / Ирландия / EUR / тип 03" in report
    assert "Тип на вземането" not in report
    assert "Забележки за СПБ-8" not in report
    assert "Бележки и допускания" not in report
    assert "СПБ-8\n-" in report
    assert "Попълнете липсващите стойности в генерирания SPB-8 input файл:" in report
    assert str(template) in report
    assert "Стартирайте отново с --spb8-input-file <path>" in report
    assert 'ако името съдържа "spb8", файлът ще бъде разпознат автоматично' in report
    assert "SPB-8 input file was not provided" not in report


def _write_spb8_input(path: Path, *, platform: str = "kraken", start: str = "1000", end: str = "2000") -> None:
    path.write_text(
        "\n".join(
            [
                "account name,platform,type,country,ISIN,currency,start amount,end amount",
                f"{platform} account,{platform},03,Ирландия,-,EUR,{start},{end}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_aggregate_mode_auto_detects_spb8_input_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")
    _write_spb8_input(tmp_path / "spb8-input-file.csv")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 0
    assert "SPB-8 input file:" not in stdout
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "Размер в началото на отчетната година (в хиляди валутни единици): 1.00" in report
    assert "Размер в края на отчетната година (в хиляди валутни единици): 2.00" in report
    assert (
        f"{tmp_path / 'spb8-input-file.csv'} -> spb8-input "
        "(auto-detected from filename tokens)"
    ) in diagnostics
    assert "spb8-input-file.csv: no analyzer alias matched" not in diagnostics


def test_aggregate_mode_auto_detects_spb8_input_file_case_insensitively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")
    _write_spb8_input(tmp_path / "SPB8_INPUT_FILE.CSV")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )

    assert code == 0
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "Размер в началото на отчетната година (в хиляди валутни единици): 1.00" in report


def test_aggregate_mode_spb8_auto_detection_respects_include_pattern(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "[tax-analyzer] kraken_report.csv").write_text("x\n", encoding="utf-8")
    _write_spb8_input(tmp_path / "spb8-input-file.csv")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--include-pattern",
            "*[[]tax-analyzer[]]*",
            "--output-dir",
            str(out_dir),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 0
    assert "SPB-8 input file:" in stdout
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "spb8-input-file.csv -> spb8-input" not in diagnostics
    assert "spb8-input-file.csv: does not match include-pattern" in diagnostics
    assert "СПБ-8: липсват начални/крайни стойности" in report


def test_aggregate_mode_explicit_spb8_input_file_wins_over_auto_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")
    _write_spb8_input(tmp_path / "spb8-auto.csv", start="1000", end="2000")
    explicit = tmp_path / "manual.csv"
    _write_spb8_input(explicit, start="3000", end="4000")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--spb8-input-file",
            str(explicit),
        ]
    )

    assert code == 0
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "Размер в началото на отчетната година (в хиляди валутни единици): 3.00" in report
    assert "Размер в края на отчетната година (в хиляди валутни единици): 4.00" in report
    assert "Размер в началото на отчетната година (в хиляди валутни единици): 1.00" not in report
    assert f"{explicit} -> spb8-input (explicit --spb8-input-file)" in diagnostics
    assert "SPB-8 candidate ignored because --spb8-input-file selected" in diagnostics
    assert str(tmp_path / "spb8-auto.csv") in diagnostics


def test_aggregate_mode_fails_on_multiple_auto_detected_spb8_input_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")
    _write_spb8_input(tmp_path / "spb8-one.csv")
    _write_spb8_input(tmp_path / "SPB8-two.csv")

    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 2
    assert "Multiple SPB-8 input CSV files were detected" in stdout
    assert "--spb8-input-file" in stdout
    assert "spb8-one.csv" in stdout
    assert "SPB8-two.csv" in stdout


def test_aggregate_mode_auto_detected_spb8_parse_error_is_visible_in_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(alias="kraken", group="crypto", tmp_path=tmp_path, run_capture=run_capture)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "spb8-input-file.csv").write_text("not,the,expected,header\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )

    assert code == 2
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert (
        f"{tmp_path / 'spb8-input-file.csv'} -> spb8-input "
        "(auto-detected from filename tokens)"
    ) in diagnostics
    assert "[ERROR] [spb8]" in diagnostics


def test_aggregate_mode_attaches_individual_technical_details_as_structured_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])
    debug_path = tmp_path / "debug" / "state-debug.json"
    raw_report_text = "\n".join(
        [
            "ok",
            "",
            TECHNICAL_DETAILS_SEPARATOR,
            "",
            "Audit Data",
            "- processed rows: 7",
            f"- debug artifact: {debug_path}",
            "",
        ]
    )
    fake = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        raw_report_text=raw_report_text,
    )
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: _make_registry(fake))
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )

    assert code == 0
    main = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    diagnostics = (out_dir / "aggregated_tax_report_2025.diagnostics.txt").read_text(encoding="utf-8")
    assert "Complete individual diagnostics" not in diagnostics
    assert "Tax calculation summary" in diagnostics
    assert "- processed rows: 7" in diagnostics
    assert "Debug artifacts" in diagnostics
    assert f"- debug artifact: {debug_path}" in diagnostics
    assert "- processed rows: 7" not in main
    assert str(debug_path) not in main


def test_aggregate_mode_generates_spb8_template_rows_for_ibkr_analyzer_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        out = context.output_dir / "ibkr_declaration.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"ok\n\n{TECHNICAL_DETAILS_SEPARATOR}\n\nAudit Data\n", encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias="ibkr",
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": out},
            appendices=[],
            diagnostics=[],
            spb8_rows=[
                SPB8Row(
                    account_name=context.input_path.stem,
                    platform="ibkr",
                    type_code="03",
                    country="Ирландия",
                    currency="EUR",
                    start_nav=Decimal("1000"),
                    end_nav=Decimal("2000"),
                )
            ],
        )

    definition = AnalyzerDefinition(
        alias="ibkr",
        group="broker",
        aliases=(),
        description="ibkr fake analyzer",
        default_output_dir=tmp_path / "ibkr",
        input_suffixes=(".csv",),
        detection_token_sets=(("ibkr",),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )
    registry = _make_registry(definition)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    for account in ("U1", "U2", "U3"):
        (tmp_path / f"ibkr_{account}.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 3
    assert (out_dir / "spb8-input-file.csv").read_text(encoding="utf-8").splitlines() == [
        "account name,platform,type,country,ISIN,currency,start amount,end amount",
        "ibkr_U1,ibkr,03,Ирландия,-,EUR,1000,2000",
        "ibkr_U2,ibkr,03,Ирландия,-,EUR,1000,2000",
        "ibkr_U3,ibkr,03,Ирландия,-,EUR,1000,2000",
    ]
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "СПБ-8" in report
    assert "Забележки за СПБ-8" not in report
    assert "Бележки и допускания" not in report
    assert TECHNICAL_DETAILS_SEPARATOR not in report
    assert "Размер в началото на отчетната година (в хиляди валутни единици): 3.00" in report
    assert "Размер в края на отчетната година (в хиляди валутни единици): 6.00" in report
    declaration = next((out_dir / "ibkr").rglob("ibkr_declaration.txt"))
    declaration_text = declaration.read_text(encoding="utf-8")
    assert "СПБ-8" in declaration_text
    assert TECHNICAL_DETAILS_SEPARATOR not in declaration_text


def test_no_spb8_disables_template_and_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])
    fake = _make_fake_definition(
        alias="kraken",
        group="crypto",
        tmp_path=tmp_path,
        run_capture=run_capture,
        spb8_rows=[
            SPB8Row("kraken", "kraken", "03", "Ирландия", "EUR", Decimal("1000"), Decimal("2000")),
        ],
        spb8_notes=["SPB-8 analyzer note that must be suppressed"],
    )
    registry = _make_registry(fake)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--no-spb8",
            "--spb8-input-file",
            str(tmp_path / "missing-spb8.csv"),
        ]
    )
    stdout = capsys.readouterr().out

    assert code == 0
    assert "SPB-8" not in stdout
    assert "СПБ-8" not in stdout
    assert not (out_dir / "spb8-input-file.csv").exists()
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "SPB-8" not in report
    assert "СПБ-8" not in report
    declaration = next((out_dir / "kraken").rglob("*_declaration.txt"))
    declaration_text = declaration.read_text(encoding="utf-8")
    assert "SPB-8" not in declaration_text
    assert "СПБ-8" not in declaration_text


def test_spb8_input_file_renders_aggregate_and_individual_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        out = context.output_dir / "kraken_declaration.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok\n", encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias="kraken",
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": out},
            appendices=[],
            diagnostics=[],
            spb8_rows=[
                SPB8Row(
                    account_name="ignored by csv",
                    platform="kraken",
                    type_code="03",
                    country="Ирландия",
                    currency="EUR",
                    start_nav=Decimal("999"),
                    end_nav=Decimal("999"),
                )
            ],
        )

    definition = AnalyzerDefinition(
        alias="kraken",
        group="crypto",
        aliases=(),
        description="kraken fake analyzer",
        default_output_dir=tmp_path / "kraken",
        input_suffixes=(".csv",),
        detection_token_sets=(("kraken",),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )
    registry = _make_registry(definition)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    (tmp_path / "kraken_report.csv").write_text("x\n", encoding="utf-8")
    spb8_input = tmp_path / "spb8.csv"
    spb8_input.write_text(
        "account_name,platform,type,country,ISIN,currency,start_amount,end_amount\n"
        "kraken,kraken,03,Ireland,-,EUR,1000,2000\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--spb8-input-file",
            str(spb8_input),
        ]
    )

    assert code == 0
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "СПБ-8" in report
    assert "Размер в началото на отчетната година (в хиляди валутни единици): 1.00" in report
    assert "Размер в края на отчетната година (в хиляди валутни единици): 2.00" in report
    assert "Забележки за СПБ-8" not in report
    assert "Бележки и допускания" not in report
    declaration = next((out_dir / "kraken").rglob("kraken_declaration.txt"))
    declaration_text = declaration.read_text(encoding="utf-8")
    assert "СПБ-8" in declaration_text
    assert "Забележки за СПБ-8" not in declaration_text
    assert "Бележки и допускания" not in declaration_text
    assert declaration_text.index("Диагностика") > declaration_text.index("Тип на вземането")
    assert "СПБ-8\n-" in declaration_text


def test_spb8_input_type04_override_falls_back_to_analyzer_and_resolves_corporate_action_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_capture = _RunCapture(contexts=[])

    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        out = context.output_dir / "ibkr_declaration.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok\n", encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias="ibkr",
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": out},
            appendices=[],
            diagnostics=[_ibkr_corporate_actions_diagnostic()],
            spb8_rows=[
                SPB8Row(
                    account_name="ibkr analyzer",
                    platform="ibkr",
                    type_code="04",
                    country="Ирландия",
                    currency="",
                    start_nav=None,
                    end_nav=Decimal("15"),
                    isin="IE00BK5BQT80",
                )
            ],
            spb8_corporate_actions_present=True,
        )

    definition = AnalyzerDefinition(
        alias="ibkr",
        group="broker",
        aliases=(),
        description="ibkr fake analyzer",
        default_output_dir=tmp_path / "ibkr",
        input_suffixes=(".csv",),
        detection_token_sets=(("ibkr",),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )
    registry = _make_registry(definition)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    (tmp_path / "ibkr_report.csv").write_text("x\n", encoding="utf-8")
    spb8_input = tmp_path / "spb8.csv"
    spb8_input.write_text(
        "account name,platform,type,country,ISIN,currency,start amount,end amount\n"
        "ibkr manual,ibkr,04,Ireland,IE00BK5BQT80,-,12,\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
            "--spb8-input-file",
            str(spb8_input),
        ]
    )

    assert code == 0
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "ISIN: IE00BK5BQT80" in report
    assert "Размер в началото на отчетната година: 12" in report
    assert "Размер в края на отчетната година: 15" in report
    assert "IBKR: открити са Corporate Actions в Activity Statement CSV" in report
    assert "корпоративните събития може да влияят както на СПБ-8 количествата" in report
    assert "Откритите IBKR Corporate Actions може да влияят на коректността на СПБ-8 количествата" in report
    assert "Попълнете липсващите начални количества" not in report
    assert report.count("IBKR: открити са Corporate Actions в Activity Statement CSV") == 1
    assert (out_dir / "spb8-input-file.csv").read_text(encoding="utf-8").splitlines() == [
        "account name,platform,type,country,ISIN,currency,start amount,end amount",
        "ibkr manual,ibkr,04,Ирландия,IE00BK5BQT80,-,12,15",
    ]


def test_spb8_corporate_actions_with_missing_start_quantity_warns_for_manual_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_capture = _RunCapture(contexts=[])

    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        run_capture.contexts.append(context)
        out = context.output_dir / "ibkr_declaration.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok\n", encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias="ibkr",
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": out},
            appendices=[],
            diagnostics=[_ibkr_corporate_actions_diagnostic()],
            spb8_rows=[
                SPB8Row("ibkr analyzer", "ibkr", "04", "Ирландия", "", None, Decimal("15"), isin="IE00BK5BQT80")
            ],
            spb8_corporate_actions_present=True,
        )

    definition = AnalyzerDefinition(
        alias="ibkr",
        group="broker",
        aliases=(),
        description="ibkr fake analyzer",
        default_output_dir=tmp_path / "ibkr",
        input_suffixes=(".csv",),
        detection_token_sets=(("ibkr",),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )
    registry = _make_registry(definition)
    monkeypatch.setattr(report_analyzer, "discover_analyzer_registry", lambda: registry)
    (tmp_path / "ibkr_report.csv").write_text("x\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    code = report_analyzer.main(
        [
            "--input-dir",
            str(tmp_path),
            "--tax-year",
            "2025",
            "--output-dir",
            str(out_dir),
        ]
    )

    stdout = capsys.readouterr().out

    assert code == 0
    assert "STATUS: MANUAL CHECK REQUIRED" in stdout
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "Тип на вземането" not in report
    assert "Попълнете липсващите начални количества" not in report
    assert "IBKR: открити са Corporate Actions в Activity Statement CSV" in report
    assert "ISIN IE00BK5BQT80" in report
    assert report.count("СПБ-8: липсват начални/крайни стойности") == 1
    assert report.count("Попълнете липсващите стойности в генерирания SPB-8 input файл:") == 1
    assert "Попълнете липсващите стойности в генерирания SPB-8 input файл:" in report
    assert str(out_dir / "spb8-input-file.csv") in report
    assert "Стартирайте отново с --spb8-input-file <path>" in report
    assert 'ако името съдържа "spb8", файлът ще бъде разпознат автоматично' in report
    assert (out_dir / "spb8-input-file.csv").read_text(encoding="utf-8").splitlines() == [
        "account name,platform,type,country,ISIN,currency,start amount,end amount",
        "ibkr analyzer,ibkr,04,Ирландия,IE00BK5BQT80,-,,15",
    ]
