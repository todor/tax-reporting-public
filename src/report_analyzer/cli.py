from __future__ import annotations

import argparse
import fnmatch
import logging
import shutil
import sys
from dataclasses import dataclass, replace
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
    AnalyzerReportDetail,
    AnalyzerRunContext,
    AnalyzerStatus,
    TaxAnalysisResult,
)
from integrations.shared.reporting import (
    classify_exception,
    format_path,
    normalize_diagnostics,
    print_operational_summary,
    split_technical_details,
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
_STATE_SIDECAR_SUFFIX = ".state.json"


@dataclass(frozen=True, slots=True)
class _StatefulInput:
    alias: str
    path: Path


@dataclass(frozen=True, slots=True)
class _OpeningStateResolution:
    state_path: Path | None
    source: str
    sidecar_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _OverridableAggregateOption:
    flag: str
    group_key: str
    takes_value: bool
    help: str
    display_label: str
    single_flag: str | None = None
    choices: tuple[str, ...] = ()
    repeatable: bool = False
    default: object = None
    hidden: bool = False
    aggregate_global: bool = True

    @property
    def concrete_flag(self) -> str:
        return self.single_flag or self.flag


# These are analyzer tax/reporting settings that may be set once for an aggregate
# run, then overridden for individual analyzers with --<alias>-<option>. Global
# run controls such as input discovery, output location, SPB-8 inputs, logging,
# and rendering currency intentionally do not live here.
_OVERRIDABLE_AGGREGATE_OPTIONS: tuple[_OverridableAggregateOption, ...] = (
    _OverridableAggregateOption(
        flag="tax-exempt-mode",
        group_key="tax_exempt_mode",
        takes_value=True,
        help=(
            "Controls how securities analyzers determine tax-exempt treatment: "
            "by execution exchange or listing exchange."
        ),
        display_label="Tax-exempt mode",
        choices=("execution_exchange", "listing_exchange"),
    ),
    _OverridableAggregateOption(
        flag="eu-regulated-exchange",
        group_key="eu_regulated_exchange",
        takes_value=True,
        help="Additional EU-regulated exchange override where supported (repeatable or comma-separated)",
        display_label="EU regulated exchange",
        repeatable=True,
    ),
    _OverridableAggregateOption(
        flag="closed-world",
        group_key="closed_world",
        takes_value=False,
        help=(
            "Enable conservative closed-world exchange/market classification where supported; "
            "unknown or unrecognized markets/exchanges are treated conservatively."
        ),
        display_label="Closed-world validation",
    ),
    _OverridableAggregateOption(
        flag="no-net-cfd-financing",
        group_key="no_net_cfd_financing",
        takes_value=False,
        help="Do not net CFD financing into Appendix 5 where supported; positive amounts go to Appendix 6 code 606",
        display_label="Disable CFD financing netting",
    ),
    _OverridableAggregateOption(
        flag="negative-pil-mode",
        group_key="negative_pil_mode",
        takes_value=True,
        help="Negative Payment in Lieu handling mode where supported",
        display_label="Negative PIL mode",
        choices=("always-net", "ignore", "position-aware"),
    ),
    _OverridableAggregateOption(
        flag="positive-wht-mode",
        group_key="positive_wht_mode",
        takes_value=True,
        help="Positive dividend Withholding Tax correction mode where supported",
        display_label="Positive WHT mode",
        choices=("current-year-net", "prior-year-correction"),
        default="current-year-net",
    ),
    _OverridableAggregateOption(
        flag="appendix8-dividend-list-mode",
        group_key="appendix8_dividend_list_mode",
        takes_value=True,
        help="Appendix 8 dividend listing mode where supported",
        display_label="Appendix 8 dividend list mode",
        choices=("company", "country"),
        hidden=True,
    ),
    _OverridableAggregateOption(
        flag="p2p-secondary-market-mode",
        group_key="p2p_secondary_market_mode",
        takes_value=True,
        help="Group-level P2P secondary-market mode",
        display_label="P2P secondary market mode",
        choices=("appendix_5", "appendix_6"),
        default="appendix_6",
    ),
    _OverridableAggregateOption(
        flag="csv-decimal-separator",
        group_key="csv_decimal_separator",
        takes_value=True,
        help="CSV decimal separator mode where supported",
        display_label="CSV decimal separator",
        choices=("auto", "dot", "comma"),
        hidden=True,
        default="auto",
        aggregate_global=False,
    ),
)
_OVERRIDABLE_AGGREGATE_OPTION_BY_FLAG = {item.flag: item for item in _OVERRIDABLE_AGGREGATE_OPTIONS}


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
    parser.add_argument(
        "--spb8-csv-decimal-separator",
        choices=["auto", "dot", "comma"],
        default="auto",
        help="Decimal separator mode for external SPB-8 input CSV only",
    )


def _add_opening_state_argument(parser: argparse.ArgumentParser, *, aggregate: bool) -> None:
    if aggregate:
        parser.add_argument(
            "--opening-state-json",
            action="append",
            default=[],
            metavar="VALUE",
            help=(
                "Opening state JSON for stateful analyzers. In aggregate mode repeat as "
                "input-file=state.json, alias:input-file=state.json, or pass one simple "
                "state path only when exactly one stateful input is detected."
            ),
        )
        return
    parser.add_argument(
        "--opening-state-json",
        type=str,
        help=(
            "Optional opening state JSON. If omitted, the input is treated as "
            "since-inception/full-history."
        ),
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


def _with_generated_artifacts_section(result: TaxAnalysisResult, raw_report_text: str) -> str:
    visible_artifacts = [artifact for artifact in result.generated_artifacts if artifact.show_in_main]
    if not visible_artifacts:
        return raw_report_text

    lines = ["Помощни файлове за проверка"]
    seen_paths: set[str] = set()
    analyzer_label = _main_report_analyzer_label(result.analyzer_alias)
    for artifact in sorted(visible_artifacts, key=lambda item: format_path(item.path)):
        path_text = format_path(artifact.path)
        if path_text in seen_paths:
            continue
        seen_paths.add(path_text)
        lines.append(f"- {analyzer_label} — {result.input_path.name}")
        lines.append(f"  CSV файл за проверка на обработените редове: {path_text}")
    if len(lines) == 1:
        return raw_report_text

    section = "\n".join(lines)
    current = raw_report_text.rstrip()
    if TECHNICAL_DETAILS_SEPARATOR in current:
        declaration_part, technical_part = current.split(TECHNICAL_DETAILS_SEPARATOR, 1)
        return (
            declaration_part.rstrip()
            + "\n\n"
            + section
            + "\n\n"
            + TECHNICAL_DETAILS_SEPARATOR
            + technical_part.rstrip()
            + "\n"
        )
    return current + "\n\n" + section + "\n"


def _main_report_analyzer_label(alias: str) -> str:
    names = {
        "ibkr": "IBKR",
        "kraken": "Kraken",
        "coinbase": "Coinbase",
        "binance_futures": "Binance Futures",
        "crypto_com": "Crypto.com",
        "finexify": "Finexify",
        "karol": "Karol",
    }
    return names.get(alias, alias.replace("_", " ").title())


def _spb8_rows_from_detected(detected: dict[str, list[Path]]) -> list[SPB8Row]:
    rows: list[SPB8Row] = []
    for alias, paths in sorted(detected.items()):
        for path in paths:
            rows.extend(manual_input_template_rows_for_platform(platform=alias, account_name=path.stem))
    return rows


def _load_spb8_input(path: Path, *, csv_decimal_separator: str = "auto") -> list[SPB8Row]:
    if not path.expanduser().exists():
        raise InputDetectionError(f"SPB-8 input file does not exist: {path}")
    try:
        return read_spb8_csv(path, csv_decimal_separator=csv_decimal_separator)
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


def _is_opening_state_sidecar(path: Path) -> bool:
    return path.name.lower().endswith(_STATE_SIDECAR_SUFFIX)


def _state_path(raw_value: str) -> Path:
    value = raw_value.strip()
    if value == "":
        raise InputDetectionError("empty --opening-state-json value")
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise InputDetectionError(f"--opening-state-json file does not exist: {path}")
    if not path.is_file():
        raise InputDetectionError(f"--opening-state-json is not a file: {path}")
    return path


def _stateful_inputs(
    detected: dict[str, list[Path]],
    *,
    registry,
) -> list[_StatefulInput]:
    stateful: list[_StatefulInput] = []
    for alias in sorted(detected):
        definition = registry.resolve(alias)
        if not definition.supports_opening_state:
            continue
        for path in detected[alias]:
            stateful.append(_StatefulInput(alias=alias, path=path.expanduser().resolve()))
    return stateful


def _opening_state_sidecars(input_dir: Path) -> list[Path]:
    return sorted(
        path.expanduser().resolve()
        for path in input_dir.iterdir()
        if path.is_file() and _is_opening_state_sidecar(path)
    )


def _resolve_alias_prefix(raw_selector: str, stateful_aliases: set[str], *, registry) -> tuple[str | None, str]:
    if ":" not in raw_selector:
        return None, raw_selector
    maybe_alias, selector = raw_selector.split(":", 1)
    try:
        alias = registry.resolve(maybe_alias.strip()).alias
    except AnalyzerRegistryError:
        return None, raw_selector
    if alias not in stateful_aliases:
        return None, raw_selector
    if selector.strip() == "":
        raise InputDetectionError(f"invalid --opening-state-json mapping {raw_selector!r}; missing input selector")
    return alias, selector


def _matches_state_selector(
    item: _StatefulInput,
    selector: str,
    *,
    input_dir: Path,
) -> bool:
    needle = selector.strip()
    if needle == "":
        return False
    path = item.path.expanduser().resolve()
    if needle == path.name:
        return True
    try:
        relative = path.relative_to(input_dir)
    except ValueError:
        relative = None
    if relative is not None and needle in {str(relative), relative.as_posix()}:
        return True
    if needle in {str(path), path.as_posix()}:
        return True
    candidate = Path(needle).expanduser()
    return candidate.is_absolute() and candidate.resolve() == path


def _resolve_opening_states(
    *,
    args: argparse.Namespace,
    input_dir: Path,
    detected: dict[str, list[Path]],
    registry,
) -> tuple[dict[Path, _OpeningStateResolution], list[Path]]:
    stateful = _stateful_inputs(detected, registry=registry)
    sidecars = _opening_state_sidecars(input_dir)
    expected_sidecars: dict[Path, Path] = {}
    sidecar_to_input: dict[Path, Path] = {}
    for item in stateful:
        sidecar = item.path.with_suffix(_STATE_SIDECAR_SUFFIX).expanduser().resolve()
        if sidecar.exists() and sidecar.is_file():
            expected_sidecars[item.path] = sidecar
            sidecar_to_input[sidecar] = item.path

    resolutions: dict[Path, _OpeningStateResolution] = {
        item.path: _OpeningStateResolution(
            state_path=expected_sidecars.get(item.path),
            source="auto" if item.path in expected_sidecars else "none",
            sidecar_path=expected_sidecars.get(item.path),
        )
        for item in stateful
    }
    unmatched_sidecars = [path for path in sidecars if path not in sidecar_to_input]
    raw_values = list(getattr(args, "opening_state_json", []) or [])
    if not raw_values:
        return resolutions, unmatched_sidecars

    simple_values = [value for value in raw_values if "=" not in value]
    mapped_values = [value for value in raw_values if "=" in value]
    if simple_values and mapped_values:
        raise InputDetectionError(
            "Do not combine simple and mapped --opening-state-json values. "
            "Use per-input mappings when more than one state value is needed."
        )
    if len(simple_values) > 1:
        raise InputDetectionError(
            "Only one simple --opening-state-json value is allowed. "
            "Use per-input mappings for multiple state files."
        )
    if simple_values:
        if len(stateful) == 0:
            raise InputDetectionError("--opening-state-json was provided but no stateful analyzer inputs were detected")
        if len(stateful) > 1:
            names = "\n".join(f"- {item.alias}: {format_path(item.path)}" for item in stateful)
            raise InputDetectionError(
                "A simple --opening-state-json path is valid only when exactly one stateful input is detected.\n"
                f"Detected stateful inputs:\n{names}\n"
                "Use --opening-state-json input-file=state.json mappings instead."
            )
        item = stateful[0]
        resolutions[item.path] = _OpeningStateResolution(
            state_path=_state_path(simple_values[0]),
            source="cli",
            sidecar_path=expected_sidecars.get(item.path),
        )
        return resolutions, unmatched_sidecars

    stateful_aliases = {item.alias for item in stateful}
    assigned: set[Path] = set()
    for raw in mapped_values:
        selector_raw, state_raw = raw.split("=", 1)
        selector_raw = selector_raw.strip()
        if selector_raw == "":
            raise InputDetectionError(f"invalid --opening-state-json mapping {raw!r}; missing input selector")
        alias_filter, selector = _resolve_alias_prefix(selector_raw, stateful_aliases, registry=registry)
        candidates = [item for item in stateful if alias_filter is None or item.alias == alias_filter]
        matches = [
            item
            for item in candidates
            if _matches_state_selector(item, selector, input_dir=input_dir)
        ]
        if not matches:
            raise InputDetectionError(
                f"--opening-state-json mapping {selector_raw!r} did not match any detected stateful input"
            )
        if len(matches) > 1:
            formatted = "\n".join(f"- {item.alias}: {format_path(item.path)}" for item in matches)
            raise InputDetectionError(
                f"--opening-state-json mapping {selector_raw!r} matched multiple inputs:\n"
                f"{formatted}\n"
                "Use a more specific relative/absolute path or an alias-qualified mapping."
            )
        target = matches[0].path
        if target in assigned:
            raise InputDetectionError(
                f"multiple --opening-state-json mappings target the same input: {format_path(target)}"
            )
        assigned.add(target)
        resolutions[target] = _OpeningStateResolution(
            state_path=_state_path(state_raw),
            source="cli",
            sidecar_path=expected_sidecars.get(target),
        )
    return resolutions, unmatched_sidecars


def _opening_state_description(resolution: _OpeningStateResolution) -> str:
    if resolution.state_path is None:
        return "none (since inception)"
    if resolution.source == "cli" and resolution.sidecar_path is not None:
        return (
            f"{format_path(resolution.state_path)} "
            f"(CLI override; sidecar {format_path(resolution.sidecar_path)} ignored)"
        )
    if resolution.source == "cli":
        return f"{format_path(resolution.state_path)} (CLI override)"
    return f"{format_path(resolution.state_path)} (auto-detected sidecar)"


def _opening_state_diagnostics_lines(
    *,
    detected: dict[str, list[Path]],
    registry,
    resolutions: dict[Path, _OpeningStateResolution],
    unmatched_sidecars: list[Path],
) -> list[str]:
    stateful = _stateful_inputs(detected, registry=registry)
    if not stateful and not unmatched_sidecars:
        return []
    lines = ["- opening_state_resolution:"]
    for item in stateful:
        resolution = resolutions.get(
            item.path,
            _OpeningStateResolution(state_path=None, source="none"),
        )
        lines.append(
            f"  - {format_path(item.path)} -> {item.alias}: "
            f"Opening state: {_opening_state_description(resolution)}"
        )
    for path in unmatched_sidecars:
        lines.append(
            f"  - {format_path(path)}: unmatched state sidecar ignored; "
            "no detected stateful input has the same basename"
        )
    return lines


def _opening_state_diagnostics_line(
    *,
    input_path: Path,
    resolution: _OpeningStateResolution | None,
) -> str:
    if resolution is None:
        resolution = _OpeningStateResolution(state_path=None, source="none")
    return f"- opening_state: {_opening_state_description(resolution)}"


def _append_report_detail(
    result: TaxAnalysisResult,
    *,
    key: str,
    title: str,
    lines: list[str],
    visibility: str,
    category: str = "diagnostics",
) -> None:
    result.report_details.append(
        AnalyzerReportDetail(
            key=key,
            title=title,
            lines=tuple(lines),
            visibility=visibility,  # type: ignore[arg-type]
            analyzer_alias=result.analyzer_alias,
            source_path=result.input_path,
            category=category,
        )
    )


def _attach_detection_detail(
    result: TaxAnalysisResult,
    *,
    detection_reason: str,
) -> None:
    _append_report_detail(
        result,
        key="input_detection",
        title="Input detection context",
        lines=[
            f"- detection reason: {detection_reason or '-'}",
        ],
        visibility="DIAGNOSTICS",
        category="audit",
    )


def _attach_opening_state_detail(
    result: TaxAnalysisResult,
    *,
    input_path: Path,
    resolution: _OpeningStateResolution | None,
) -> None:
    _append_report_detail(
        result,
        key="opening_state_resolution",
        title="Opening state resolution",
        lines=[_opening_state_diagnostics_line(input_path=input_path, resolution=resolution)],
        visibility="DIAGNOSTICS",
        category="audit",
    )


def _is_debug_technical_line(line: str) -> bool:
    lowered = line.casefold()
    return "debug" in lowered or "_debug" in lowered


def _attach_analyzer_technical_details(result: TaxAnalysisResult, raw_report_text: str) -> None:
    _declaration_text, technical_lines = split_technical_details(raw_report_text)
    if not technical_lines:
        return
    diagnostics_lines: list[str] = []
    debug_lines: list[str] = []
    for line in technical_lines:
        if _is_debug_technical_line(line):
            debug_lines.append(line)
        else:
            diagnostics_lines.append(line)
    if diagnostics_lines:
        _append_report_detail(
            result,
            key="analyzer_technical_details",
            title="Analyzer technical details",
            lines=diagnostics_lines,
            visibility="DIAGNOSTICS",
            category="technical_audit",
        )
    if debug_lines:
        _append_report_detail(
            result,
            key="analyzer_debug_artifacts",
            title="Analyzer debug artifacts",
            lines=debug_lines,
            visibility="DEBUG",
            category="debug",
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
    _ = rows
    if not result.spb8_corporate_actions_present:
        return []
    return [
        "\n".join(
            [
                "Неподдържаните IBKR Corporate Actions може да влияят на коректността на СПБ-8 количествата, "
                "защото могат да променят ISIN-и, количества или позиции. "
                "Вижте съответното предупреждение в секцията „Изискват ръчен преглед“.",
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


def _spb8_missing_diagnostics(
    rows: list[SPB8Row],
    *,
    analyzer_alias: str,
    corporate_actions_safety_platforms: set[str] | None = None,
) -> list[AnalysisDiagnostic]:
    safety_platforms = {platform.strip().casefold() for platform in corporate_actions_safety_platforms or set()}
    missing_by_row: dict[tuple[str, ...], dict[str, object]] = {}
    missing_platforms: set[str] = set()
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
        platform_key = row.platform.strip().casefold()
        missing_platforms.add(platform_key)
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
    params: dict[str, object] = {"missing_values": missing_values}
    safety_missing_platforms = sorted(platform for platform in missing_platforms if platform in safety_platforms)
    if safety_missing_platforms:
        params["missing_value_reason"] = "corporate_actions_safety"
        params["corporate_actions_safety_platforms"] = safety_missing_platforms
    return [
        AnalysisDiagnostic(
            severity="MANUAL_REVIEW",
            message="SPB-8 required values are missing.",
            analyzer_alias=analyzer_alias,
            code="SPB8_MISSING_VALUES",
            params=params,
            technical_message_en=None,
        )
    ]


def _with_generated_spb8_input_file(
    diagnostics: list[AnalysisDiagnostic],
    *,
    template_path: Path,
) -> list[AnalysisDiagnostic]:
    enriched: list[AnalysisDiagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.code != "SPB8_MISSING_VALUES":
            enriched.append(diagnostic)
            continue
        params = dict(diagnostic.params)
        params["generated_spb8_input_file"] = format_path(template_path)
        enriched.append(replace(diagnostic, params=params))
    return enriched


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
    has_aggregate_spb8_missing_values = any(
        diagnostic.code == "SPB8_MISSING_VALUES" for diagnostic in extra_diagnostics
    )
    diagnostics: list[AnalysisDiagnostic] = []
    for result in results:
        result_diagnostics = _with_report_context(
            result.diagnostics,
            source_file=result.input_path,
            report_path=result.output_paths.get("declaration_txt"),
        )
        if has_aggregate_spb8_missing_values:
            result_diagnostics = [
                diagnostic for diagnostic in result_diagnostics if diagnostic.code != "SPB8_MISSING_VALUES"
            ]
        diagnostics.extend(result_diagnostics)
    diagnostics.extend(extra_diagnostics)
    return diagnostics


def _ignored_input_diagnostics(ignored_items: list[DetectionItem]) -> list[AnalysisDiagnostic]:
    related_items = [item for item in ignored_items if item.related_to_supported_analyzer]
    if not related_items:
        return []
    return [
        AnalysisDiagnostic(
            severity="WARNING",
            analyzer_alias="aggregate",
            code="IGNORED_RELATED_INPUT",
            message="Ignored files looked related to supported analyzers.",
            params={
                "count": len(related_items),
                "items": [
                    {
                        "path": format_path(item.path),
                        "filename": item.path.name,
                        "reason": item.reason,
                        "analyzer_alias": item.analyzer_alias or "-",
                    }
                    for item in related_items
                ],
            },
        )
    ]


def _global_status_from_results(
    results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
    *,
    spb8_needs_review: bool = False,
    ignored_related_input: bool = False,
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
    if ignored_related_input:
        return "WARNING"
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


def _aggregate_override_usage() -> str:
    return (
        "Analyzer-specific overrides:\n"
        "  In analyzer-specific commands, use normal unprefixed option names.\n"
        "  In aggregate mode, use analyzer-prefixed overrides:\n"
        "    --<analyzer-alias>-<option>\n"
        "\n"
        "Examples:\n"
        "  tax-reporting ibkr --tax-exempt-mode listing_exchange\n"
        "  tax-reporting --input-dir inputs --tax-year 2025 --ibkr-tax-exempt-mode execution_exchange\n"
        "\n"
        "To see the supported analyzer-prefixed options, run:\n"
        "  tax-reporting --list-aggregate-overrides\n"
        "\n"
        "When both forms are present, the analyzer-prefixed value wins over the aggregate value.\n"
        "Options that control the whole run, such as input discovery, output paths, logging,\n"
        "SPB-8 inputs, opening-state mappings, and display currency, are configured only once."
    )


def _overridable_flag_from_override_name(raw_name: str, *, registry) -> str:
    for raw_alias in sorted(registry.alias_lookup, key=len, reverse=True):
        prefix = raw_alias.replace("_", "-") + "-"
        if raw_name.startswith(prefix):
            return raw_name[len(prefix) :]
    for flag in sorted(_OVERRIDABLE_AGGREGATE_OPTION_BY_FLAG, key=len, reverse=True):
        suffix = "-" + flag
        if raw_name.endswith(suffix):
            return flag
    return ""


def _is_aggregate_override_candidate(raw_name: str, *, registry) -> bool:
    if _overridable_flag_from_override_name(raw_name, registry=registry):
        return True
    return any(raw_name.startswith(raw_alias.replace("_", "-") + "-") for raw_alias in registry.alias_lookup)


def _split_analyzer_aggregate_override_args(
    argv: list[str],
    *,
    registered_option_strings: set[str],
    registry,
) -> tuple[list[str], list[str]]:
    parser_args: list[str] = []
    override_args: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--"):
            parser_args.append(token)
            index += 1
            continue

        raw_name = token[2:].split("=", 1)[0]
        if "--" + raw_name in registered_option_strings:
            parser_args.append(token)
            index += 1
            continue
        if not _is_aggregate_override_candidate(raw_name, registry=registry):
            parser_args.append(token)
            index += 1
            continue

        override_args.append(token)
        if "=" not in token and index + 1 < len(argv) and not argv[index + 1].startswith("--"):
            index += 1
            override_args.append(argv[index])
        index += 1
    return parser_args, override_args


def _parse_analyzer_aggregate_overrides(
    unknown_args: list[str],
    *,
    registry,
) -> dict[str, dict[str, object]]:
    overrides: dict[str, dict[str, object]] = {}
    index = 0
    while index < len(unknown_args):
        token = unknown_args[index]
        if not token.startswith("--"):
            raise InputDetectionError(f"unexpected aggregate CLI argument: {token}")
        if "=" in token:
            raw_name, inline_value = token[2:].split("=", 1)
        else:
            raw_name, inline_value = token[2:], None
        matched_alias = ""
        matched_flag = ""
        for raw_alias in sorted(registry.alias_lookup, key=len, reverse=True):
            prefix = raw_alias.replace("_", "-") + "-"
            if raw_name.startswith(prefix):
                candidate_flag = raw_name[len(prefix) :]
                if candidate_flag in _OVERRIDABLE_AGGREGATE_OPTION_BY_FLAG:
                    matched_alias = raw_alias
                    matched_flag = candidate_flag
                    break
        if matched_alias == "":
            for raw_alias in sorted(registry.alias_lookup, key=len, reverse=True):
                prefix = raw_alias.replace("_", "-") + "-"
                if raw_name.startswith(prefix):
                    definition = registry.resolve(raw_alias)
                    option_name = raw_name[len(prefix) :]
                    raise InputDetectionError(
                        f"Unsupported analyzer override: --{raw_name}\n\n"
                        f"The option {option_name} is not supported by analyzer {definition.alias}.\n"
                        "Use --list-aggregate-overrides to see supported analyzer overrides."
                    )
            raise InputDetectionError(
                f"Unsupported analyzer override: --{raw_name}\n\n"
                "The analyzer alias or option is not supported in aggregate mode.\n"
                "Use --list-aggregate-overrides to see supported analyzer overrides."
            )

        definition = registry.resolve(matched_alias)
        option = _OVERRIDABLE_AGGREGATE_OPTION_BY_FLAG[matched_flag]
        if option.group_key not in definition.supported_aggregate_overrides:
            raise InputDetectionError(
                f"Unsupported analyzer override: --{raw_name}\n\n"
                f"The option {option.flag} is not supported by analyzer {definition.alias}.\n"
                "Use --list-aggregate-overrides to see supported analyzer overrides."
            )

        if option.takes_value:
            if inline_value is not None:
                value = inline_value
            else:
                index += 1
                if index >= len(unknown_args) or unknown_args[index].startswith("--"):
                    raise InputDetectionError(f"missing value for --{raw_name}")
                value = unknown_args[index]
            if option.choices and value not in option.choices:
                choices = ", ".join(option.choices)
                raise InputDetectionError(f"invalid value for --{raw_name}: {value!r} (choose from: {choices})")
        else:
            if inline_value is not None:
                raise InputDetectionError(f"--{raw_name} does not take a value")
            value = True

        alias_overrides = overrides.setdefault(definition.alias, {})
        if option.repeatable:
            current = alias_overrides.setdefault(option.group_key, [])
            if not isinstance(current, list):
                raise InputDetectionError(f"internal option conflict for --{raw_name}")
            current.append(value)
        else:
            alias_overrides[option.group_key] = value
        index += 1
    return overrides


def _add_overridable_aggregate_arguments(parser: argparse.ArgumentParser) -> None:
    for option in _OVERRIDABLE_AGGREGATE_OPTIONS:
        if not option.aggregate_global:
            continue
        kwargs: dict[str, object] = {"help": argparse.SUPPRESS if option.hidden else option.help}
        if option.takes_value:
            if option.choices:
                kwargs["choices"] = list(option.choices)
            if option.repeatable:
                kwargs["action"] = "append"
            if option.default is not None:
                kwargs["default"] = option.default
        else:
            kwargs["action"] = "store_true"
        parser.add_argument(f"--{option.flag}", **kwargs)


def _aggregate_override_lines(*, registry) -> list[str]:
    lines = ["Supported aggregate analyzer overrides:"]
    analyzer_lines: list[tuple[str, list[tuple[str, str]]]] = []
    max_flag_length = 0
    for definition in registry.definitions():
        supported_options = [
            option
            for option in _OVERRIDABLE_AGGREGATE_OPTIONS
            if option.group_key in definition.supported_aggregate_overrides
        ]
        if not supported_options:
            continue
        aliases = (definition.alias,) if definition.alias == "ibkr" else (definition.alias, *definition.aliases)
        entries: list[tuple[str, str]] = []
        for alias in aliases:
            prefix = alias.replace("_", "-")
            for option in supported_options:
                aggregate_flag = f"--{prefix}-{option.flag}"
                max_flag_length = max(max_flag_length, len(aggregate_flag))
                entries.append((aggregate_flag, option.display_label))
        analyzer_lines.append((definition.alias, entries))
    for alias, entries in analyzer_lines:
        lines.append("")
        lines.append(f"{alias}:")
        for aggregate_flag, display_label in entries:
            lines.append(f"  {aggregate_flag:<{max_flag_length}}  {display_label}")
    if len(lines) == 1:
        lines.append("")
        lines.append("(none)")
    return lines


def _print_aggregate_overrides(*, registry) -> None:
    print("\n".join(_aggregate_override_lines(registry=registry)))


def build_parser() -> argparse.ArgumentParser:
    registry = discover_analyzer_registry()
    parser = argparse.ArgumentParser(
        prog="tax-reporting",
        epilog=_aggregate_override_usage(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(_registry=registry)
    parser.add_argument(
        "--list-analyzers",
        action="store_true",
        help="List available analyzers and exit",
    )
    parser.add_argument(
        "--list-aggregate-overrides",
        action="store_true",
        help="List analyzer-specific aggregate overrides and exit",
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
    _add_opening_state_argument(parser, aggregate=True)
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
    _add_overridable_aggregate_arguments(parser)

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
        if definition.supports_opening_state:
            _add_opening_state_argument(alias_parser, aggregate=False)
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
    opening_state_resolution = None
    if definition.supports_opening_state:
        state_value = getattr(args, "opening_state_json", None)
        opening_state_resolution = _OpeningStateResolution(
            state_path=_state_path(state_value) if state_value else None,
            source="cli" if state_value else "none",
        )
        options["opening_state_json"] = (
            str(opening_state_resolution.state_path)
            if opening_state_resolution.state_path is not None
            else None
        )
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
                *(
                    [_opening_state_diagnostics_line(input_path=context.input_path, resolution=opening_state_resolution)]
                    if definition.supports_opening_state
                    else []
                ),
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
            external_rows = (
                _load_spb8_input(
                    args.spb8_input_file,
                    csv_decimal_separator=args.spb8_csv_decimal_separator,
                )
                if args.spb8_input_file is not None
                else []
            )
        except Exception as exc:  # noqa: BLE001
            declaration_path = result.output_paths.get("declaration_txt", output_dir / "declaration.txt")
            raw_report_text = declaration_path.read_text(encoding="utf-8") if declaration_path.exists() else ""
            raw_report_text = _with_generated_artifacts_section(result, raw_report_text)
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
                    *(
                        [
                            _opening_state_diagnostics_line(
                                input_path=context.input_path,
                                resolution=opening_state_resolution,
                            )
                        ]
                        if definition.supports_opening_state
                        else []
                    ),
                ],
                generated_artifacts=result.generated_artifacts,
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
        safety_platforms = {result.analyzer_alias} if result.spb8_corporate_actions_present else set()
        extra_diagnostics.extend(
            _spb8_missing_diagnostics(
                rows,
                analyzer_alias=result.analyzer_alias,
                corporate_actions_safety_platforms=safety_platforms,
            )
        )
        _append_spb8_to_declaration(result, rows=rows, notes=notes)
    else:
        spb8_needs_review = False

    status = _status_with_spb8_review(result.status, spb8_needs_review=spb8_needs_review)
    declaration_path = result.output_paths.get("declaration_txt")
    if declaration_path is not None:
        raw_report_text = declaration_path.read_text(encoding="utf-8")
        raw_report_text = _with_generated_artifacts_section(result, raw_report_text)
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
                f"- declaration_path: {format_path(declaration_path)}",
                f"- output_dir: {format_path(output_dir)}",
                *(
                    [_opening_state_diagnostics_line(input_path=context.input_path, resolution=opening_state_resolution)]
                    if definition.supports_opening_state
                    else []
                ),
            ],
            generated_artifacts=result.generated_artifacts,
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

    opening_state_resolutions, unmatched_state_sidecars = _resolve_opening_states(
        args=args,
        input_dir=input_dir,
        detected=detected,
        registry=registry,
    )
    opening_state_diagnostics_lines = _opening_state_diagnostics_lines(
        detected=detected,
        registry=registry,
        resolutions=opening_state_resolutions,
        unmatched_sidecars=unmatched_state_sidecars,
    )

    detected_input_items_for_report = [
        (item.path, item.analyzer_alias or "unknown", item.reason) for item in detected_items
    ]
    detection_reason_by_path = {
        item.path.expanduser().resolve(): item.reason for item in detected_items
    }
    if _spb8_enabled(args) and spb8_input_file is not None:
        detected_input_items_for_report.append(
            (spb8_input_file, "spb8-input", spb8_detection_reason)
        )

    if not detected or all(not paths for paths in detected.values()):
        raise InputDetectionError("no analyzer inputs detected")

    group_options = {
        "p2p_secondary_market_mode": args.p2p_secondary_market_mode,
        "cache_dir": str(args.cache_dir) if args.cache_dir is not None else None,
        "display_currency": str(args.display_currency),
        "tax_exempt_mode": args.tax_exempt_mode,
        "eu_regulated_exchange": args.eu_regulated_exchange,
        "closed_world": bool(args.closed_world),
        "no_net_cfd_financing": bool(args.no_net_cfd_financing),
        "negative_pil_mode": args.negative_pil_mode,
        "positive_wht_mode": args.positive_wht_mode,
        "appendix8_dividend_list_mode": args.appendix8_dividend_list_mode,
    }
    try:
        spb8_input_rows = (
            _load_spb8_input(
                spb8_input_file,
                csv_decimal_separator=args.spb8_csv_decimal_separator,
            )
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
            ignored_input_items=ignored_items,
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
                *opening_state_diagnostics_lines,
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
        analyzer_group_options = dict(group_options)
        analyzer_group_options.update(getattr(args, "analyzer_option_overrides", {}).get(alias, {}))
        options = definition.build_options(args, "aggregate", analyzer_group_options)
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
            run_options = dict(options)
            if definition.supports_opening_state:
                resolution = opening_state_resolutions.get(input_path.expanduser().resolve())
                run_options["opening_state_json"] = (
                    str(resolution.state_path)
                    if resolution is not None and resolution.state_path is not None
                    else None
                )
            else:
                resolution = None
            context = AnalyzerRunContext(
                input_path=input_path,
                tax_year=args.tax_year,
                output_dir=analyzer_output_dir,
                log_level=args.log_level,
                options=run_options,
            )
            try:
                result = definition.run(context)
                _attach_detection_detail(
                    result,
                    detection_reason=detection_reason_by_path.get(context.input_path, "explicit/aggregate input"),
                )
                if definition.supports_opening_state:
                    _attach_opening_state_detail(
                        result,
                        input_path=context.input_path,
                        resolution=resolution,
                    )
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
                    safety_platforms = {result.analyzer_alias} if result.spb8_corporate_actions_present else set()
                    result_extra_diagnostics.extend(
                        _spb8_missing_diagnostics(
                            rows,
                            analyzer_alias=result.analyzer_alias,
                            corporate_actions_safety_platforms=safety_platforms,
                        )
                    )
                    resolved_spb8_notes.extend(notes)
                    _append_spb8_to_declaration(result, rows=rows, notes=notes)
                declaration_path = result.output_paths.get("declaration_txt")
                if declaration_path is not None:
                    raw_report_text = declaration_path.read_text(encoding="utf-8")
                    _attach_analyzer_technical_details(result, raw_report_text)
                    raw_report_text = _with_generated_artifacts_section(result, raw_report_text)
                    result_status = _status_with_spb8_review(
                        result.status,
                        spb8_needs_review=result_spb8_needs_review,
                    )
                    if result_extra_diagnostics:
                        result.diagnostics = normalize_diagnostics(
                            [*result.diagnostics, *result_extra_diagnostics]
                        )
                    diagnostics_path = write_standardized_reports(
                        main_report_path=declaration_path,
                        raw_report_text=raw_report_text,
                        status=result_status,
                        tax_year=args.tax_year,
                        diagnostics=_with_report_context(
                            result.diagnostics,
                            source_file=context.input_path,
                            report_path=declaration_path,
                        ),
                        diagnostics_title=f"{result.analyzer_alias} analyzer diagnostics",
                        diagnostics_extra_lines=[
                            f"- input_path: {format_path(context.input_path)}",
                            f"- declaration_path: {format_path(declaration_path)}",
                            f"- output_dir: {format_path(analyzer_output_dir)}",
                            *(
                                [_opening_state_diagnostics_line(input_path=context.input_path, resolution=resolution)]
                                if definition.supports_opening_state
                                else []
                            ),
                        ],
                        generated_artifacts=result.generated_artifacts,
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
        corporate_actions_safety_platforms = {
            result.analyzer_alias for result in analyzer_results if result.spb8_corporate_actions_present
        }
        analyzer_error_diagnostics.extend(
            _spb8_missing_diagnostics(
                spb8_rows,
                analyzer_alias="spb8",
                corporate_actions_safety_platforms=corporate_actions_safety_platforms,
            )
        )
        if spb8_input_file is None:
            analyzer_error_diagnostics = _with_generated_spb8_input_file(
                analyzer_error_diagnostics,
                template_path=template_path,
            )
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
        ignored_input_items=ignored_items,
        display_currency=str(args.display_currency),
        cache_dir=args.cache_dir,
        spb8_rows=spb8_rows,
        spb8_notes=spb8_notes,
        spb8_needs_review=spb8_needs_review,
    )
    aggregated_report_path = output_dir / f"aggregated_tax_report_{args.tax_year}.txt"
    ignored_diagnostics = _ignored_input_diagnostics(ignored_items)

    global_status = _global_status_from_results(
        analyzer_results,
        analyzer_errors,
        spb8_needs_review=spb8_needs_review,
        ignored_related_input=bool(ignored_diagnostics),
    )
    all_diagnostics = _all_result_diagnostics(analyzer_results, [*analyzer_error_diagnostics, *ignored_diagnostics])
    analyzer_input_count = sum(len(paths) for paths in detected.values())
    auxiliary_input_count = max(0, len(detected_input_items_for_report) - analyzer_input_count)
    successful_analyzer_input_count = sum(1 for result in analyzer_results if result.status != "ERROR")
    failed_analyzer_input_count = sum(len(errors) for errors in analyzer_errors.values())
    failed_analyzer_input_count += sum(1 for result in analyzer_results if result.status == "ERROR")
    warning_count = sum(1 for diagnostic in all_diagnostics if diagnostic.severity == "WARNING")
    error_count = sum(1 for diagnostic in all_diagnostics if diagnostic.severity == "ERROR")
    diagnostics_path = write_standardized_reports(
        main_report_path=aggregated_report_path,
        raw_report_text=aggregated_report_text,
        status=global_status,
        tax_year=args.tax_year,
        diagnostics=all_diagnostics,
        diagnostics_title="Aggregated tax report diagnostics",
        diagnostics_extra_lines=[
            f"- output_dir: {format_path(output_dir)}",
            f"- analyzer_input_count: {analyzer_input_count}",
            f"- auxiliary_input_count: {auxiliary_input_count}",
            f"- ignored_input_count: {len(ignored_items)}",
            f"- successful_analyzer_input_count: {successful_analyzer_input_count}",
            f"- failed_analyzer_input_count: {failed_analyzer_input_count}",
            f"- warning_count: {warning_count}",
            f"- error_count: {error_count}",
            *opening_state_diagnostics_lines,
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
        registry = parser.get_default("_registry")
        parser_argv, override_args = _split_analyzer_aggregate_override_args(
            list(sys.argv[1:] if argv is None else argv),
            registered_option_strings={
                option_string for action in parser._actions for option_string in action.option_strings
            },
            registry=registry,
        )
        args, unknown_args = parser.parse_known_args(parser_argv)
        unknown_args.extend(override_args)
        if args.list_aggregate_overrides:
            if unknown_args:
                parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
            _print_aggregate_overrides(registry=registry)
            return 0
        if args.list_analyzers:
            if unknown_args:
                parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
            for analyzer in list_analyzers():
                print(analyzer)
            return 0
        if getattr(args, "single_analyzer_alias", None):
            if unknown_args:
                parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
            return _run_single_mode(args)
        args.analyzer_option_overrides = _parse_analyzer_aggregate_overrides(
            unknown_args,
            registry=args._registry,
        )
        return _run_aggregate_mode(args)
    except (AnalyzerRegistryError, InputDetectionError, DisplayCurrencyError, SPB8Error) as exc:
        logger.error("%s", exc)
        print(f"ERROR: {exc}")
        print("STATUS: ERROR")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
