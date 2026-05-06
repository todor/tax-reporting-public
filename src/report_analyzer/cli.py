from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from config import OUTPUT_DIR, PROJECT_ROOT
from integrations.shared.aggregation import render_aggregated_report
from integrations.shared.autodetect import (
    DetectionItem,
    InputDetectionError,
    detect_analyzer_inputs,
    parse_analyzer_input_overrides,
)
from integrations.shared.contracts import AnalyzerRunContext, AnalyzerStatus, TaxAnalysisResult
from integrations.shared.registry import AnalyzerRegistryError, discover_analyzer_registry
from integrations.shared.rendering.common import TECHNICAL_DETAILS_SEPARATOR
from integrations.shared.rendering.display_currency import DisplayCurrencyError
from integrations.shared.spb8 import (
    SPB8Error,
    SPB8Row,
    filter_rows_for_options,
    manual_input_template_rows_for_platform,
    merge_external_platform_rows,
    read_spb8_csv,
    render_spb8_notes_section,
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


def _print_detection_section(title: str, items: list[DetectionItem]) -> None:
    print(title)
    if not items:
        print("- -")
        return
    for item in items:
        if item.analyzer_alias:
            print(f"- {item.path} -> {item.analyzer_alias} ({item.reason})")
        else:
            print(f"- {item.path} ({item.reason})")


def _add_spb8_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-spb8",
        action="store_true",
        help="Disable SPB-8 generation",
    )
    parser.add_argument(
        "--spb8-input-file",
        type=Path,
        help="External SPB-8 input CSV with platform NAV values",
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
    section_lines = render_spb8_section(rows)
    notes_lines = render_spb8_notes_section(notes)
    if not section_lines and not notes_lines:
        return
    new_block = "\n\n".join("\n".join(block) for block in (section_lines, notes_lines) if block)
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


def _write_spb8_template(*, output_dir: Path, rows: list[SPB8Row]) -> Path:
    output_path = output_dir / "spb8-input-file.csv"
    write_spb8_csv(output_path, rows)
    return output_path


def _spb8_warning_notes(*, input_file_provided: bool) -> list[str]:
    if input_file_provided:
        return []
    return [
        "SPB-8 input file was not provided. A template was generated. Fill it and rerun with --spb8-input-file <path>.",
    ]


def _global_status_from_results(
    results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
) -> AnalyzerStatus:
    statuses: list[AnalyzerStatus] = [result.status for result in results]
    if analyzer_errors:
        statuses.extend(["ERROR"] * sum(len(items) for items in analyzer_errors.values()))
    if any(status == "ERROR" for status in statuses):
        return "ERROR"
    if any(status == "NEEDS_REVIEW" for status in statuses):
        return "NEEDS_REVIEW"
    if any(status == "WARNING" for status in statuses):
        return "WARNING"
    return "OK"


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
        print("STATUS: ERROR")
        return 2

    if _spb8_enabled(args):
        external_rows = _load_spb8_input(args.spb8_input_file) if args.spb8_input_file is not None else []
        rows = rows_by_platform(external_rows).get(result.analyzer_alias, result.spb8_rows) if external_rows else result.spb8_rows
        rows, option_notes = filter_rows_for_options(
            rows,
            enabled=True,
            exclude_crypto=bool(args.spb8_exclude_crypto),
        )
        notes = result.spb8_notes + _spb8_warning_notes(input_file_provided=args.spb8_input_file is not None) + option_notes
        _append_spb8_to_declaration(result, rows=rows, notes=notes)

    status = result.status
    if status == "OK":
        print("STATUS: SUCCESS")
    elif status == "NEEDS_REVIEW":
        print("STATUS: MANUAL CHECK REQUIRED")
    else:
        print(f"STATUS: {status}")

    for key, path in sorted(result.output_paths.items()):
        print(f"{key}: {path}")
    return 0


def _run_aggregate_mode(args: argparse.Namespace) -> int:
    if args.input_dir is None:
        raise InputDetectionError("--input-dir is required in aggregate mode")
    if args.tax_year is None:
        raise InputDetectionError("--tax-year is required in aggregate mode")

    _validate_tax_year(args.tax_year)
    _configure_logging(args.log_level)
    output_dir = _prepare_output_dir(output_dir=args.output_dir, clean_output=bool(args.clean_output))

    registry = args._registry
    overrides = parse_analyzer_input_overrides(args.analyzer_input, registry=registry)
    detection = detect_analyzer_inputs(
        input_dir=args.input_dir.expanduser().resolve(),
        include_pattern=args.include_pattern,
        registry=registry,
    )

    detected = {alias: list(paths) for alias, paths in detection.detected.items()}
    ignored_items = list(detection.ignored_items)
    detected_items = list(detection.detected_items)

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

    _print_detection_section("Detected inputs", sorted(detected_items, key=lambda item: str(item.path)))
    _print_detection_section("Ignored inputs", sorted(ignored_items, key=lambda item: str(item.path)))

    if not detected or all(not paths for paths in detected.values()):
        raise InputDetectionError("no analyzer inputs detected")

    group_options = {
        "p2p_secondary_market_mode": args.p2p_secondary_market_mode,
        "cache_dir": str(args.cache_dir) if args.cache_dir is not None else None,
        "display_currency": str(args.display_currency),
    }
    spb8_input_rows = _load_spb8_input(args.spb8_input_file) if args.spb8_input_file is not None else []
    if _spb8_enabled(args) and args.spb8_input_file is None:
        template_path = _write_spb8_template(output_dir=output_dir, rows=_spb8_rows_from_detected(detected))
        print("SPB-8 input file generated based on detected data sources. Fill missing NAV values and rerun with --spb8-input-file.")
        print(f"SPB-8 input file: {template_path}")
        print("SPB-8 input file was not provided. A template was generated. Fill it and rerun with --spb8-input-file <path>.")
    elif _spb8_enabled(args):
        _write_spb8_template(output_dir=output_dir, rows=spb8_input_rows)

    analyzer_results: list[TaxAnalysisResult] = []
    analyzer_errors: dict[str, list[str]] = {}

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
                if _spb8_enabled(args):
                    external_rows_by_platform = rows_by_platform(spb8_input_rows)
                    rows = (
                        external_rows_by_platform.get(result.analyzer_alias, result.spb8_rows)
                        if spb8_input_rows
                        else result.spb8_rows
                    )
                    rows, option_notes = filter_rows_for_options(
                        rows,
                        enabled=True,
                        exclude_crypto=bool(args.spb8_exclude_crypto),
                    )
                    notes = (
                        result.spb8_notes
                        + _spb8_warning_notes(input_file_provided=args.spb8_input_file is not None)
                        + option_notes
                    )
                    _append_spb8_to_declaration(result, rows=rows, notes=notes)
                analyzer_results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("%s analyzer failed for %s: %s", alias, input_path, exc)
                analyzer_errors.setdefault(alias, []).append(f"{input_path.name}: {exc}")

    analyzer_spb8_rows = [row for result in analyzer_results for row in result.spb8_rows]
    if not _spb8_enabled(args):
        spb8_rows: list[SPB8Row] = []
        spb8_notes: list[str] = []
    else:
        merged_rows = (
            merge_external_platform_rows(analyzer_spb8_rows, spb8_input_rows)
            if spb8_input_rows
            else analyzer_spb8_rows
        )
        spb8_rows, spb8_notes = filter_rows_for_options(
            merged_rows,
            enabled=True,
            exclude_crypto=bool(args.spb8_exclude_crypto),
        )
        spb8_notes = _spb8_warning_notes(input_file_provided=args.spb8_input_file is not None) + spb8_notes

    aggregated_report_text = render_aggregated_report(
        tax_year=args.tax_year,
        detected_inputs=detected,
        ignored_inputs=[(item.path, item.reason) for item in ignored_items],
        analyzer_results=analyzer_results,
        analyzer_errors=analyzer_errors,
        display_currency=str(args.display_currency),
        cache_dir=args.cache_dir,
        spb8_rows=spb8_rows,
        spb8_notes=spb8_notes,
    )
    aggregated_report_path = output_dir / f"aggregated_tax_report_{args.tax_year}.txt"
    aggregated_report_path.write_text(aggregated_report_text, encoding="utf-8")

    global_status = _global_status_from_results(analyzer_results, analyzer_errors)
    print(f"STATUS: {global_status}")
    print(f"Aggregated report: {aggregated_report_path}")
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
        print("STATUS: ERROR")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
