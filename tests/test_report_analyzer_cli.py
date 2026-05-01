from __future__ import annotations

import importlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

import report_analyzer
from integrations.shared.aggregation import render_aggregated_report
from integrations.shared.autodetect import InputDetectionError, detect_analyzer_inputs
from integrations.shared.contracts import (
    AnalysisDiagnostic,
    AnalyzerDefinition,
    AnalyzerRunContext,
    AppendixRecord,
    TaxAnalysisResult,
)
from integrations.shared.cli_helpers import CliMode
from integrations.shared.registry import AnalyzerRegistry


@dataclass(slots=True)
class _RunCapture:
    contexts: list[AnalyzerRunContext]


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
    aggregate_mode_option_name: str | None = None,
) -> AnalyzerDefinition:
    def add_arguments(parser, mode: CliMode):  # noqa: ANN001
        if mode == "single":
            parser.add_argument("--mode", default="single_default")
            return
        if aggregate_mode_option_name:
            parser.add_argument(f"--{aggregate_mode_option_name}", type=str)

    def build_options(args, mode: CliMode, group_options):  # noqa: ANN001
        if mode == "single":
            return {"mode": args.mode}
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
        output_path.write_text("ok\n", encoding="utf-8")
        return TaxAnalysisResult(
            analyzer_alias=alias,
            input_path=context.input_path,
            tax_year=context.tax_year,
            output_paths={"declaration_txt": output_path},
            appendices=list(appendices or []),
            diagnostics=list(diagnostics or []),
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
    )


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

    detection = detect_analyzer_inputs(
        input_dir=tmp_path,
        include_pattern="*.csv",
        registry=registry,
    )

    assert [path.name for path in detection.detected["coinbase"]] == ["Coinbase Report.csv"]
    assert [path.name for path in detection.detected["kraken"]] == ["kraken_ledger.csv"]
    ignored = {item.path.name: item.reason for item in detection.ignored_items}
    assert "notes.txt" in ignored


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
        aggregate_mode_option_name="afranga-secondary-market-mode",
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
            "--afranga-secondary-market-mode",
            "appendix_5",
        ]
    )

    assert code == 0
    assert len(run_capture.contexts) == 1
    assert run_capture.contexts[0].options["mode"] == "appendix_5"


def test_clean_output_safety_rejects_dangerous_targets() -> None:
    with pytest.raises(InputDetectionError):
        report_analyzer._prepare_output_dir(output_dir=Path("/"), clean_output=True)

    with pytest.raises(InputDetectionError):
        report_analyzer._prepare_output_dir(output_dir=Path.home(), clean_output=True)

    with pytest.raises(InputDetectionError):
        report_analyzer._prepare_output_dir(output_dir=report_analyzer.PROJECT_ROOT, clean_output=True)


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


def test_aggregate_mode_continues_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    report = (out_dir / "aggregated_tax_report_2025.txt").read_text(encoding="utf-8")
    assert "coinbase: OK" in report
    assert "kraken: ERROR" in report
    assert "boom" in report


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
    assert "- global status: NEEDS_REVIEW" in report
    assert "  Продажна цена: 5.00 EUR" in report
    assert "manual row excluded" in report


def test_render_aggregated_report_snapshot() -> None:
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
        detected_inputs={"coinbase": [Path("/tmp/coinbase.csv")]},
        ignored_inputs=[],
        analyzer_results=[result],
        analyzer_errors={},
    )
    assert "Приложение 5" in rendered
    assert "- Код 5082" in rendered
    assert "  Продажна цена: 11.00 EUR" in rendered
    assert "------------------------------ Technical Details ------------------------------" in rendered
    assert "- global status: OK" in rendered
    assert "declaration: file:///private/tmp/coinbase.txt" in rendered


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

    assert "Warnings/Errors summary" in rendered
    assert "fx fallback used" in rendered
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
