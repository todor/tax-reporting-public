from __future__ import annotations

import argparse
import fnmatch
import logging
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from config import OUTPUT_DIR, PROJECT_ROOT
from integrations.shared.aggregation import render_aggregated_report
from integrations.shared.autodetect import (
    DetectionItem,
    InputDetectionError,
    detect_analyzer_inputs,
    parse_analyzer_input_overrides,
)
from integrations.shared.contracts import (
    AnalysisDiagnostic,
    AnalyzerRunContext,
    AnalyzerStatus,
    TaxAnalysisResult,
)
from integrations.shared.reporting import (
    classify_exception,
    format_path,
    print_operational_summary,
    write_standardized_reports,
)
from integrations.shared.registry import AnalyzerRegistryError, discover_analyzer_registry
from integrations.shared.rendering.common import TECHNICAL_DETAILS_SEPARATOR
from integrations.shared.rendering.display_currency import DisplayCurrencyError
from integrations.shared.spb8 import (
    SPB8Error,
    SPB8Row,
    filter_rows_for_options,
    manual_input_template_rows_for_platform,
    merge_external_platform_rows,
    missing_spb8_value_notes,
    read_spb8_csv,
    render_spb8_section,
    rows_by_platform,
    write_spb8_csv,
)
from report_analyzer.registry import list_analyzers

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _validate_clean_output_target(output_dir: Path) -> None:
    normalized = output_dir.expanduser().resolve()
    if str(normalized).strip() == "":
        raise InputDetectionError("refusing to clean empty output path")
    if normalized == Path(normalized.anchor).resolve():
        raise InputDetectionError(f"refusing to clean filesystem root {normalized}")
    if normalized == Path.home().resolve():
        raise InputDetectionError("refusing to clean home directory")
    if normalized == Path.cwd().resolve():
        raise InputDetectionError("refusing to clean current working directory")
    if normalized == PROJECT_ROOT.resolve():
        raise InputDetectionError("refusing to clean repository root directory")


def _prepare_output_dir(*, output_dir: Path, clean_output: bool) -> Path:
    resolved = output_dir.expanduser().resolve()
    if clean_output:
        _validate_clean_output_target(resolved)
        if resolved.exists():
            shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _add_spb8_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-spb8",
        action="store_true",
        help="Disable SPB-8 generation",
    )
    parser.add_argument(
        "--spb8-input-file",
        type=Path,
        help=(
            "External SPB-8 input CSV with platform NAV values. "
            "In aggregate mode this is optional when exactly one *spb8*.csv file exists in --input-dir."
        ),
    )
    parser.add_argument(
        "--spb8-exclude-crypto",
        action="store_true",
        help="Exclude crypto platforms from SPB-8 rows",
    )


def _spb8_enabled(args: argparse.Namespace) -> bool:
    return not bool(getattr(args, "no_spb8", False))


def _append_spb8_to_declaration(
    result: TaxAnalysisResult,
    *,
    rows: list[SPB8Row],
    notes: list[str],
) -> None:
    declaration_path = result.output_paths.get("declaration_txt")
    if declaration_path is None:
        return
    section_lines = render_spb8_section(rows, notes=notes)
    if not section_lines:
        return
    new_block = "\n".join(section_lines)
    current = declaration_path.read_text(encoding="utf-8").rstrip()
    if TECHNICAL_DETAILS_SEPARATOR in current:
        declaration_part, technical_part = current.split(TECHNICAL_DETAILS_SEPARATOR, 1)
        updated = declaration_part.rstrip() + "\n\n" + new_block.rstrip() + "\n\n"
        updated += TECHNICAL_DETAILS_SEPARATOR + technical_part.rstrip() + "\n"
    else:
        updated = current + "\n\n" + new_block.rstrip() + "\n"
    declaration_path.write_text(updated, encoding="utf-8")


def _spb8_rows_from_detected(detected: dict[str, list[Path]]) -> list[SPB8Row]:
    rows: list[SPB8Row] = []
    for alias, paths in sorted(detected.items()):
        for path in paths:
            rows.extend(manual_input_template_rows_for_platform(platform=alias, account_name=path.stem))
    return rows


