from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

import report_analyzer
from integrations.crypto.shared.crypto_ir_models import IrAnalysisSummary
from integrations.fund.shared.fund_ir_models import FundAnalysisSummary
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
from integrations.shared.result_builders import (
    build_binance_futures_result,
    build_crypto_result,
    build_fund_result,
    build_p2p_result,
)
from integrations.shared.reporting import render_action_items, render_diagnostics_report, render_main_report
from integrations.shared.rendering.common import TECHNICAL_DETAILS_SEPARATOR
from integrations.shared.spb8 import SPB8Row
from integrations.p2p.shared.appendix6_models import P2PAppendix6Result


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
    spb8_rows: list[SPB8Row] | None = None,
    spb8_notes: list[str] | None = None,
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
            spb8_rows=list(spb8_rows or []),
            spb8_notes=list(spb8_notes or []),
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
    output_path = tmp_path / "out" / "ibkr_declaration.txt"
    diagnostics_path = tmp_path / "out" / "ibkr_declaration.diagnostics.txt"
    assert output_path.exists()
    assert diagnostics_path.exists()
    declaration = output_path.read_text(encoding="utf-8")
    diagnostics = diagnostics_path.read_text(encoding="utf-8")
    assert "!!! СТАТУС: УСПЕШЕН !!!" in declaration
    assert "Данъчна година: 2025" in declaration
    assert "Бележки и допускания" not in declaration
    assert "Изчисления и визуализация" in declaration
    assert "Диагностика" in declaration
    assert TECHNICAL_DETAILS_SEPARATOR not in declaration
    assert "Technical Details" in diagnostics


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
    assert report.startswith("!!! СТАТУС: ИЗИСКВА РЪЧЕН ПРЕГЛЕД !!!\nДанъчна година: 2025")
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
    assert "Diagnostics\n[ERROR] [binance_futures] MISSING_REQUIRED_COLUMNS" in rendered
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
    assert rendered.index("[ERROR]") < rendered.index("[MANUAL_REVIEW]") < rendered.index("[WARNING]")


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
    assert "rows:\n  -\n    execution_exchange: IDEALFX" in rendered
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
    assert "Coinbase: има 1 предупреждения, които изискват преглед." in main
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
    assert "!!! СТАТУС: ГРЕШКА !!!" in report
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
    assert "!!! СТАТУС: ИЗИСКВА РЪЧЕН ПРЕГЛЕД !!!" in report
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
    assert "СПБ-8: липсват начални/крайни стойности" in report
    assert "kraken / Ирландия / EUR / тип 03" in report
    assert "Тип на вземането" not in report
    assert "Забележки за СПБ-8" not in report
    assert "Бележки и допускания" not in report
    assert "СПБ-8\n-" in report
    assert "Стартирайте отново с --spb8-input-file <path>" in report
    assert "SPB-8 input file was not provided" not in report


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
            diagnostics=[],
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
    assert "Открити са корпоративни събития" in report
    assert "Попълнете липсващите начални количества" not in report
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
            diagnostics=[],
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
    assert "Попълнете липсващите начални количества" in report
    assert "ISIN IE00BK5BQT80" in report
    assert "Стартирайте отново с --spb8-input-file <path>" in report
    assert (out_dir / "spb8-input-file.csv").read_text(encoding="utf-8").splitlines() == [
        "account name,platform,type,country,ISIN,currency,start amount,end amount",
        "ibkr analyzer,ibkr,04,Ирландия,IE00BK5BQT80,-,,15",
    ]
