from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from integrations.shared.rendering.appendix13 import (
    Appendix13Part2Entry,
    render_appendix13_part2,
)
from integrations.shared.rendering.appendix5 import Appendix5Table2Entry, render_appendix5_table2
from integrations.shared.rendering.appendix6 import (
    Appendix6Part1CodeTotal,
    Appendix6Part1CompanyRow,
    Appendix6Part2TaxableTotal,
    Appendix6RenderData,
    render_appendix6,
)
from integrations.shared.rendering.appendix8 import (
    Appendix8Part1Row,
    Appendix8Part3Row,
    Appendix8RenderData,
    appendix8_part1_declarative_note_lines,
    render_appendix8,
)
from integrations.shared.rendering.appendix9 import Appendix9Part2Row, render_appendix9_part2
from integrations.shared.rendering.common import Money, append_technical_details
from integrations.shared.rendering.display_currency import (
    build_render_context,
    display_currency_technical_lines,
)
from integrations.shared.reporting import normalize_diagnostics
from integrations.shared.spb8 import (
    SPB8Row,
    aggregate_spb8_rows,
    render_spb8_section,
)

from .autodetect import DetectionItem
from .contracts import (
    AnalysisDiagnostic,
    AnalyzerStatus,
    AppendixRecord,
    GeneratedArtifact,
    MainReportNote,
    TaxAnalysisResult,
)

ZERO = Decimal("0")


def _format_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def _visible_generated_artifacts(
    result: TaxAnalysisResult,
    *,
    main: bool,
) -> list[GeneratedArtifact]:
    return [
        artifact
        for artifact in result.generated_artifacts
        if (artifact.show_in_main if main else artifact.show_in_diagnostics)
    ]


def _status_banner(global_status: AnalyzerStatus) -> str:
    if global_status == "OK":
        return "!!! СТАТУС: OK !!!"
    if global_status == "NEEDS_REVIEW":
        return "!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!"
    if global_status == "WARNING":
        return "!!! СТАТУС: WARNING !!!"
    return "!!! СТАТУС: ERROR !!!"


def _to_decimal(record: AppendixRecord, key: str) -> Decimal:
    raw = record.values.get(key, ZERO)
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    return Decimal(str(raw))


def _to_int(record: AppendixRecord, key: str) -> int:
    raw = record.values.get(key, 0)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, Decimal):
        return int(raw)
    return int(str(raw))


def _to_text(record: AppendixRecord, key: str, default: str = "") -> str:
    raw = record.values.get(key, default)
    return str(raw) if raw is not None else default


@dataclass(slots=True)
class Appendix5Totals:
    sale_value_eur: Decimal = ZERO
    acquisition_value_eur: Decimal = ZERO
    profit_eur: Decimal = ZERO
    loss_eur: Decimal = ZERO
    trade_count: int = 0
    net_result_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix13Totals:
    gross_income_eur: Decimal = ZERO
    acquisition_value_eur: Decimal = ZERO
    profit_eur: Decimal = ZERO
    loss_eur: Decimal = ZERO
    trade_count: int = 0
    net_result_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8Part1Totals:
    quantity: Decimal = ZERO
    acquisition_native: Decimal = ZERO
    acquisition_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8Part3Totals:
    gross_income_eur: Decimal = ZERO
    foreign_tax_eur: Decimal = ZERO
    allowable_credit_eur: Decimal = ZERO
    recognized_credit_eur: Decimal = ZERO
    tax_due_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix9Part2Totals:
    gross_income_eur: Decimal = ZERO
    tax_base_eur: Decimal = ZERO
    foreign_tax_eur: Decimal = ZERO
    allowable_credit_eur: Decimal = ZERO
    recognized_credit_eur: Decimal = ZERO
    document_refs: set[str] = field(default_factory=set)


@dataclass(slots=True)
class AggregatedAppendices:
    appendix5_by_code: dict[tuple[str, str], Appendix5Totals] = field(default_factory=dict)
    appendix13_by_code: dict[tuple[str, str, str], Appendix13Totals] = field(default_factory=dict)
    appendix6_part1_company: dict[tuple[str, str, str], Decimal] = field(default_factory=dict)
    appendix6_part1_total_by_code: dict[str, Decimal] = field(default_factory=dict)
    appendix6_part2_taxable_by_code: dict[str, Decimal] = field(default_factory=dict)
    appendix6_part3_withheld_tax: Decimal = ZERO
    appendix8_part1_by_group: dict[tuple[str, str, str], Appendix8Part1Totals] = field(default_factory=dict)
    appendix8_part3_by_group: dict[tuple[str, str, str, str], Appendix8Part3Totals] = field(default_factory=dict)
    appendix9_part2_by_group: dict[tuple[str, str], Appendix9Part2Totals] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnalyzerRunStatus:
    analyzer_alias: str
    status: AnalyzerStatus
    source_path: Path | str | None = None
    declaration_path: Path | str | None = None
    reason: str = ""


def _aggregate_appendix5(record: AppendixRecord, data: AggregatedAppendices) -> None:
    key = ((record.table or ""), (record.code or ""))
    bucket = data.appendix5_by_code.setdefault(key, Appendix5Totals())
    bucket.sale_value_eur += _to_decimal(record, "sale_value_eur")
    bucket.acquisition_value_eur += _to_decimal(record, "acquisition_value_eur")
    bucket.profit_eur += _to_decimal(record, "profit_eur")
    bucket.loss_eur += _to_decimal(record, "loss_eur")
    bucket.trade_count += _to_int(record, "trade_count")
    bucket.net_result_eur += _to_decimal(record, "net_result_eur")


def _aggregate_appendix13(record: AppendixRecord, data: AggregatedAppendices) -> None:
    key = ((record.part or ""), (record.table or ""), (record.code or ""))
    bucket = data.appendix13_by_code.setdefault(key, Appendix13Totals())
    bucket.gross_income_eur += _to_decimal(record, "gross_income_eur")
    bucket.acquisition_value_eur += _to_decimal(record, "acquisition_value_eur")
    bucket.profit_eur += _to_decimal(record, "profit_eur")
    bucket.loss_eur += _to_decimal(record, "loss_eur")
    bucket.trade_count += _to_int(record, "trade_count")
    bucket.net_result_eur += _to_decimal(record, "net_result_eur")