def _load_spb8_input(path: Path) -> list[SPB8Row]:
    if not path.expanduser().exists():
        raise InputDetectionError(f"SPB-8 input file does not exist: {path}")
    try:
        return read_spb8_csv(path)
    except SPB8Error as exc:
        raise InputDetectionError(str(exc)) from exc


def _detect_spb8_input_candidates(input_dir: Path, *, include_pattern: str | None) -> list[Path]:
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".csv"
        and "spb8" in path.name.lower()
        and (include_pattern is None or fnmatch.fnmatch(path.name, include_pattern))
    )


def _detect_spb8_input_file(input_dir: Path, *, include_pattern: str | None) -> Path | None:
    candidates = _detect_spb8_input_candidates(input_dir, include_pattern=include_pattern)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].expanduser().resolve()
    formatted = "\n".join(f"- {format_path(path)}" for path in candidates)
    raise InputDetectionError(
        "Multiple SPB-8 input CSV files were detected in --input-dir:\n"
        f"{formatted}\n"
        "Pass --spb8-input-file explicitly to choose which file to use."
    )


def _write_spb8_template(*, output_dir: Path, rows: list[SPB8Row]) -> Path:
    output_path = output_dir / "spb8-input-file.csv"
    write_spb8_csv(output_path, rows)
    return output_path


def _external_rows_for_result(result: TaxAnalysisResult, external_rows: list[SPB8Row]) -> list[SPB8Row]:
    return rows_by_platform(external_rows).get(result.analyzer_alias, [])


def _merged_spb8_rows_for_result(result: TaxAnalysisResult, external_rows: list[SPB8Row]) -> list[SPB8Row]:
    if not external_rows:
        return result.spb8_rows
    return merge_external_platform_rows(result.spb8_rows, _external_rows_for_result(result, external_rows))


def _corporate_actions_notes(result: TaxAnalysisResult, rows: list[SPB8Row]) -> list[str]:
    if not result.spb8_corporate_actions_present:
        return []
    has_missing_start_quantity = any(
        row.type_code == "04" and not row.is_bulgaria and row.end_nav is not None and row.start_nav is None
        for row in rows
    )
    if has_missing_start_quantity:
        return [
            "\n".join(
                [
                    "⚠️ Открити са корпоративни събития (Corporate Actions) в IBKR Activity Statement CSV.",
                    "Корпоративните събития все още не се обработват автоматично от анализатора.",
                    "Началните количества за СПБ-8 може да не могат да бъдат изчислени надеждно.",
                    "Попълнете липсващите начални количества в SPB-8 input файла за съответните ISIN-и.",
                    'Прегледайте секцията "Corporate Actions" ръчно, защото тя може да има влияние и върху данъците.',
                ]
            )
        ]
    return [
        "\n".join(
            [
                "⚠️ Открити са корпоративни събития (Corporate Actions) в IBKR Activity Statement CSV.",
                "Корпоративните събития все още не се обработват автоматично от анализатора.",
                'Прегледайте секцията "Corporate Actions" ръчно, защото тя може да има влияние и върху данъците.',
            ]
        )
    ]


def _spb8_notes_for_result(
    result: TaxAnalysisResult,
    *,
    rows: list[SPB8Row],
    input_file_provided: bool,
    option_notes: list[str],
) -> list[str]:
    _ = input_file_provided
    return (
        result.spb8_notes
        + _corporate_actions_notes(result, rows)
        + option_notes
    )


