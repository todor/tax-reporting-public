from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .contracts import AnalyzerStatus, AppendixRecord, TaxAnalysisResult

ZERO = Decimal("0")
DECIMAL_TWO = Decimal("0.01")
TECHNICAL_DETAILS_SEPARATOR = (
    "------------------------------ Technical Details ------------------------------"
)


def _fmt_decimal(value: Decimal) -> str:
    return format(value.quantize(DECIMAL_TWO, rounding=ROUND_HALF_UP), "f")


def _to_file_uri(path: Path) -> str:
    return path.expanduser().resolve().as_uri()


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
    lines.extend(["", "Detected inputs"])
    if detected_inputs:
        for alias in sorted(detected_inputs):
            for path in detected_inputs[alias]:
                lines.append(f"- {alias}: {_to_file_uri(path)}")
        return
    lines.append("- -")


def _render_ignored_inputs(lines: list[str], ignored_inputs: list[tuple[Path, str]]) -> None:
    lines.extend(["", "Ignored inputs"])
    if ignored_inputs:
        for path, reason in ignored_inputs:
            lines.append(f"- {_to_file_uri(path)}: {reason}")
        return
    lines.append("- -")


def _results_by_alias(analyzer_results: list[TaxAnalysisResult]) -> dict[str, list[TaxAnalysisResult]]:
    grouped: dict[str, list[TaxAnalysisResult]] = defaultdict(list)
    for result in analyzer_results:
        grouped[result.analyzer_alias].append(result)
    return grouped


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
    statuses: dict[str, AnalyzerStatus],
    detected_inputs: dict[str, list[Path]],
    analyzer_results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
) -> None:
    lines.extend(["", "Per-analyzer status"])
    all_aliases = sorted(set(statuses) | set(detected_inputs))
    results_by_alias = _results_by_alias(analyzer_results)
    for alias in all_aliases:
        status = statuses.get(alias, "ERROR")
        lines.append(f"- {alias}: {status}")
        alias_errors = analyzer_errors.get(alias, [])
        for error in alias_errors:
            lines.append(f"  error: {error}")
        alias_results = results_by_alias.get(alias, [])
        declaration_paths = [
            result.output_paths["declaration_txt"]
            for result in alias_results
            if "declaration_txt" in result.output_paths
        ]
        for declaration_path in declaration_paths:
            lines.append(f"  declaration: {_to_file_uri(declaration_path)}")


def _collect_diagnostics(
    analyzer_results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
) -> dict[str, list[str]]:
    diagnostics_by_severity: dict[str, list[str]] = defaultdict(list)
    for result in analyzer_results:
        for diagnostic in result.diagnostics:
            if diagnostic.severity == "INFO":
                continue
            diagnostics_by_severity[diagnostic.severity].append(
                f"{diagnostic.analyzer_alias}: {diagnostic.message}"
            )
    for alias, errors in sorted(analyzer_errors.items()):
        for error in errors:
            diagnostics_by_severity["ERROR"].append(f"{alias}: {error}")
    return diagnostics_by_severity


def _render_diagnostics_summary(
    lines: list[str],
    diagnostics_by_severity: dict[str, list[str]],
) -> None:
    if not diagnostics_by_severity:
        return
    lines.extend(["", "Warnings/Errors summary"])
    for severity in ("ERROR", "MANUAL_REVIEW", "WARNING"):
        entries = diagnostics_by_severity.get(severity, [])
        if not entries:
            continue
        lines.append(f"- {severity}: {len(entries)}")
        for entry in entries:
            lines.append(f"  {entry}")


def _render_appendix5(lines: list[str], aggregated: AggregatedAppendices) -> None:
    entries = [
        ((table, code), bucket)
        for (table, code), bucket in sorted(aggregated.appendix5_by_code.items())
        if any(
            value != ZERO
            for value in (
                bucket.sale_value_eur,
                bucket.acquisition_value_eur,
                bucket.profit_eur,
                bucket.loss_eur,
                bucket.net_result_eur,
            )
        )
        or bucket.trade_count > 0
    ]
    if not entries:
        return
    if lines:
        lines.append("")
    lines.append("Приложение 5")
    lines.append("Таблица 2")
    for idx, ((_table, code), bucket) in enumerate(entries):
        if idx > 0:
            lines.append("")
        code_label = code or "-"
        lines.append(f"- Продажна цена (EUR) - код {code_label}: {_fmt_decimal(bucket.sale_value_eur)}")
        lines.append(f"  Цена на придобиване (EUR) - код {code_label}: {_fmt_decimal(bucket.acquisition_value_eur)}")
        lines.append(f"  Печалба (EUR) - код {code_label}: {_fmt_decimal(bucket.profit_eur)}")
        lines.append(f"  Загуба (EUR) - код {code_label}: {_fmt_decimal(bucket.loss_eur)}")
        if bucket.net_result_eur != ZERO or bucket.trade_count > 0:
            lines.append("")
            lines.append("  Информативни")
            lines.append(f"  - Нетен резултат (EUR): {_fmt_decimal(bucket.net_result_eur)}")
            lines.append(f"  - Брой сделки: {bucket.trade_count}")