def _aggregate_appendix6(record: AppendixRecord, data: AggregatedAppendices) -> None:
    part = record.part or ""
    code = record.code or ""
    if part == "I":
        row_kind = _to_text(record, "row_kind")
        if row_kind == "company":
            key = (
                _to_text(record, "payer_eik", "-"),
                _to_text(record, "payer"),
                code,
            )
            data.appendix6_part1_company[key] = data.appendix6_part1_company.get(key, ZERO) + _to_decimal(
                record, "income_eur"
            )
            return
        if row_kind == "total_by_code":
            data.appendix6_part1_total_by_code[code] = data.appendix6_part1_total_by_code.get(
                code, ZERO
            ) + _to_decimal(record, "amount_eur")
        return

    if part == "II":
        data.appendix6_part2_taxable_by_code[code] = data.appendix6_part2_taxable_by_code.get(
            code, ZERO
        ) + _to_decimal(record, "taxable_income_eur")
        return

    if part == "III":
        data.appendix6_part3_withheld_tax += _to_decimal(record, "withheld_tax_eur")


def _aggregate_appendix8(record: AppendixRecord, data: AggregatedAppendices) -> None:
    part = record.part or ""
    code = record.code or ""
    if part == "I":
        key = (
            _to_text(record, "asset_type"),
            _to_text(record, "country"),
            _to_text(record, "currency"),
        )
        bucket = data.appendix8_part1_by_group.setdefault(key, Appendix8Part1Totals())
        bucket.quantity += _to_decimal(record, "quantity")
        bucket.acquisition_native += _to_decimal(record, "acquisition_native")
        bucket.acquisition_eur += _to_decimal(record, "acquisition_eur")
        return

    if part == "III":
        key = (
            _to_text(record, "payer"),
            _to_text(record, "country"),
            code,
            _to_text(record, "treaty_method"),
        )
        bucket = data.appendix8_part3_by_group.setdefault(key, Appendix8Part3Totals())
        bucket.gross_income_eur += _to_decimal(record, "gross_income_eur")
        bucket.foreign_tax_eur += _to_decimal(record, "foreign_tax_eur")
        bucket.allowable_credit_eur += _to_decimal(record, "allowable_credit_eur")
        bucket.recognized_credit_eur += _to_decimal(record, "recognized_credit_eur")
        bucket.tax_due_eur += _to_decimal(record, "tax_due_eur")


def _aggregate_appendix9(record: AppendixRecord, data: AggregatedAppendices) -> None:
    part = record.part or ""
    code = record.code or ""
    if part != "II":
        return
    key = (
        _to_text(record, "country"),
        code,
    )
    bucket = data.appendix9_part2_by_group.setdefault(key, Appendix9Part2Totals())
    bucket.gross_income_eur += _to_decimal(record, "gross_income_eur")
    bucket.tax_base_eur += _to_decimal(record, "tax_base_eur")
    bucket.foreign_tax_eur += _to_decimal(record, "foreign_tax_eur")
    bucket.allowable_credit_eur += _to_decimal(record, "allowable_credit_eur")
    bucket.recognized_credit_eur += _to_decimal(record, "recognized_credit_eur")
    ref = _to_text(record, "document_ref")
    if ref:
        bucket.document_refs.add(ref)


def _aggregate_record(record: AppendixRecord, data: AggregatedAppendices) -> None:
    if record.appendix == "5":
        _aggregate_appendix5(record, data)
        return
    if record.appendix == "13":
        _aggregate_appendix13(record, data)
        return
    if record.appendix == "6":
        _aggregate_appendix6(record, data)
        return
    if record.appendix == "8":
        _aggregate_appendix8(record, data)
        return
    if record.appendix == "9":
        _aggregate_appendix9(record, data)


def aggregate_appendix_records(results: list[TaxAnalysisResult]) -> AggregatedAppendices:
    data = AggregatedAppendices()
    for result in results:
        for record in result.appendices:
            _aggregate_record(record, data)
    return data


def _global_status(statuses: list[AnalyzerStatus]) -> AnalyzerStatus:
    if any(status == "ERROR" for status in statuses):
        return "ERROR"
    if any(status == "NEEDS_REVIEW" for status in statuses):
        return "NEEDS_REVIEW"
    if any(status == "WARNING" for status in statuses):
        return "WARNING"
    return "OK"


DetectedInputDisplay = tuple[Path, str, str]


def _render_detected_inputs(
    lines: list[str],
    detected_inputs: dict[str, list[Path]],
    detected_input_items: list[DetectedInputDisplay] | None = None,
) -> None:
    if not detected_inputs and not detected_input_items:
        return
    analyzer_aliases = set(detected_inputs)
    if detected_input_items is not None:
        analyzer_items = [
            (path, alias, reason)
            for path, alias, reason in detected_input_items
            if alias in analyzer_aliases
        ]
        auxiliary_items = [
            (path, alias, reason)
            for path, alias, reason in detected_input_items
            if alias not in analyzer_aliases
        ]
        if analyzer_items:
            lines.extend(["", "Analyzer inputs"])
            for path, alias, reason in analyzer_items:
                lines.append(f"- {_format_path(path)} -> {alias} ({reason})")
        if auxiliary_items:
            lines.extend(["", "Auxiliary/manual inputs"])
            for path, alias, reason in auxiliary_items:
                lines.append(f"- {_format_path(path)} -> {alias} ({reason})")
        return
    lines.extend(["", "Analyzer inputs"])
    for alias in sorted(detected_inputs):
        for path in detected_inputs[alias]:
            lines.append(f"- {_format_path(path)} -> {alias}")


def _render_ignored_inputs(lines: list[str], ignored_inputs: list[tuple[Path, str]]) -> None:
    if not ignored_inputs:
        return
    lines.extend(["", "Ignored inputs"])
    for path, reason in ignored_inputs:
        lines.append(f"- {_format_path(path)}: {reason}")


def _render_ignored_input_items(lines: list[str], ignored_input_items: list[DetectionItem] | None) -> None:
    if not ignored_input_items:
        return
    lines.extend(["", "Ignored input details"])
    for item in ignored_input_items:
        suffix = item.path.suffix or "<none>"
        lines.append(f"- path: {_format_path(item.path)}")
        lines.append(f"  reason: {item.reason}")
        lines.append(f"  extension: {suffix}")
        lines.append(f"  kind: {item.ignored_kind}")
        lines.append(f"  known_noise: {'yes' if item.known_noise else 'no'}")
        lines.append(f"  ordinary_unmatched: {'yes' if item.ignored_kind == 'ordinary_unmatched' else 'no'}")
        lines.append(
            "  related_to_supported_analyzer: "
            f"{'yes' if item.related_to_supported_analyzer else 'no'}"
        )
        lines.append(f"  analyzer_alias: {item.analyzer_alias or '-'}")
        lines.append(
            "  main_output_notice: "
            f"{'suppressed known-noise notice' if item.known_noise else 'visible in ignored input summary'}"
        )