def _dedupe_notes(notes: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for note in notes:
        if note in seen:
            continue
        seen.add(note)
        result.append(note)
    return result


def _spb8_row_has_zero_rendered_end_value(row: SPB8Row) -> bool:
    if row.end_nav is None:
        return False
    end_value = row.end_nav if row.type_code == "04" else (row.end_nav / Decimal("1000")).quantize(Decimal("0.01"))
    return end_value == Decimal("0")


def _spb8_missing_diagnostics(rows: list[SPB8Row], *, analyzer_alias: str) -> list[AnalysisDiagnostic]:
    missing_by_row: dict[tuple[str, ...], dict[str, object]] = {}
    for row in rows:
        if row.is_bulgaria:
            continue
        if row.type_code != "04" and _spb8_row_has_zero_rendered_end_value(row):
            continue
        missing_fields: list[str] = []
        if row.start_nav is None:
            missing_fields.append("start_amount")
        if row.end_nav is None:
            missing_fields.append("end_amount")
        if not missing_fields:
            continue
        if row.type_code == "04":
            key = (row.platform, row.type_code, row.isin.strip().upper())
            item = missing_by_row.setdefault(
                key,
                {
                    "platform": row.platform,
                    "type": row.type_code,
                    "isin": row.isin.strip().upper(),
                    "missing": [],
                },
            )
        else:
            key = (
                row.platform,
                row.type_code,
                row.country.strip().casefold(),
                row.currency.strip().upper(),
                row.maturity.strip().casefold(),
            )
            item = missing_by_row.setdefault(
                key,
                {
                    "platform": row.platform,
                    "type": row.type_code,
                    "country": row.country,
                    "currency": row.currency,
                    "missing": [],
                },
            )
        current_missing = item["missing"]
        assert isinstance(current_missing, list)
        for field in missing_fields:
            if field not in current_missing:
                current_missing.append(field)
    missing_values = sorted(
        missing_by_row.values(),
        key=lambda item: (
            str(item.get("platform", "")),
            str(item.get("type", "")),
            str(item.get("country", "")),
            str(item.get("currency", "")),
            str(item.get("isin", "")),
        ),
    )
    if not missing_values:
        return []
    return [
        AnalysisDiagnostic(
            severity="MANUAL_REVIEW",
            message="SPB-8 required values are missing.",
            analyzer_alias=analyzer_alias,
            code="SPB8_MISSING_VALUES",
            params={"missing_values": missing_values},
            technical_message_en=None,
        )
    ]


def _with_report_context(
    diagnostics: list[AnalysisDiagnostic],
    *,
    source_file: Path | None = None,
    report_path: Path | None = None,
) -> list[AnalysisDiagnostic]:
    enriched: list[AnalysisDiagnostic] = []
    for diagnostic in diagnostics:
        params = dict(diagnostic.params)
        if source_file is not None:
            params.setdefault("source_file", format_path(source_file))
        if report_path is not None:
            params.setdefault("report_path", format_path(report_path))
        enriched.append(replace(diagnostic, params=params))
    return enriched


def _all_result_diagnostics(
    results: list[TaxAnalysisResult],
    extra_diagnostics: list[AnalysisDiagnostic],
) -> list[AnalysisDiagnostic]:
    diagnostics: list[AnalysisDiagnostic] = []
    for result in results:
        diagnostics.extend(
            _with_report_context(
                result.diagnostics,
                source_file=result.input_path,
                report_path=result.output_paths.get("declaration_txt"),
            )
        )
    diagnostics.extend(extra_diagnostics)
    return diagnostics


def _global_status_from_results(
    results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
    *,
    spb8_needs_review: bool = False,
) -> AnalyzerStatus:
    statuses: list[AnalyzerStatus] = [result.status for result in results]
    if analyzer_errors:
        statuses.extend(["ERROR"] * sum(len(items) for items in analyzer_errors.values()))
    if any(status == "ERROR" for status in statuses):
        return "ERROR"
    if any(status == "NEEDS_REVIEW" for status in statuses):
        return "NEEDS_REVIEW"
    if spb8_needs_review:
        return "NEEDS_REVIEW"
    if any(status == "WARNING" for status in statuses):
        return "WARNING"
    return "OK"


def _status_with_spb8_review(status: AnalyzerStatus, *, spb8_needs_review: bool) -> AnalyzerStatus:
    if spb8_needs_review and status in {"OK", "WARNING"}:
        return "NEEDS_REVIEW"
    return status


def _validate_tax_year(tax_year: int) -> None:
    if tax_year < 2009 or tax_year > 2100:
        raise InputDetectionError(f"invalid tax year: {tax_year}")


def build_parser() -> argparse.ArgumentParser:
    registry = discover_analyzer_registry()
    parser = argparse.ArgumentParser(prog="tax-reporting")
    parser.set_defaults(_registry=registry)
    parser.add_argument(
        "--list-analyzers",
        action="store_true",
        help="List available analyzers and exit",
    )

    # Aggregate mode arguments (when no subcommand/analyzer alias is provided).
    parser.add_argument("--input-dir", type=Path, help="Input folder with analyzer files")
    parser.add_argument("--include-pattern", type=str, help="Optional glob filter for input files")
    parser.add_argument(
        "--analyzer-input",
        action="append",
        default=[],
        help="Analyzer input override in the form alias=path (repeatable)",
    )
    parser.add_argument("--tax-year", type=int, help="Tax year")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--cache-dir", type=Path, help="Optional shared FX cache dir override")
    parser.add_argument(
        "--display-currency",
        choices=["EUR", "BGN"],
        default="EUR",
        help=(
            "Controls ONLY TXT output rendering. "
            "All calculations and aggregation are performed in EUR. "
            "BGN rendering uses BNB FX service at tax year end."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--clean-output", action="store_true", help="Delete output-dir before processing")
    _add_spb8_arguments(parser)

    parser.add_argument(
        "--p2p-secondary-market-mode",
        type=str,
        default="appendix_6",
        help="Group-level P2P secondary-market mode",
    )

    for definition in registry.definitions():
        definition.add_arguments(parser, "aggregate")

    subparsers = parser.add_subparsers(dest="single_analyzer_alias")
    for definition in registry.definitions():
        alias_parser = subparsers.add_parser(
            definition.alias,
            aliases=list(definition.aliases),
            help=definition.description,
        )
        alias_parser.set_defaults(single_analyzer_alias=definition.alias)
        alias_parser.add_argument("--input", type=Path, required=True, help="Analyzer input file")
        alias_parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
        alias_parser.add_argument(
            "--output-dir",
            type=Path,
            default=definition.default_output_dir,
            help="Output directory",
        )
        alias_parser.add_argument("--log-level", default="INFO")
        alias_parser.add_argument("--cache-dir", type=Path, help="Optional shared FX cache dir override")
        alias_parser.add_argument(
            "--display-currency",
            choices=["EUR", "BGN"],
            default="EUR",
            help=(
                "Controls ONLY TXT output rendering. "
                "All calculations and aggregation are performed in EUR. "
                "BGN rendering uses BNB FX service at tax year end."
            ),
        )
        alias_parser.add_argument("--clean-output", action="store_true", help="Delete output-dir before processing")
        _add_spb8_arguments(alias_parser)
        definition.add_arguments(alias_parser, "single")

    return parser


def _run_single_mode(args: argparse.Namespace) -> int:
    registry = args._registry
    definition = registry.resolve(args.single_analyzer_alias)
    _validate_tax_year(args.tax_year)
    _configure_logging(args.log_level)

    output_dir = _prepare_output_dir(
        output_dir=args.output_dir,
        clean_output=bool(args.clean_output),
    )
    options = definition.build_options(args, "single", {})
    options["display_currency"] = str(args.display_currency)
    options["cache_dir"] = str(args.cache_dir) if args.cache_dir is not None else options.get("cache_dir")
    context = AnalyzerRunContext(
        input_path=args.input.expanduser().resolve(),
        tax_year=args.tax_year,
        output_dir=output_dir,
        log_level=args.log_level,
        options=options,
    )
    try:
        result = definition.run(context)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s", exc)
        fallback_report_path = output_dir / f"{definition.alias}_declaration_{args.tax_year}.txt"
        diagnostic = classify_exception(exc, analyzer_alias=definition.alias, input_path=context.input_path)
        diagnostics = _with_report_context([diagnostic], source_file=context.input_path, report_path=fallback_report_path)
        diagnostics_path = write_standardized_reports(
            main_report_path=fallback_report_path,
            raw_report_text="",
            status="ERROR",
            tax_year=args.tax_year,
            diagnostics=diagnostics,
            diagnostics_title=f"{definition.alias} analyzer failure",
            diagnostics_extra_lines=[
                f"- input_path: {format_path(context.input_path)}",
                f"- output_dir: {format_path(output_dir)}",
            ],
            exception=exc,
        )
        print_operational_summary(
            status="ERROR",
            main_report_path=fallback_report_path,
            diagnostics_path=diagnostics_path,
            diagnostics=diagnostics,
        )
        return 2

    extra_diagnostics: list[AnalysisDiagnostic] = []
    notes: list[str] = []
    if _spb8_enabled(args):
        try:
            external_rows = _load_spb8_input(args.spb8_input_file) if args.spb8_input_file is not None else []
        except Exception as exc:  # noqa: BLE001
            declaration_path = result.output_paths.get("declaration_txt", output_dir / "declaration.txt")
            raw_report_text = declaration_path.read_text(encoding="utf-8") if declaration_path.exists() else ""
            diagnostic = classify_exception(exc, analyzer_alias=result.analyzer_alias, input_path=args.spb8_input_file)
            diagnostics = _with_report_context(
                [*result.diagnostics, diagnostic],
                source_file=context.input_path,
                report_path=declaration_path,
            )
            diagnostics_path = write_standardized_reports(
                main_report_path=declaration_path,
                raw_report_text=raw_report_text,
                status="ERROR",
                tax_year=args.tax_year,
                diagnostics=diagnostics,
                diagnostics_title=f"{result.analyzer_alias} analyzer diagnostics",
                diagnostics_extra_lines=[
                    f"- input_path: {format_path(context.input_path)}",
                    f"- spb8_input_file: {format_path(args.spb8_input_file)}",
                ],
                exception=exc,
            )
            print_operational_summary(
                status="ERROR",
                main_report_path=declaration_path,
                diagnostics_path=diagnostics_path,
                diagnostics=diagnostics,
            )
            return 2
        rows = _merged_spb8_rows_for_result(result, external_rows)
        rows, option_notes = filter_rows_for_options(
            rows,
            enabled=True,
            exclude_crypto=bool(args.spb8_exclude_crypto),
        )
        notes = _spb8_notes_for_result(
            result,
            rows=rows,
            input_file_provided=args.spb8_input_file is not None,
            option_notes=option_notes,
        )
        spb8_needs_review = bool(missing_spb8_value_notes(rows))
        extra_diagnostics.extend(_spb8_missing_diagnostics(rows, analyzer_alias=result.analyzer_alias))
        _append_spb8_to_declaration(result, rows=rows, notes=notes)
    else:
        spb8_needs_review = False

    status = _status_with_spb8_review(result.status, spb8_needs_review=spb8_needs_review)
    declaration_path = result.output_paths.get("declaration_txt")
    if declaration_path is not None:
        raw_report_text = declaration_path.read_text(encoding="utf-8")
        diagnostics = _with_report_context(
            [*result.diagnostics, *extra_diagnostics],
            source_file=context.input_path,
            report_path=declaration_path,
        )
        diagnostics_path = write_standardized_reports(
            main_report_path=declaration_path,
            raw_report_text=raw_report_text,
            status=status,
            tax_year=args.tax_year,
            diagnostics=diagnostics,
            diagnostics_title=f"{result.analyzer_alias} analyzer diagnostics",
            diagnostics_extra_lines=[
                f"- input_path: {format_path(context.input_path)}",
                f"- output_dir: {format_path(output_dir)}",
            ],
        )
        result.output_paths["diagnostics_txt"] = diagnostics_path
        print_operational_summary(
            status=status,
            main_report_path=declaration_path,
            diagnostics_path=diagnostics_path,
            diagnostics=diagnostics,
        )
    else:
        stdout_status = {
            "OK": "SUCCESS",
            "WARNING": "WARNING",
            "NEEDS_REVIEW": "MANUAL CHECK REQUIRED",
            "ERROR": "ERROR",
        }
        print(f"STATUS: {stdout_status[status]}")
    return 0


def _run_aggregate_mode(args: argparse.Namespace) -> int:
    if args.input_dir is None:
        raise InputDetectionError("--input-dir is required in aggregate mode")
    if args.tax_year is None:
        raise InputDetectionError("--tax-year is required in aggregate mode")

    _validate_tax_year(args.tax_year)
    _configure_logging(args.log_level)
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = _prepare_output_dir(output_dir=args.output_dir, clean_output=bool(args.clean_output))

    registry = args._registry
    overrides = parse_analyzer_input_overrides(args.analyzer_input, registry=registry)
    detection = detect_analyzer_inputs(
        input_dir=input_dir,
        include_pattern=args.include_pattern,
        registry=registry,
    )

    detected = {alias: list(paths) for alias, paths in detection.detected.items()}
    ignored_items = list(detection.ignored_items)
    detected_items = list(detection.detected_items)
    spb8_candidates = (
        _detect_spb8_input_candidates(input_dir, include_pattern=args.include_pattern)
        if _spb8_enabled(args)
        else []
    )
    if args.spb8_input_file is not None:
        spb8_input_file = args.spb8_input_file.expanduser().resolve()
        spb8_detection_reason = "explicit --spb8-input-file"
    else:
        spb8_input_file = (
            _detect_spb8_input_file(input_dir, include_pattern=args.include_pattern)
            if _spb8_enabled(args)
            else None
        )
        spb8_detection_reason = "auto-detected from filename tokens"
    spb8_related_paths = {path.expanduser().resolve() for path in spb8_candidates}
    if spb8_input_file is not None:
        spb8_related_paths.add(spb8_input_file)
    if spb8_related_paths:
        ignored_items = [
            item
            for item in ignored_items
            if item.path.expanduser().resolve() not in spb8_related_paths
        ]
    if args.spb8_input_file is not None:
        for candidate in spb8_candidates:
            candidate_path = candidate.expanduser().resolve()
            if candidate_path == spb8_input_file:
                continue
            ignored_items.append(
                DetectionItem(
                    path=candidate_path,
                    analyzer_alias=None,
                    reason=(
                        "SPB-8 candidate ignored because --spb8-input-file "
                        f"selected {format_path(spb8_input_file)}"
                    ),
                )
            )

    for alias, override_paths in overrides.items():
        previous = detected.get(alias, [])
        if previous:
            for previous_path in previous:
                ignored_items.append(
                    DetectionItem(
                        path=previous_path,
                        analyzer_alias=alias,
                        reason=f"overridden by --analyzer-input for alias {alias}",
                    )
                )
            detected_items = [item for item in detected_items if item.analyzer_alias != alias]
        detected[alias] = list(override_paths)
        for override_path in override_paths:
            detected_items.append(
                DetectionItem(
                    path=override_path,
                    analyzer_alias=alias,
                    reason="explicit --analyzer-input override",
                )
            )

    detected_input_items_for_report = [
        (item.path, item.analyzer_alias or "unknown", item.reason) for item in detected_items
    ]
    if spb8_input_file is not None:
        detected_input_items_for_report.append(
            (spb8_input_file, "spb8-input", spb8_detection_reason)
        )

    if not detected or all(not paths for paths in detected.values()):
        raise InputDetectionError("no analyzer inputs detected")

    group_options = {
        "p2p_secondary_market_mode": args.p2p_secondary_market_mode,
        "cache_dir": str(args.cache_dir) if args.cache_dir is not None else None,
        "display_currency": str(args.display_currency),
    }
    try:
        spb8_input_rows = (
            _load_spb8_input(spb8_input_file)
            if _spb8_enabled(args) and spb8_input_file is not None
            else []
        )
    except Exception as exc:  # noqa: BLE001
        aggregated_report_path = output_dir / f"aggregated_tax_report_{args.tax_year}.txt"
        diagnostic = classify_exception(exc, analyzer_alias="spb8", input_path=spb8_input_file)
        diagnostics = _with_report_context([diagnostic], source_file=spb8_input_file, report_path=aggregated_report_path)
        raw_report_text = render_aggregated_report(
            tax_year=args.tax_year,
            detected_inputs=detected,
            ignored_inputs=[(item.path, item.reason) for item in ignored_items],
            analyzer_results=[],
            analyzer_errors={"spb8": [str(exc)]},
            detected_input_items=detected_input_items_for_report,
            display_currency=str(args.display_currency),
            cache_dir=args.cache_dir,
        )
        diagnostics_path = write_standardized_reports(
            main_report_path=aggregated_report_path,
            raw_report_text=raw_report_text,
            status="ERROR",
            tax_year=args.tax_year,
            diagnostics=diagnostics,
            diagnostics_title="Aggregated tax report diagnostics",
            diagnostics_extra_lines=[
                f"- output_dir: {format_path(output_dir)}",
                f"- spb8_input_file: {format_path(spb8_input_file)}",
            ],
            exception=exc,
        )
        print_operational_summary(
            status="ERROR",
            main_report_path=aggregated_report_path,
            diagnostics_path=diagnostics_path,
            diagnostics=diagnostics,
        )
        return 2

    analyzer_results: list[TaxAnalysisResult] = []
    analyzer_errors: dict[str, list[str]] = {}
    analyzer_error_diagnostics: list[AnalysisDiagnostic] = []
    resolved_spb8_rows: list[SPB8Row] = []
    resolved_spb8_notes: list[str] = []

    for alias in sorted(detected):
        definition = registry.resolve(alias)
        input_paths = detected[alias]
        if not input_paths:
            continue
        alias_output_dir = (output_dir / alias).resolve()
        alias_output_dir.mkdir(parents=True, exist_ok=True)
        options = definition.build_options(args, "aggregate", group_options)
        options["display_currency"] = str(args.display_currency)
        options["cache_dir"] = str(args.cache_dir) if args.cache_dir is not None else options.get("cache_dir")
        if len(input_paths) == 1:
            run_targets = [(0, input_paths[0], alias_output_dir)]
        else:
            run_targets = [
                (
                    index,
                    input_path,
                    (alias_output_dir / f"{input_path.stem}_{index + 1}").resolve(),
                )
                for index, input_path in enumerate(input_paths)
            ]
        for _index, input_path, analyzer_output_dir in run_targets:
            analyzer_output_dir.mkdir(parents=True, exist_ok=True)
            context = AnalyzerRunContext(
                input_path=input_path,
                tax_year=args.tax_year,
                output_dir=analyzer_output_dir,
                log_level=args.log_level,
                options=options,
            )
            try:
                result = definition.run(context)
                result_extra_diagnostics: list[AnalysisDiagnostic] = []
                result_spb8_needs_review = False
                if _spb8_enabled(args):
                    rows = _merged_spb8_rows_for_result(result, spb8_input_rows)
                    resolved_spb8_rows.extend(rows)
                    rows, option_notes = filter_rows_for_options(
                        rows,
                        enabled=True,
                        exclude_crypto=bool(args.spb8_exclude_crypto),
                    )
                    notes = _spb8_notes_for_result(
                        result,
                        rows=rows,
                        input_file_provided=spb8_input_file is not None,
                        option_notes=option_notes,
                    )
                    result_spb8_needs_review = bool(missing_spb8_value_notes(rows))
                    result_extra_diagnostics.extend(
                        _spb8_missing_diagnostics(rows, analyzer_alias=result.analyzer_alias)
                    )
                    resolved_spb8_notes.extend(notes)
                    _append_spb8_to_declaration(result, rows=rows, notes=notes)
                declaration_path = result.output_paths.get("declaration_txt")
                if declaration_path is not None:
                    result_status = _status_with_spb8_review(
                        result.status,
                        spb8_needs_review=result_spb8_needs_review,
                    )
                    diagnostics_path = write_standardized_reports(
                        main_report_path=declaration_path,
                        raw_report_text=declaration_path.read_text(encoding="utf-8"),
                        status=result_status,
                        tax_year=args.tax_year,
                        diagnostics=_with_report_context(
                            [*result.diagnostics, *result_extra_diagnostics],
                            source_file=context.input_path,
                            report_path=declaration_path,
                        ),
                        diagnostics_title=f"{result.analyzer_alias} analyzer diagnostics",
                        diagnostics_extra_lines=[
                            f"- input_path: {format_path(context.input_path)}",
                            f"- output_dir: {format_path(analyzer_output_dir)}",
                        ],
                    )
                    result.output_paths["diagnostics_txt"] = diagnostics_path
                analyzer_results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("%s analyzer failed for %s: %s", alias, input_path, exc)
                analyzer_errors.setdefault(alias, []).append(f"{input_path.name}: {exc}")
                analyzer_error_diagnostics.append(
                    classify_exception(exc, analyzer_alias=alias, input_path=input_path)
                )

    if not _spb8_enabled(args):
        spb8_rows: list[SPB8Row] = []
        spb8_notes: list[str] = []
        spb8_needs_review = False
    else:
        analyzer_template_rows = [row for result in analyzer_results for row in result.spb8_rows]
        manual_template_rows = _spb8_rows_from_detected(detected)
        base_template_rows = merge_external_platform_rows(analyzer_template_rows, manual_template_rows)
        template_rows = (
            merge_external_platform_rows(base_template_rows, spb8_input_rows)
            if spb8_input_rows
            else base_template_rows
        )
        template_path = _write_spb8_template(output_dir=output_dir, rows=template_rows)
        if spb8_input_file is None:
            print(f"SPB-8 input file: {template_path}")
        base_report_rows = merge_external_platform_rows(resolved_spb8_rows, manual_template_rows)
        merged_rows = merge_external_platform_rows(base_report_rows, spb8_input_rows) if spb8_input_rows else base_report_rows
        spb8_rows, spb8_notes = filter_rows_for_options(
            merged_rows,
            enabled=True,
            exclude_crypto=bool(args.spb8_exclude_crypto),
        )
        spb8_missing_notes = missing_spb8_value_notes(spb8_rows)
        analyzer_error_diagnostics.extend(_spb8_missing_diagnostics(spb8_rows, analyzer_alias="spb8"))
        spb8_notes = _dedupe_notes(resolved_spb8_notes + spb8_notes)
        spb8_needs_review = bool(spb8_missing_notes)

    aggregated_report_text = render_aggregated_report(
        tax_year=args.tax_year,
        detected_inputs=detected,
        ignored_inputs=[(item.path, item.reason) for item in ignored_items],
        analyzer_results=analyzer_results,
        analyzer_errors=analyzer_errors,
        detected_input_items=detected_input_items_for_report,
        analyzer_error_diagnostics=analyzer_error_diagnostics,
        display_currency=str(args.display_currency),
        cache_dir=args.cache_dir,
        spb8_rows=spb8_rows,
        spb8_notes=spb8_notes,
        spb8_needs_review=spb8_needs_review,
    )
    aggregated_report_path = output_dir / f"aggregated_tax_report_{args.tax_year}.txt"

    global_status = _global_status_from_results(
        analyzer_results,
        analyzer_errors,
        spb8_needs_review=spb8_needs_review,
    )
    all_diagnostics = _all_result_diagnostics(analyzer_results, analyzer_error_diagnostics)
    diagnostics_path = write_standardized_reports(
        main_report_path=aggregated_report_path,
        raw_report_text=aggregated_report_text,
        status=global_status,
        tax_year=args.tax_year,
        diagnostics=all_diagnostics,
        diagnostics_title="Aggregated tax report diagnostics",
        diagnostics_extra_lines=[
            f"- output_dir: {format_path(output_dir)}",
            f"- detected_inputs_count: {sum(len(paths) for paths in detected.values())}",
            f"- ignored_inputs_count: {len(ignored_items)}",
        ],
    )
    print_operational_summary(
        status=global_status,
        main_report_path=aggregated_report_path,
        diagnostics_path=diagnostics_path,
        diagnostics=all_diagnostics,
    )
    return 2 if global_status == "ERROR" else 0


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.list_analyzers:
            for analyzer in list_analyzers():
                print(analyzer)
            return 0
        if getattr(args, "single_analyzer_alias", None):
            return _run_single_mode(args)
        return _run_aggregate_mode(args)
    except (AnalyzerRegistryError, InputDetectionError, DisplayCurrencyError, SPB8Error) as exc:
        logger.error("%s", exc)
        print(f"ERROR: {exc}")
        print("STATUS: ERROR")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