def _render_appendix13(lines: list[str], aggregated: AggregatedAppendices) -> None:
    entries = [
        ((part, table, code), bucket)
        for (part, table, code), bucket in sorted(aggregated.appendix13_by_code.items())
        if any(
            value != ZERO
            for value in (
                bucket.gross_income_eur,
                bucket.acquisition_value_eur,
                bucket.profit_eur,
                bucket.loss_eur,
                bucket.net_result_eur,
            )
        )
        or bucket.trade_count > 0
    ]
    if not entries:
        return
    if lines:
        lines.append("")
    for idx, ((_part, _table, code), bucket) in enumerate(entries):
        if idx > 0:
            lines.append("")
        code_label = code or "-"
        lines.append("Приложение 13")
        lines.append("Част ІІ")
        lines.append(f"- Брутен размер на дохода (EUR) - код {code_label}: {_fmt_decimal(bucket.gross_income_eur)}")
        lines.append(f"  Цена на придобиване (EUR) - код {code_label}: {_fmt_decimal(bucket.acquisition_value_eur)}")
        if bucket.net_result_eur != ZERO or bucket.trade_count > 0:
            lines.append("Информативни")
            lines.append(f"- печалба (EUR): {_fmt_decimal(bucket.profit_eur)}")
            lines.append(f"- загуба (EUR): {_fmt_decimal(bucket.loss_eur)}")
            lines.append(f"- нетен резултат (EUR): {_fmt_decimal(bucket.net_result_eur)}")
            lines.append(f"- брой сделки: {bucket.trade_count}")


def _render_appendix6(lines: list[str], aggregated: AggregatedAppendices) -> None:
    company_rows = [
        (key, amount)
        for key, amount in sorted(aggregated.appendix6_part1_company.items())
        if amount != ZERO
    ]
    total_by_code = [
        (code, amount)
        for code, amount in sorted(aggregated.appendix6_part1_total_by_code.items())
        if amount != ZERO
    ]
    taxable_by_code = [
        (code, amount)
        for code, amount in sorted(aggregated.appendix6_part2_taxable_by_code.items())
        if amount != ZERO
    ]
    has_withheld = aggregated.appendix6_part3_withheld_tax != ZERO

    if not company_rows and not total_by_code and not taxable_by_code and not has_withheld:
        return

    if lines:
        lines.append("")
    lines.append("Приложение 6")
    lines.append("Част I")
    for idx, ((eik, payer, code), amount) in enumerate(company_rows, start=1):
        lines.append(f"- Ред 1.{idx}")
        lines.append(f"  ЕИК: {eik}")
        lines.append(f"  Наименование: {payer}")
        lines.append(f"  Код: {code}")
        lines.append(f"  Размер на дохода: {_fmt_decimal(amount)}")
    for code, amount in total_by_code:
        lines.append(f"- Обща сума на доходите с код {code}: {_fmt_decimal(amount)}")

    if taxable_by_code:
        lines.append("")
        lines.append("Част II")
        for code, amount in taxable_by_code:
            lines.append(f"- Облагаем доход по чл. 35, код {code}: {_fmt_decimal(amount)}")

    if has_withheld:
        lines.append("")
        lines.append("Част III")
        lines.append(
            "- Удържан и/или внесен окончателен данък за доходи: "
            f"{_fmt_decimal(aggregated.appendix6_part3_withheld_tax)}"
        )