def _display_alias_bg(alias: str) -> str:
    names = {
        "ibkr": "IBKR",
        "kraken": "Kraken",
        "coinbase": "Coinbase",
        "binance_futures": "Binance Futures",
        "crypto_com": "Crypto.com",
        "finexify": "Finexify",
        "karol": "Karol",
        "spb8-input": "СПБ-8 input",
        "spb8": "СПБ-8",
    }
    return names.get(alias, alias.replace("_", " ").title())


def _compact_input_label(path: Path) -> str:
    return path.name


def _render_generated_artifacts_main(analyzer_results: list[TaxAnalysisResult]) -> list[str]:
    entries: list[tuple[str, str, GeneratedArtifact]] = []
    seen: set[tuple[str, str, str]] = set()
    for result in analyzer_results:
        for artifact in _visible_generated_artifacts(result, main=True):
            path_text = _format_path(artifact.path)
            key = (result.analyzer_alias, result.input_path.name, path_text)
            if key in seen:
                continue
            seen.add(key)
            entries.append((result.analyzer_alias, result.input_path.name, artifact))
    if not entries:
        return []

    lines = ["Помощни файлове за проверка"]
    for alias, input_name, artifact in sorted(
        entries,
        key=lambda item: (_display_alias_bg(item[0]), item[1], _format_path(item[2].path)),
    ):
        lines.append(f"- {_display_alias_bg(alias)} — {input_name}")
        lines.append(f"  CSV файл за проверка на обработените редове: {_format_path(artifact.path)}")
    return lines


def _processed_input_inventory_lines(detected_input_items: list[DetectedInputDisplay] | None) -> list[str]:
    if not detected_input_items:
        return []
    grouped: dict[str, list[Path]] = defaultdict(list)
    for path, alias, _reason in detected_input_items:
        grouped[alias].append(path)
    lines = ["Анализирани входни файлове"]
    for alias in sorted(grouped):
        lines.append(f"- {_display_alias_bg(alias)}:")
        for path in sorted(grouped[alias], key=lambda item: item.name):
            lines.append(f"  - {_compact_input_label(path)}")
    return lines


def _ignored_input_summary_lines(ignored_input_items: list[DetectionItem] | None) -> list[str]:
    if ignored_input_items is None:
        return []
    visible = [
        item
        for item in ignored_input_items
        if not item.known_noise and item.ignored_kind != "include_pattern"
    ]
    related = [item for item in visible if item.related_to_supported_analyzer]
    if not ignored_input_items or not visible:
        return []
    lines = ["Игнорирани входни файлове"]
    lines.append(f"- Файлове, намерени в папката, но неанализирани: {len(visible)}")
    for item in sorted(visible, key=lambda value: value.path.name)[:10]:
        lines.append(f"- {item.path.name}")
    if len(visible) > 10:
        lines.append(f"- ... още {len(visible) - 10} файла")
    if related:
        lines.append(
            "ВНИМАНИЕ: "
            f"{len(related)} файл(а) изглеждат свързани с поддържан анализатор, "
            "но не бяха анализирани. Проверете diagnostics файла преди да използвате резултатите."
        )
    lines.append("- Пълните пътища и причините са в diagnostics файла.")
    return lines


def _run_completeness_risk(
    *,
    global_status: AnalyzerStatus,
    ignored_input_items: list[DetectionItem] | None,
) -> bool:
    if global_status in {"ERROR", "NEEDS_REVIEW", "WARNING"}:
        return True
    return any(item.related_to_supported_analyzer for item in ignored_input_items or [])


def _run_assumption_lines(
    *,
    tax_year: int,
    display_currency: str,
    analyzer_results: list[TaxAnalysisResult],
) -> list[str]:
    aliases = sorted({result.analyzer_alias for result in analyzer_results})
    lines = ["Настройки и данъчни допускания"]
    lines.append(f"- Данъчна година: {tax_year}")
    lines.append("- Изчислителна валута: EUR.")
    lines.append(f"- Валута за визуализация в TXT: {display_currency}.")
    if aliases:
        lines.append("- Използвани анализатори/платформи: " + ", ".join(_display_alias_bg(alias) for alias in aliases) + ".")
    return lines


def _merge_status(base: AnalyzerStatus, incoming: AnalyzerStatus) -> AnalyzerStatus:
    priority = {
        "OK": 0,
        "WARNING": 1,
        "NEEDS_REVIEW": 2,
        "ERROR": 3,
    }
    return incoming if priority[incoming] > priority[base] else base


def _aggregate_policy_notes(analyzer_results: list[TaxAnalysisResult]) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for result in analyzer_results:
        for note in result.policy_notes:
            cleaned = note.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            notes.append(cleaned)
    return notes


def _aggregate_main_report_notes(analyzer_results: list[TaxAnalysisResult]) -> list[MainReportNote]:
    notes: list[MainReportNote] = []
    seen: set[tuple[str, str]] = set()
    for result in analyzer_results:
        for note in result.main_report_notes:
            if note.category == "duplicate_individual_context":
                continue
            section_title = note.section_title.strip()
            text = note.text.strip()
            if not section_title or not text:
                continue
            key = (section_title, text)
            if key in seen:
                continue
            seen.add(key)
            notes.append(note)
    return notes


_IBKR_MARKET_SECTION = "IBKR — класификация на пазари"


def _split_bg_set(value: str) -> set[str]:
    cleaned = value.strip().removesuffix(".").strip()
    if not cleaned or cleaned == "няма":
        return set()
    return {item.strip() for item in cleaned.split(",") if item.strip() and item.strip() != "няма"}


