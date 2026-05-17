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
    render_spb8_notes_section,
    render_spb8_section,
)

from .contracts import AnalysisDiagnostic, AnalyzerStatus, AppendixRecord, TaxAnalysisResult

ZERO = Decimal("0")


def _format_path(path: Path) -> str:
    return str(path.expanduser().resolve())


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


def _render_detected_inputs(lines: list[str], detected_inputs: dict[str, list[Path]]) -> None:
    if not detected_inputs:
        return
    lines.extend(["", "Detected inputs"])
    for alias in sorted(detected_inputs):
        for path in detected_inputs[alias]:
            lines.append(f"- {alias}: {_format_path(path)}")


def _render_ignored_inputs(lines: list[str], ignored_inputs: list[tuple[Path, str]]) -> None:
    if not ignored_inputs:
        return
    lines.extend(["", "Ignored inputs"])
    for path, reason in ignored_inputs:
        lines.append(f"- {_format_path(path)}: {reason}")


def _merge_status(base: AnalyzerStatus, incoming: AnalyzerStatus) -> AnalyzerStatus:
    priority = {
        "OK": 0,
        "WARNING": 1,
        "NEEDS_REVIEW": 2,
        "ERROR": 3,
    }
    return incoming if priority[incoming] > priority[base] else base


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
    analyzer_error_diagnostics: list[AnalysisDiagnostic] | None = None,
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
    aggregated = aggregate_appendix_records(analyzer_results)
    aggregated_spb8_rows = aggregate_spb8_rows(spb8_rows or [])

    lines: list[str] = [_status_banner(global_status)]
    status_reasons = _status_reason_lines(
        global_status=global_status,
        analyzer_results=analyzer_results,
        analyzer_errors=analyzer_errors,
        spb8_notes=spb8_notes,
    )
    if status_reasons:
        lines.extend(["", *status_reasons])
    lines.append("")
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
        render_spb8_section(aggregated_spb8_rows),
        render_spb8_notes_section(spb8_notes, aggregate=bool(aggregated_spb8_rows)),
    ):
        if not section_lines:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(section_lines)

    _append_appendix8_part1_note(lines, aggregated=aggregated)

    technical_lines: list[str] = [
        "Aggregated Report",
        f"- tax year: {tax_year}",
        f"- global status: {global_status}",
    ]
    technical_lines.extend(f"- {line}" for line in display_currency_technical_lines(money_context))
    _render_detected_inputs(technical_lines, detected_inputs)
    _render_ignored_inputs(technical_lines, ignored_inputs)
    _render_per_analyzer_status(
        technical_lines,
        run_statuses=_run_statuses_from_results(analyzer_results, analyzer_errors, analyzer_error_diagnostics or []),
    )

    append_technical_details(lines, technical_lines)
    return "\n".join(lines).rstrip() + "\n"