def _render_appendix8(lines: list[str], aggregated: AggregatedAppendices) -> None:
    part1_rows = [
        ((asset_type, country, currency), bucket)
        for (asset_type, country, currency), bucket in sorted(aggregated.appendix8_part1_by_group.items())
        if any(
            value != ZERO
            for value in (
                bucket.quantity,
                bucket.acquisition_native,
                bucket.acquisition_eur,
            )
        )
    ]
    part3_rows = [
        ((payer, country, code, method), bucket)
        for (payer, country, code, method), bucket in sorted(aggregated.appendix8_part3_by_group.items())
        if any(
            value != ZERO
            for value in (
                bucket.gross_income_eur,
                bucket.foreign_tax_eur,
                bucket.allowable_credit_eur,
                bucket.recognized_credit_eur,
                bucket.tax_due_eur,
            )
        )
    ]
    if not part1_rows and not part3_rows:
        return

    if lines:
        lines.append("")
    lines.append("Приложение 8")
    if part1_rows:
        lines.append("Част І, Акции")
        for idx, ((asset_type, country, currency), bucket) in enumerate(part1_rows, start=1):
            lines.append(f"- ред 1.{idx}")
            lines.append(f"  Вид: {asset_type}")
            lines.append(f"  Държава: {country}")
            lines.append(f"  Брой: {_fmt_decimal(bucket.quantity)}")
            lines.append(
                f"  Обща цена на придобиване в съответната валута ({currency}): "
                f"{_fmt_decimal(bucket.acquisition_native)}"
            )
            lines.append(f"  В EUR: {_fmt_decimal(bucket.acquisition_eur)}")
            lines.append("")
    if part3_rows:
        lines.append("Част III,")
        for idx, ((payer, country, code, method), bucket) in enumerate(part3_rows, start=1):
            lines.append(f"- Ред 1.{idx}")
            lines.append(f"  Наименование на лицето, изплатило дохода: {payer}")
            lines.append(f"  Държава: {country}")
            lines.append(f"  Код вид доход: {code}")
            lines.append(f"  Код за прилагане на метод за избягване на двойното данъчно облагане: {method}")
            lines.append(f"  Брутен размер на дохода: {_fmt_decimal(bucket.gross_income_eur)}")
            lines.append("  Документално доказана цена на придобиване: ")
            lines.append(f"  Платен данък в чужбина: {_fmt_decimal(bucket.foreign_tax_eur)}")
            lines.append(f"  Допустим размер на данъчния кредит: {_fmt_decimal(bucket.allowable_credit_eur)}")
            lines.append(f"  Размер на признатия данъчен кредит: {_fmt_decimal(bucket.recognized_credit_eur)}")
            lines.append(f"  Дължим данък, подлежащ на внасяне: {_fmt_decimal(bucket.tax_due_eur)}")
            lines.append("")


def _render_appendix9(lines: list[str], aggregated: AggregatedAppendices) -> None:
    entries = [
        ((country, code), bucket)
        for (country, code), bucket in sorted(aggregated.appendix9_part2_by_group.items())
        if any(
            value != ZERO
            for value in (
                bucket.gross_income_eur,
                bucket.tax_base_eur,
                bucket.foreign_tax_eur,
                bucket.allowable_credit_eur,
                bucket.recognized_credit_eur,
            )
        )
    ]
    if not entries:
        return
    if lines:
        lines.append("")
    lines.extend(["Приложение 9", "Част II"])
    for (country, code), bucket in entries:
        lines.append(f"- Държава: {country}")
        lines.append(f"  Код вид доход: {code}")
        lines.append(
            f"  Брутен размер на дохода (включително платеният данък): {_fmt_decimal(bucket.gross_income_eur)}"
        )
        lines.append("  Нормативно определени разходи: 0")
        lines.append("  Задължителни осигурителни вноски: 0")
        lines.append(f"  Годишна данъчна основа: {_fmt_decimal(bucket.tax_base_eur)}")
        lines.append(f"  Платен данък в чужбина: {_fmt_decimal(bucket.foreign_tax_eur)}")
        lines.append(f"  Допустим размер на данъчния кредит: {_fmt_decimal(bucket.allowable_credit_eur)}")
        lines.append(f"  Размер на признатия данъчен кредит: {_fmt_decimal(bucket.recognized_credit_eur)}")
        lines.append(
            "  № и дата на документа за дохода и съответния данък: "
            f"{', '.join(sorted(bucket.document_refs)) if bucket.document_refs else '-'}"
        )
        lines.append("")


def render_aggregated_report(
    *,
    tax_year: int,
    detected_inputs: dict[str, list[Path]],
    ignored_inputs: list[tuple[Path, str]],
    analyzer_results: list[TaxAnalysisResult],
    analyzer_errors: dict[str, list[str]],
) -> str:
    statuses: dict[str, AnalyzerStatus] = {}
    for result in analyzer_results:
        previous = statuses.get(result.analyzer_alias, "OK")
        statuses[result.analyzer_alias] = _merge_status(previous, result.status)
    for alias, errors in analyzer_errors.items():
        if errors:
            statuses[alias] = "ERROR"

    global_status = _global_status(list(statuses.values()))
    aggregated = aggregate_appendix_records(analyzer_results)

    lines: list[str] = [_status_banner(global_status), ""]
    _render_appendix5(lines, aggregated)
    _render_appendix13(lines, aggregated)
    _render_appendix6(lines, aggregated)
    _render_appendix8(lines, aggregated)
    _render_appendix9(lines, aggregated)

    technical_lines: list[str] = [
        "Aggregated Report",
        f"- tax year: {tax_year}",
        f"- global status: {global_status}",
    ]
    _render_detected_inputs(technical_lines, detected_inputs)
    _render_ignored_inputs(technical_lines, ignored_inputs)
    _render_per_analyzer_status(
        technical_lines,
        statuses=statuses,
        detected_inputs=detected_inputs,
        analyzer_results=analyzer_results,
        analyzer_errors=analyzer_errors,
    )
    _render_diagnostics_summary(technical_lines, _collect_diagnostics(analyzer_results, analyzer_errors))

    if lines:
        lines.append("")
    lines.append(TECHNICAL_DETAILS_SEPARATOR)
    lines.append("")
    lines.extend(technical_lines)
    return "\n".join(lines).rstrip() + "\n"