def _append_unique_ordered(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _render_ibkr_market_notes(notes: list[MainReportNote]) -> list[str]:
    market_notes = [note.text.strip() for note in notes if note.section_title.strip() == _IBKR_MARKET_SECTION]
    if not market_notes:
        return []

    tax_modes: list[str] = []
    classification_modes: list[str] = []
    date_formats: list[str] = []
    eu_regulated: set[str] = set()
    eu_other: set[str] = set()
    non_eu: set[str] = set()
    unmapped: set[str] = set()
    invalid: set[str] = set()

    for text in market_notes:
        if text.startswith("Режим за данъчно освобождаване: "):
            _append_unique_ordered(tax_modes, text)
            continue
        if text.startswith("Режим за класификация на пазарите: "):
            _append_unique_ordered(classification_modes, text)
            continue
        if text.startswith("Разпознати регулирани пазари от ЕС в отчета: "):
            eu_regulated.update(_split_bg_set(text.split(": ", 1)[1]))
            continue
        if text.startswith("Разпознати нерегулирани/други пазари от ЕС за целите на данъчното освобождаване: "):
            eu_other.update(_split_bg_set(text.split(": ", 1)[1]))
            continue
        if text.startswith("Разпознати пазари извън ЕС: "):
            non_eu.update(_split_bg_set(text.split(": ", 1)[1]))
            continue
        if text.startswith("Неразпознати пазари: "):
            rest = text.removeprefix("Неразпознати пазари: ")
            parts = rest.split("; невалидни/нечетими стойности: ", 1)
            unmapped.update(_split_bg_set(parts[0]))
            if len(parts) > 1:
                invalid.update(_split_bg_set(parts[1]))
            continue
        if text.startswith("Разпознат формат на датите в IBKR отчета: "):
            _append_unique_ordered(date_formats, text)

    lines = [_IBKR_MARKET_SECTION]
    lines.extend(tax_modes)
    lines.extend(classification_modes)
    lines.append(f"Разпознати регулирани пазари от ЕС в отчета: {_fmt_set_bg(eu_regulated)}.")
    lines.append(
        "Разпознати нерегулирани/други пазари от ЕС за целите на данъчното освобождаване: "
        f"{_fmt_set_bg(eu_other)}."
    )
    lines.append(f"Разпознати пазари извън ЕС: {_fmt_set_bg(non_eu)}.")
    lines.append(
        f"Неразпознати пазари: {_fmt_set_bg(unmapped)}; "
        f"невалидни/нечетими стойности: {_fmt_set_bg(invalid)}."
    )
    lines.extend(date_formats)
    return lines


def _render_specific_analysis_notes(section_notes: list[str]) -> list[str]:
    if not section_notes:
        return []
    secondary_omitted: list[str] = []
    other_notes: list[str] = []
    seen: set[str] = set()
    for text in section_notes:
        if text in seen:
            continue
        seen.add(text)
        if text.startswith(("Estateguru: агрегираният резултат от вторичен пазар", "Iuvo: агрегираният резултат от вторичен пазар")):
            secondary_omitted.append(text.split(":", 1)[0])
        else:
            other_notes.append(text)

    rendered: list[str] = []
    if secondary_omitted:
        platforms = sorted(set(secondary_omitted))
        platform_text = _format_bg_list(platforms)
        rendered.append(
            f"P2P вторичен пазар: за {platform_text} агрегираният резултат е <= 0 "
            "и не е включен като доход по Приложение 6, код 606."
        )
    rendered.extend(other_notes)
    return rendered


def _format_bg_list(values: list[str]) -> str:
    if len(values) <= 1:
        return values[0] if values else ""
    return ", ".join(values[:-1]) + " и " + values[-1]


def _fmt_set_bg(values: set[str]) -> str:
    cleaned = sorted(value for value in values if value.strip())
    return ", ".join(cleaned) if cleaned else "няма"


def _parse_csv_decimal_note(note: MainReportNote) -> dict[str, str]:
    if not note.text.startswith("CSV_NUMBER_FORMAT|"):
        return {}
    result: dict[str, str] = {"analyzer": note.analyzer_alias or ""}
    for part in note.text.split("|")[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key] = value
    return result


def _render_csv_decimal_notes(notes: list[MainReportNote]) -> list[str]:
    items = [_parse_csv_decimal_note(note) for note in notes if note.category == "csv_decimal_format"]
    items = [item for item in items if item]
    if not items:
        return []
    deviations = [
        item
        for item in items
        if item.get("separator") != "dot" or (item.get("source") == "explicit" and item.get("separator") != "dot")
    ]
    if not deviations:
        return ['Числов формат на CSV файловете: използван е стандартният формат с десетичен разделител ".".']

    lines = ["Отклонения от стандартния числов формат:"]
    for item in sorted(deviations, key=lambda value: value.get("analyzer", "")):
        analyzer = item.get("analyzer") or item.get("analyzer_alias") or "CSV"
        separator = "," if item.get("separator") == "comma" else "."
        if item.get("source") == "explicit":
            lines.append(
                f'{analyzer}: десетичен разделител "{separator}" зададен ръчно чрез '
                f"--{analyzer}-csv-decimal-separator {item.get('separator')}."
            )
        elif item.get("source") == "auto":
            lines.append(f'{analyzer}: автоматично разпознат десетичен разделител "{separator}".')
        else:
            lines.append(f'{analyzer}: използван е десетичен разделител "{separator}".')
    return lines


def _render_top_main_report_notes(notes: list[MainReportNote]) -> list[str]:
    top_notes = [note for note in notes if note.category != "methodology"]
    if not top_notes:
        return []
    csv_decimal_lines = _render_csv_decimal_notes(top_notes)
    top_notes = [note for note in top_notes if note.category != "csv_decimal_format"]
    grouped: dict[str, list[str]] = defaultdict(list)
    seen_by_section: dict[str, set[str]] = defaultdict(set)
    ordered_section_titles: list[str] = []
    ibkr_market_lines = _render_ibkr_market_notes(top_notes)
    ibkr_market_inserted = False
    for note in top_notes:
        section_title = note.section_title.strip()
        if section_title == _IBKR_MARKET_SECTION:
            if ibkr_market_lines and not ibkr_market_inserted:
                grouped[section_title] = ibkr_market_lines[1:]
                ordered_section_titles.append(section_title)
                ibkr_market_inserted = True
            continue
        text = note.text.strip()
        if not section_title or not text:
            continue
        seen = seen_by_section[section_title]
        if text in seen:
            continue
        if not seen:
            if section_title == "Настройки и данъчни допускания":
                ordered_section_titles.insert(0, section_title)
            else:
                ordered_section_titles.append(section_title)
        seen.add(text)
        grouped[section_title].append(text)
    lines: list[str] = []
    if csv_decimal_lines and "Настройки и данъчни допускания" not in ordered_section_titles:
        ordered_section_titles.insert(0, "Настройки и данъчни допускания")
        grouped["Настройки и данъчни допускания"] = []
    for section_title in ordered_section_titles:
        if lines:
            lines.append("")
        lines.append(section_title)
        section_texts = grouped[section_title]
        if section_title == "Настройки и данъчни допускания" and csv_decimal_lines:
            section_texts = [*section_texts, *csv_decimal_lines]
        if section_title == "Специфични бележки от анализа":
            section_texts = _render_specific_analysis_notes(section_texts)
        for text in section_texts:
            text_lines = text.splitlines()
            if not text_lines:
                continue
            lines.append(f"- {text_lines[0]}")
            lines.extend(f"  {line}" for line in text_lines[1:])
    return lines


def _render_methodology_report_notes(notes: list[MainReportNote]) -> list[str]:
    methodology_notes = [note for note in notes if note.category == "methodology"]
    if not methodology_notes:
        return []
    grouped: dict[str, list[str]] = defaultdict(list)
    seen_by_section: dict[str, set[str]] = defaultdict(set)
    ordered_section_titles: list[str] = []
    for note in methodology_notes:
        section_title = note.section_title.strip()
        text = note.text.strip()
        if not section_title or not text:
            continue
        seen = seen_by_section[section_title]
        if text in seen:
            continue
        if not seen:
            ordered_section_titles.append(section_title)
        seen.add(text)
        grouped[section_title].append(text)
    lines = ["Методологични бележки"]
    for section_title in ordered_section_titles:
        lines.append("")
        lines.append(section_title)
        lines.extend(f"- {text}" for text in grouped[section_title])
    return lines


def _detail_category_for_line(line: str, *, current_block: str | None = None) -> str:
    stripped = line.strip()
    lowered = stripped.casefold()
    if "debug" in lowered or "_debug" in lowered or "artifact" in lowered or "sanity report:" in lowered:
        return "Debug artifacts"
    if current_block == "Validation / sanity checks":
        return "Validation / sanity checks"
    if (
        "sanity" in lowered
        or lowered.startswith("- checked ")
        or "validation" in lowered
        or "mismatch" in lowered
    ):
        return "Validation / sanity checks"
    if (
        "date format" in lowered
        or "opening_state" in lowered
        or "opening state" in lowered
        or "начално състояние" in lowered
        or "report alias" in lowered
        or "detection reason" in lowered
        or "full input path" in lowered
    ):
        return "Input interpretation"
    if (
        "processed" in lowered
        or "included" in lowered
        or "ignored" in lowered
        or "skipped" in lowered
        or "rows" in lowered
        or "count" in lowered
        or "total" in lowered
        or "paid eur" in lowered
        or "rate:" in lowered
        or "редове" in lowered
        or "брой" in lowered
        or "сума" in lowered
    ) and " policy:" not in lowered and " policy" not in lowered:
        return "Tax calculation summary"
    if (
        "policy" in lowered
        or "режим" in lowered
        or "третира" in lowered
        or "realization" in lowered
        or "classification" in lowered
        or "tax-exempt" in lowered
        or "appendix 5 mapping" in lowered
        or "appendix 8 dividend list mode" in lowered
        or "ledger" in lowered
        or "state reconstruction" in lowered
        or "markets" in lowered
        or "market classification" in lowered
        or "invalid/unreadable market" in lowered
        or "selected mode:" in lowered
        or "пазар" in lowered
        or "cfd" in lowered
        or "pil" in lowered
        or "futures" in lowered
        or "option" in lowered
        or "опции" in lowered
        or "фючърси" in lowered
    ):
        return "Tax treatment decisions"
    if (
        "review status" in lowered
        or "override" in lowered
    ):
        return "Analyzer-specific audit"
    return "Analyzer-specific audit"


def _add_grouped_detail_line(
    grouped: dict[str, list[str]],
    seen: dict[str, set[str]],
    category: str,
    line: str,
) -> None:
    cleaned = line.rstrip()
    if not cleaned or cleaned in {"Audit Data", "Sanity Check", "Technical Details", "Analyzer technical details"}:
        return
    if cleaned.startswith("- full input path:") or cleaned.startswith("- analyzer alias:"):
        return
    if cleaned in seen[category]:
        return
    seen[category].add(cleaned)
    grouped[category].append(cleaned)


def _group_report_details(result: TaxAnalysisResult) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {
        "Input interpretation": [],
        "Tax calculation summary": [],
        "Tax treatment decisions": [],
        "Analyzer-specific audit": [],
        "Validation / sanity checks": [],
        "Debug artifacts": [],
    }
    seen: dict[str, set[str]] = {category: set() for category in grouped}
    for line in result.policy_audit_lines:
        category = _detail_category_for_line(line)
        _add_grouped_detail_line(grouped, seen, category, line)
    for detail in result.report_details:
        if detail.category == "complete_individual_diagnostics":
            continue
        if detail.visibility == "MAIN":
            continue
        if detail.category == "debug" or detail.visibility == "DEBUG":
            for line in detail.lines:
                _add_grouped_detail_line(grouped, seen, "Debug artifacts", line)
            continue
        if detail.category == "audit" or detail.key == "input_detection":
            for line in detail.lines:
                _add_grouped_detail_line(grouped, seen, "Input interpretation", line)
            continue
        current_block: str | None = None
        for line in detail.lines:
            if line.strip() == "Sanity Check":
                current_block = "Validation / sanity checks"
                continue
            if line.strip() == "Audit Data":
                current_block = None
                continue
            category = _detail_category_for_line(line, current_block=current_block)
            _add_grouped_detail_line(grouped, seen, category, line)
    return grouped


def _render_result_diagnostics_summary(result: TaxAnalysisResult) -> list[str]:
    diagnostics = normalize_diagnostics(result.diagnostics)
    if not diagnostics:
        return []
    lines = ["Warnings/errors for this input"]
    for diagnostic in diagnostics:
        code = f" {diagnostic.code}" if diagnostic.code else ""
        lines.append(f"- [{diagnostic.severity}]{code}: {diagnostic.message}")
    return lines


def _render_per_input_details(lines: list[str], analyzer_results: list[TaxAnalysisResult]) -> None:
    if not analyzer_results:
        return
    lines.extend(["", "Per-input diagnostics summary"])
    for result in sorted(analyzer_results, key=lambda item: (item.analyzer_alias, _format_path(item.input_path))):
        lines.append("")
        lines.append(f"{result.analyzer_alias}: {result.input_path.name}")
        lines.append(f"- input_path: {_format_path(result.input_path)}")
        lines.append(f"- analyzer_alias: {result.analyzer_alias}")
        lines.append(f"- status: {result.status}")
        declaration_path = result.output_paths.get("declaration_txt")
        diagnostics_path = result.output_paths.get("diagnostics_txt")
        if declaration_path is not None:
            lines.append(f"- declaration_path: {_format_path(declaration_path)}")
            lines.append(f"- output_dir: {_format_path(declaration_path.parent)}")
        if diagnostics_path is not None:
            lines.append(f"- diagnostics_path: {_format_path(diagnostics_path)}")
        artifact_lines = dict.fromkeys(
            f"- {artifact.artifact_type}: {_format_path(artifact.path)}"
            for artifact in _visible_generated_artifacts(result, main=False)
        )
        lines.extend(artifact_lines)
        grouped = _group_report_details(result)
        for section_title in (
            "Input interpretation",
            "Tax calculation summary",
            "Tax treatment decisions",
            "Validation / sanity checks",
            "Analyzer-specific audit",
            "Debug artifacts",
        ):
            section_lines = grouped[section_title]
            if not section_lines:
                continue
            lines.append(section_title)
            lines.extend(f"  {line}" for line in section_lines)
            if section_title == "Debug artifacts":
                if not any("Debug artifacts are verification-only" in line for line in section_lines):
                    lines.append("  - Debug artifacts are verification-only and not production tax outputs.")
        diagnostic_lines = _render_result_diagnostics_summary(result)
        if diagnostic_lines:
            lines.extend(diagnostic_lines)


def _render_main_report_details(analyzer_results: list[TaxAnalysisResult]) -> list[str]:
    details = [
        detail
        for result in analyzer_results
        for detail in result.report_details
        if detail.visibility == "MAIN"
    ]
    if not details:
        return []
    lines = ["Допълнителни бележки от анализаторите"]
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for detail in sorted(details, key=lambda item: (item.title, item.key)):
        key = (detail.title, detail.lines)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {detail.title}:")
        lines.extend(f"  - {line}" for line in detail.lines if line.strip())
    return lines


def _render_per_analyzer_status(
    lines: list[str],
    *,
    run_statuses: list[AnalyzerRunStatus],
) -> None:
    if not run_statuses:
        return
    lines.extend(["", "Per-analyzer status"])
    grouped: dict[str, list[AnalyzerRunStatus]] = defaultdict(list)
    for status in run_statuses:
        grouped[status.analyzer_alias].append(status)
    for alias in sorted(grouped):
        lines.append(alias)
        for run_status in grouped[alias]:
            lines.append(f"- {run_status.status}")
            if run_status.source_path:
                lines.append(f"  source: {_format_path_value(run_status.source_path)}")
            if run_status.declaration_path:
                lines.append(f"  declaration: {_format_path_value(run_status.declaration_path)}")
            if run_status.reason:
                lines.append(f"  reason: {run_status.reason}")


def _format_path_value(path: Path | str) -> str:
    if isinstance(path, Path):
        return _format_path(path)
    return str(path)


def _short_reason_for_diagnostic(diagnostic: AnalysisDiagnostic) -> str:
    if diagnostic.code == "MISSING_REQUIRED_COLUMNS":
        return "missing required columns"
    if diagnostic.code == "SPB8_MISSING_VALUES":
        return "missing SPB-8 values"
    if diagnostic.code == "FOREX_ROWS_IGNORED":
        return "forex rows ignored"
    if diagnostic.code == "UNSUPPORTED_TRADES_ROWS":
        return "unsupported Trades rows skipped"
    if diagnostic.code == "UNKNOWN_DIVIDEND_ROWS":
        return "unknown dividend/payment-in-lieu rows"
    if diagnostic.code == "CRYPTO_UNSUPPORTED_TRANSACTION_TYPE":
        return "unsupported crypto transaction types"
    if diagnostic.code == "CRYPTO_MISSING_COST_BASIS":
        return "crypto cost basis review"
    if diagnostic.code == "CRYPTO_ROW_REQUIRES_REVIEW":
        return "crypto rows require review"
    if diagnostic.code == "FUND_UNSUPPORTED_ROW_TYPE":
        return "unsupported fund rows"
    if diagnostic.code == "FUND_FX_LOOKUP_FALLBACK":
        return "fund FX fallback used"
    if diagnostic.code == "P2P_REPORTING_YEAR_MISMATCH":
        return "reporting year mismatch"
    if diagnostic.code == "P2P_SECONDARY_MARKET_REVIEW_REQUIRED":
        return "secondary market review required"
    if diagnostic.code == "P2P_AMOUNT_OMITTED":
        return "P2P amount omitted"
    if diagnostic.code == "BINANCE_FUTURES_UNSUPPORTED_INCOME_TYPE":
        return "unsupported Binance futures income type"
    if diagnostic.code == "BINANCE_FUTURES_FUNDING_FEE_REVIEW_REQUIRED":
        return "Binance futures funding fee review"
    if diagnostic.code == "IBKR_INCOMPLETE_CLOSED_LOTS":
        return "incomplete IBKR statement missing ClosedLot rows"
    if diagnostic.code == "IBKR_FUTURES_MISSING_MTM_ROWS":
        return "missing Futures MTM rows"
    if diagnostic.code == "IBKR_FUTURES_MISSING_MTM_COLUMNS":
        return "missing Futures MTM columns"
    if diagnostic.code == "IBKR_FUTURES_MTM_ARITHMETIC_MISMATCH":
        return "Futures MTM arithmetic mismatch"
    if diagnostic.code == "IBKR_FUTURES_MTM_OTHER_INCLUDED":
        return "Futures MTM Other included"
    if diagnostic.code == "IBKR_SPB8_REVIEW_REQUIRED":
        return "IBKR SPB-8 review required"
    if diagnostic.code in {"UNCLASSIFIED_WARNING_GROUP", "UNCLASSIFIED_MANUAL_REVIEW_GROUP"}:
        examples = diagnostic.params.get("examples")
        if isinstance(examples, list) and examples:
            return str(examples[0]).splitlines()[0][:100]
        return diagnostic.code.lower().replace("_", " ")
    message = diagnostic.message.strip()
    if message.startswith("reporting year in PDF (") and "differs from requested tax year" in message:
        return "reporting year mismatch"
    if diagnostic.code:
        return diagnostic.code.lower().replace("_", " ")
    return message.splitlines()[0][:100]


def _run_statuses_from_results(
    analyzer_results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
    analyzer_error_diagnostics: list[AnalysisDiagnostic],
) -> list[AnalyzerRunStatus]:
    run_statuses: list[AnalyzerRunStatus] = []
    for result in analyzer_results:
        diagnostics = normalize_diagnostics(result.diagnostics)
        run_statuses.append(
            AnalyzerRunStatus(
                analyzer_alias=result.analyzer_alias,
                status=result.status,
                source_path=result.input_path,
                declaration_path=result.output_paths.get("declaration_txt"),
                reason=_short_reason_for_diagnostic(diagnostics[0]) if result.status != "OK" and diagnostics else "",
            )
        )
    for diagnostic in analyzer_error_diagnostics:
        source_path = diagnostic.params.get("path") or diagnostic.params.get("source_file") or diagnostic.params.get("filename")
        run_statuses.append(
            AnalyzerRunStatus(
                analyzer_alias=diagnostic.analyzer_alias,
                status="ERROR",
                source_path=str(source_path) if source_path else None,
                reason=_short_reason_for_diagnostic(diagnostic),
            )
        )
    for alias, errors in analyzer_errors.items():
        if any(diagnostic.analyzer_alias == alias for diagnostic in analyzer_error_diagnostics):
            continue
        for error in errors:
            run_statuses.append(
                AnalyzerRunStatus(
                    analyzer_alias=alias,
                    status="ERROR",
                    reason=error.split(":", 1)[0] if ":" in error else "analyzer error",
                )
            )
    return sorted(
        run_statuses,
        key=lambda item: (
            item.analyzer_alias,
            str(item.source_path or ""),
            str(item.declaration_path or ""),
            item.status,
        ),
    )


def _status_reason_lines(
    *,
    global_status: AnalyzerStatus,
    analyzer_results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
    spb8_notes: list[str] | None,
) -> list[str]:
    if global_status == "ERROR":
        reasons = [f"{alias}: {error}" for alias, errors in sorted(analyzer_errors.items()) for error in errors]
        reasons.extend(
            f"{diagnostic.analyzer_alias}: {diagnostic.message}"
            for result in analyzer_results
            for diagnostic in result.diagnostics
            if diagnostic.severity == "ERROR"
        )
        return ["Причина за ERROR:", *(f"- {reason}" for reason in reasons)]
    if global_status == "NEEDS_REVIEW":
        reasons = [
            f"{diagnostic.analyzer_alias}: {diagnostic.message}"
            for result in analyzer_results
            for diagnostic in result.diagnostics
            if diagnostic.severity == "MANUAL_REVIEW"
        ]
        reasons.extend(note for note in spb8_notes or [] if note.startswith("СПБ-8: липсва стойност"))
        return ["Причина за ръчна проверка:", *(f"- {reason}" for reason in reasons)]
    return []


def _build_appendix5_lines(
    aggregated: AggregatedAppendices,
    *,
    money_context,
) -> list[str]:
    entries = [
        Appendix5Table2Entry(
            code=(code or "-"),
            sale_value=Money(bucket.sale_value_eur, "EUR"),
            acquisition_value=Money(bucket.acquisition_value_eur, "EUR"),
            profit=Money(bucket.profit_eur, "EUR"),
            loss=Money(bucket.loss_eur, "EUR"),
            net_result=Money(bucket.net_result_eur, "EUR"),
            trade_count=bucket.trade_count,
        )
        for (_table, code), bucket in sorted(aggregated.appendix5_by_code.items())
    ]
    return render_appendix5_table2(entries, money_context=money_context)


def _build_appendix13_lines(
    aggregated: AggregatedAppendices,
    *,
    money_context,
) -> list[str]:
    entries = [
        Appendix13Part2Entry(
            code=(code or "-"),
            gross_income=Money(bucket.gross_income_eur, "EUR"),
            acquisition_value=Money(bucket.acquisition_value_eur, "EUR"),
            profit=Money(bucket.profit_eur, "EUR"),
            loss=Money(bucket.loss_eur, "EUR"),
            net_result=Money(bucket.net_result_eur, "EUR"),
            trade_count=bucket.trade_count,
        )
        for (_part, _table, code), bucket in sorted(aggregated.appendix13_by_code.items())
    ]
    return render_appendix13_part2(entries, money_context=money_context)


def _build_appendix6_lines(
    aggregated: AggregatedAppendices,
    *,
    money_context,
) -> list[str]:
    data = Appendix6RenderData(
        part1_company_rows=[
            Appendix6Part1CompanyRow(
                payer_name=payer,
                payer_eik=eik,
                code=code,
                amount=Money(amount, "EUR"),
            )
            for (eik, payer, code), amount in sorted(aggregated.appendix6_part1_company.items())
            if amount != ZERO
        ],
        part1_code_totals=[
            Appendix6Part1CodeTotal(code=code, amount=Money(amount, "EUR"))
            for code, amount in sorted(aggregated.appendix6_part1_total_by_code.items())
        ],
        part2_taxable_totals=[
            Appendix6Part2TaxableTotal(code=code, amount=Money(amount, "EUR"))
            for code, amount in sorted(aggregated.appendix6_part2_taxable_by_code.items())
        ],
        part3_withheld_tax=Money(aggregated.appendix6_part3_withheld_tax, "EUR"),
    )
    return render_appendix6(data, money_context=money_context)


def _build_appendix8_lines(
    aggregated: AggregatedAppendices,
    *,
    tax_year: int,
    money_context,
) -> list[str]:
    acquisition_date = f"31.12.{tax_year}"
    data = Appendix8RenderData(
        part1_rows=[
            Appendix8Part1Row(
                asset_type=asset_type,
                country=country,
                quantity=format(bucket.quantity, "f"),
                acquisition_date=acquisition_date,
                acquisition_native=Money(bucket.acquisition_native, currency or "-"),
                acquisition_eur=Money(bucket.acquisition_eur, "EUR"),
                native_currency_label=currency or "-",
            )
            for (asset_type, country, currency), bucket in sorted(aggregated.appendix8_part1_by_group.items())
        ],
        part3_rows=[
            Appendix8Part3Row(
                payer=payer,
                country=country,
                code=code,
                treaty_method=method,
                gross_income=Money(bucket.gross_income_eur, "EUR"),
                foreign_tax=Money(bucket.foreign_tax_eur, "EUR"),
                allowable_credit=Money(bucket.allowable_credit_eur, "EUR"),
                recognized_credit=Money(bucket.recognized_credit_eur, "EUR"),
                tax_due=Money(bucket.tax_due_eur, "EUR"),
            )
            for (payer, country, code, method), bucket in sorted(aggregated.appendix8_part3_by_group.items())
        ],
    )
    return render_appendix8(data, money_context=money_context)


def _append_appendix8_part1_note(
    lines: list[str],
    *,
    aggregated: AggregatedAppendices,
) -> None:
    if not aggregated.appendix8_part1_by_group:
        return
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(appendix8_part1_declarative_note_lines())


def _build_appendix9_lines(
    aggregated: AggregatedAppendices,
    *,
    money_context,
) -> list[str]:
    rows = [
        Appendix9Part2Row(
            country=country,
            code=code,
            gross_income=Money(bucket.gross_income_eur, "EUR"),
            tax_base=Money(bucket.tax_base_eur, "EUR"),
            foreign_tax=Money(bucket.foreign_tax_eur, "EUR"),
            allowable_credit=Money(bucket.allowable_credit_eur, "EUR"),
            recognized_credit=Money(bucket.recognized_credit_eur, "EUR"),
            document_ref=", ".join(sorted(bucket.document_refs)) if bucket.document_refs else "",
        )
        for (country, code), bucket in sorted(aggregated.appendix9_part2_by_group.items())
    ]
    return render_appendix9_part2(rows, money_context=money_context)


def render_aggregated_report(
    *,
    tax_year: int,
    detected_inputs: dict[str, list[Path]],
    ignored_inputs: list[tuple[Path, str]],
    analyzer_results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
    detected_input_items: list[DetectedInputDisplay] | None = None,
    analyzer_error_diagnostics: list[AnalysisDiagnostic] | None = None,
    ignored_input_items: list[DetectionItem] | None = None,
    display_currency: str = "EUR",
    cache_dir: str | Path | None = None,
    spb8_rows: list[SPB8Row] | None = None,
    spb8_notes: list[str] | None = None,
    spb8_needs_review: bool = False,
) -> str:
    render_context = build_render_context(
        tax_year=tax_year,
        display_currency=display_currency,
        cache_dir=cache_dir,
    )
    money_context = render_context.money_context
    statuses: dict[str, AnalyzerStatus] = {}
    for result in analyzer_results:
        previous = statuses.get(result.analyzer_alias, "OK")
        statuses[result.analyzer_alias] = _merge_status(previous, result.status)
    for alias, errors in analyzer_errors.items():
        if errors:
            statuses[alias] = "ERROR"

    global_status = _global_status(list(statuses.values()))
    if spb8_needs_review and global_status in {"OK", "WARNING"}:
        global_status = "NEEDS_REVIEW"
    if any(item.related_to_supported_analyzer for item in ignored_input_items or []) and global_status == "OK":
        global_status = "WARNING"
    aggregated = aggregate_appendix_records(analyzer_results)
    aggregated_spb8_rows = aggregate_spb8_rows(spb8_rows or [])
    main_report_notes = _aggregate_main_report_notes(analyzer_results)
    policy_notes = _aggregate_policy_notes(analyzer_results)

    lines: list[str] = [_status_banner(global_status)]
    status_reasons = _status_reason_lines(
        global_status=global_status,
        analyzer_results=analyzer_results,
        analyzer_errors=analyzer_errors,
        spb8_notes=spb8_notes,
    )
    if status_reasons:
        lines.extend(["", *status_reasons])
    if _run_completeness_risk(global_status=global_status, ignored_input_items=ignored_input_items):
        lines.extend(["", "ВНИМАНИЕ: Отчетът може да е непълен или да изисква ръчна проверка."])
    lines.append("")
    top_settings_lines = _run_assumption_lines(
        tax_year=tax_year,
        display_currency=display_currency,
        analyzer_results=analyzer_results,
    )
    top_main_report_note_lines = _render_top_main_report_notes(main_report_notes)
    if top_main_report_note_lines:
        if top_main_report_note_lines[0] == "Настройки и данъчни допускания":
            top_main_report_note_lines = top_main_report_note_lines[1:]
            top_settings_lines.extend(top_main_report_note_lines)
        else:
            top_settings_lines.extend(["", *top_main_report_note_lines])
    top_detail_lines = _render_main_report_details(analyzer_results)
    if top_detail_lines:
        top_settings_lines.extend(["", *top_detail_lines])

    for section_lines in (
        _processed_input_inventory_lines(detected_input_items),
        _ignored_input_summary_lines(ignored_input_items),
        top_settings_lines,
    ):
        if not section_lines:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(section_lines)
    if lines and lines[-1] != "":
        lines.append("")
    methodology_report_note_lines = _render_methodology_report_notes(main_report_notes)
    for section_lines in (
        _build_appendix5_lines(aggregated, money_context=money_context),
        _build_appendix13_lines(aggregated, money_context=money_context),
        _build_appendix6_lines(aggregated, money_context=money_context),
        _build_appendix8_lines(
            aggregated,
            tax_year=tax_year,
            money_context=money_context,
        ),
        _build_appendix9_lines(aggregated, money_context=money_context),
        render_spb8_section(
            aggregated_spb8_rows,
            notes=spb8_notes,
            aggregate=bool(aggregated_spb8_rows),
        ),
        _render_generated_artifacts_main(analyzer_results),
        ["CFD и PIL", *(f"- {note}" for note in policy_notes)] if policy_notes else [],
        methodology_report_note_lines,
    ):
        if not section_lines:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(section_lines)

    _append_appendix8_part1_note(lines, aggregated=aggregated)

    result_diagnostics = [diagnostic for result in analyzer_results for diagnostic in normalize_diagnostics(result.diagnostics)]
    extra_diagnostics = analyzer_error_diagnostics or []
    if any(diagnostic.code == "SPB8_MISSING_VALUES" for diagnostic in extra_diagnostics):
        result_diagnostics = [
            diagnostic for diagnostic in result_diagnostics if diagnostic.code != "SPB8_MISSING_VALUES"
        ]
    all_report_diagnostics = [*result_diagnostics, *extra_diagnostics]
    analyzer_input_count = sum(len(paths) for paths in detected_inputs.values())
    auxiliary_input_count = max(0, len(detected_input_items or []) - analyzer_input_count)
    successful_analyzer_input_count = sum(1 for result in analyzer_results if result.status != "ERROR")
    failed_analyzer_input_count = sum(len(errors) for errors in analyzer_errors.values())
    failed_analyzer_input_count += sum(1 for result in analyzer_results if result.status == "ERROR")
    warning_count = sum(1 for diagnostic in all_report_diagnostics if diagnostic.severity == "WARNING")
    error_count = sum(1 for diagnostic in all_report_diagnostics if diagnostic.severity == "ERROR")
    technical_lines: list[str] = [
        "Aggregate calculation summary",
        f"- tax year: {tax_year}",
        f"- global status: {global_status}",
        f"- analyzer input count: {analyzer_input_count}",
        f"- auxiliary input count: {auxiliary_input_count}",
        f"- ignored input count: {len(ignored_inputs)}",
        f"- successful analyzer input count: {successful_analyzer_input_count}",
        f"- failed analyzer input count: {failed_analyzer_input_count}",
        f"- warning count: {warning_count}",
        f"- error count: {error_count}",
    ]
    technical_lines.extend(f"- {line}" for line in display_currency_technical_lines(money_context))
    technical_lines.extend(["", "Input inventory"])
    _render_detected_inputs(technical_lines, detected_inputs, detected_input_items)
    _render_ignored_inputs(technical_lines, ignored_inputs)
    _render_ignored_input_items(technical_lines, ignored_input_items)
    _render_per_analyzer_status(
        technical_lines,
        run_statuses=_run_statuses_from_results(analyzer_results, analyzer_errors, analyzer_error_diagnostics or []),
    )
    _render_per_input_details(technical_lines, analyzer_results)

    append_technical_details(lines, technical_lines)
    return "\n".join(lines).rstrip() + "\n"
