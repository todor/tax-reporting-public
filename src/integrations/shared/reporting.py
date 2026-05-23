from __future__ import annotations

import ast
import re
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from integrations.shared.rendering.common import TECHNICAL_DETAILS_SEPARATOR

from .contracts import AnalysisDiagnostic, AnalyzerStatus, UserFacingTaxError


STATUS_BANNER_BG: dict[AnalyzerStatus, str] = {
    "OK": "!!! СТАТУС: УСПЕШЕН !!!",
    "WARNING": "!!! СТАТУС: ПРЕДУПРЕЖДЕНИЯ !!!",
    "NEEDS_REVIEW": "!!! СТАТУС: ИЗИСКВА РЪЧЕН ПРЕГЛЕД !!!",
    "ERROR": "!!! СТАТУС: ГРЕШКА !!!",
}

STDOUT_STATUS: dict[AnalyzerStatus, str] = {
    "OK": "SUCCESS",
    "WARNING": "WARNING",
    "NEEDS_REVIEW": "MANUAL CHECK REQUIRED",
    "ERROR": "ERROR",
}

SEVERITY_PRIORITY = {
    "ERROR": 0,
    "MANUAL_REVIEW": 1,
    "WARNING": 2,
    "INFO": 3,
}

_FOREX_IGNORED_RE = re.compile(
    r"^row (?P<row>\d+): Forex ignored: (?P<reason>.*?) "
    r"\(symbol=(?P<symbol>.*?), execution_exchange=(?P<execution_exchange>.*?)\)$"
)
_UNSUPPORTED_TRADES_RE = re.compile(
    r"^row (?P<row>\d+): unsupported Trades Asset Category (?P<asset_category>.+?) "
    r"was skipped; (?P<reason>.*)$"
)
_UNKNOWN_DIVIDEND_RE = re.compile(
    r"^row (?P<row>\d+): unknown dividend description requires manual review "
    r"\(description=(?P<description>.*)\)$"
)
_IBKR_REVIEW_ROW_RE = re.compile(
    r"^row (?P<row>\d+): (?P<reason>.*?) "
    r"\(symbol=(?P<symbol>.*?), "
    r"(?:(?:listing_exchange|listing_exchange_raw)=(?P<listing_exchange>.*?), "
    r"mapped_classification=(?P<mapped_classification>.*?), )?"
    r"execution_exchange=(?P<execution_exchange>.*?)\)$"
)
_IBKR_COUNT_RE = re.compile(r"има (?P<count>\d+)")

_GROUPABLE_CODES = {
    "BINANCE_FUTURES_FUNDING_FEE_REVIEW_REQUIRED",
    "BINANCE_FUTURES_UNSUPPORTED_INCOME_TYPE",
    "CRYPTO_MISSING_COST_BASIS",
    "CRYPTO_PRICE_LOOKUP_FALLBACK",
    "CRYPTO_ROW_REQUIRES_REVIEW",
    "CRYPTO_UNSUPPORTED_TRANSACTION_TYPE",
    "FUND_DIVIDEND_REVIEW_REQUIRED",
    "FUND_FX_LOOKUP_FALLBACK",
    "FUND_ROW_REQUIRES_REVIEW",
    "FUND_UNSUPPORTED_ROW_TYPE",
    "FOREX_ROWS_IGNORED",
    "UNSUPPORTED_TRADES_ROWS",
    "UNKNOWN_DIVIDEND_ROWS",
    "IBKR_MANUAL_REVIEW_ROWS",
    "IBKR_OPTIONS_EXERCISE_ASSIGNMENT_NO_CLOSEDLOT",
    "IBKR_OPTIONS_UNHANDLED_ROWS",
    "IBKR_APPENDIX9_POSITIVE_WHT_REVERSAL",
    "IBKR_APPENDIX9_WHT_SOURCE_MISMATCH",
    "IBKR_DIVIDEND_WHT_REVERSAL_REVIEW",
    "IBKR_FUTURES_MTM_ARITHMETIC_MISMATCH",
    "IBKR_FUTURES_MTM_OTHER_INCLUDED",
    "IBKR_SPB8_REVIEW_REQUIRED",
    "IBKR_SANITY_CHECK_FAILURES",
    "IBKR_UNKNOWN_INTEREST_ROWS",
    "IBKR_DIVIDEND_COUNTRY_ERRORS",
    "IBKR_WITHHOLDING_COUNTRY_ERRORS",
    "IBKR_UNKNOWN_REVIEW_STATUS_ROWS",
    "P2P_AMOUNT_NORMALIZED",
    "P2P_AMOUNT_OMITTED",
    "P2P_PROCESSING_INFO",
    "P2P_REPORTING_YEAR_MISMATCH",
    "P2P_ROW_REQUIRES_REVIEW",
    "P2P_SECONDARY_MARKET_REVIEW_REQUIRED",
    "P2P_TOTAL_ROW_MISMATCH",
    "P2P_UNMAPPED_WITHHELD_TAX",
    "UNCLASSIFIED_MANUAL_REVIEW_GROUP",
    "UNCLASSIFIED_WARNING_GROUP",
}

_KNOWN_DIAGNOSTIC_CODES = {
    *_GROUPABLE_CODES,
    "EMPTY_INPUT_FILE",
    "GENERIC_ANALYZER_ERROR",
    "IBKR_INCOMPLETE_CLOSED_LOTS",
    "IBKR_FUTURES_MISSING_MTM_COLUMNS",
    "IBKR_FUTURES_MISSING_MTM_ROWS",
    "IBKR_UNSUPPORTED_BASE_CURRENCY",
    "INPUT_FILE_MISSING",
    "INVALID_TAX_YEAR",
    "MISSING_CSV_HEADER",
    "MISSING_REQUIRED_COLUMNS",
    "SPB8_INPUT_ERROR",
    "SPB8_MISSING_VALUES",
}


def diagnostics_path_for(main_report_path: Path) -> Path:
    return main_report_path.with_suffix(".diagnostics.txt")


def format_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def split_technical_details(text: str) -> tuple[str, list[str]]:
    if TECHNICAL_DETAILS_SEPARATOR not in text:
        return text.rstrip(), []
    declaration, technical = text.split(TECHNICAL_DETAILS_SEPARATOR, 1)
    technical_lines = [line.rstrip() for line in technical.strip().splitlines()]
    return declaration.rstrip(), technical_lines


def _strip_legacy_top_sections(lines: list[str]) -> list[str]:
    if lines and lines[0].startswith("!!!"):
        lines = lines[1:]
        while lines and lines[0] != "":
            lines = lines[1:]
        while lines and lines[0] == "":
            lines = lines[1:]
    if lines and lines[0].startswith("Причина за "):
        while lines and lines[0] != "":
            lines = lines[1:]
        while lines and lines[0] == "":
            lines = lines[1:]
    if lines and lines[0].startswith("Данъчна година:"):
        lines = lines[1:]
        while lines and lines[0] == "":
            lines = lines[1:]
    return lines


def _strip_legacy_section(lines: list[str], title: str) -> list[str]:
    remaining: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index] != title:
            remaining.append(lines[index])
            index += 1
            continue
        index += 1
        while index < len(lines) and lines[index].strip() != "":
            index += 1
        while index < len(lines) and lines[index].strip() == "":
            index += 1
        if remaining and remaining[-1].strip() != "":
            remaining.append("")
    while remaining and remaining[-1].strip() == "":
        remaining.pop()
    return remaining


def clean_declaration_body(raw_declaration: str) -> str:
    lines = _strip_legacy_top_sections([line.rstrip() for line in raw_declaration.splitlines()])
    for title in ("!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!", "Бележки по обработката", "Processing Notes"):
        lines = _strip_legacy_section(lines, title)
    cleaned = "\n".join(lines).strip()
    return (
        cleaned.replace('"Technical Details" -> "Processing Notes"', "диагностичния файл")
        .replace('"Technical Details"', "диагностичния файл")
        .replace("Technical Details", "диагностичния файл")
    )


@dataclass(slots=True)
class MainReportNotes:
    spb8: list[str] = field(default_factory=list)
    forex: list[str] = field(default_factory=list)
    cfd_pil: list[str] = field(default_factory=list)
    futures: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    appendix8: list[str] = field(default_factory=list)
    general: list[str] = field(default_factory=list)


def _strip_bullet_prefix(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    return stripped


def _extract_note_block(
    lines: list[str],
    *,
    title: str,
    bullet_lines: bool,
) -> tuple[list[str], list[str]]:
    remaining: list[str] = []
    notes: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index] != title:
            remaining.append(lines[index])
            index += 1
            continue

        index += 1
        while index < len(lines) and lines[index].strip() == "":
            index += 1
        while index < len(lines) and lines[index].strip() != "":
            raw_note = lines[index]
            notes.append(_strip_bullet_prefix(raw_note) if bullet_lines else raw_note.strip())
            index += 1
        while index < len(lines) and lines[index].strip() == "":
            index += 1
        if remaining and remaining[-1].strip() != "":
            remaining.append("")

    while remaining and remaining[-1].strip() == "":
        remaining.pop()
    return remaining, notes


def extract_main_report_notes(body: str) -> tuple[str, MainReportNotes]:
    lines = body.splitlines()
    lines, spb8_notes = _extract_note_block(lines, title="Забележки за СПБ-8", bullet_lines=True)
    lines, forex_notes = _extract_note_block(lines, title="Forex операции", bullet_lines=True)
    lines, cfd_pil_notes = _extract_note_block(lines, title="CFD и PIL", bullet_lines=True)
    lines, futures_notes = _extract_note_block(
        lines,
        title="Фючърси — IBKR daily cash-settled MTM",
        bullet_lines=True,
    )
    lines, options_notes = _extract_note_block(lines, title="Опции върху акции и индекси", bullet_lines=True)
    lines, appendix8_notes = _extract_note_block(lines, title="Забележка:", bullet_lines=False)
    general_notes: list[str] = []
    appendix8_specific_notes: list[str] = []
    for note in appendix8_notes:
        if "Запазете отчети" in note and "проверка от НАП" in note:
            general_notes.append(note)
        else:
            appendix8_specific_notes.append(note)
    return (
        "\n".join(lines).strip(),
        MainReportNotes(
            spb8=spb8_notes,
            forex=forex_notes,
            cfd_pil=cfd_pil_notes,
            futures=futures_notes,
            options=options_notes,
            appendix8=appendix8_specific_notes,
            general=general_notes,
        ),
    )


def diagnostic_counts(diagnostics: list[AnalysisDiagnostic]) -> dict[str, int]:
    return {
        "errors": sum(1 for item in diagnostics if item.severity == "ERROR"),
        "manual_review": sum(1 for item in diagnostics if item.severity == "MANUAL_REVIEW"),
        "warnings": sum(1 for item in diagnostics if item.severity == "WARNING"),
        "info": sum(1 for item in diagnostics if item.severity == "INFO"),
    }


def _hashable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _hashable(item)) for key, item in value.items()))
    if isinstance(value, list | tuple | set):
        return tuple(_hashable(item) for item in value)
    return value


def diagnostic_identity(diagnostic: AnalysisDiagnostic) -> tuple[Any, ...]:
    params = {
        key: value
        for key, value in diagnostic.params.items()
        if key not in {"report_path"}
    }
    return (
        diagnostic.severity,
        diagnostic.analyzer_alias,
        diagnostic.code or "",
        diagnostic.message,
        _hashable(params),
    )


def _diagnostic_source_key(diagnostic: AnalysisDiagnostic) -> tuple[str, str, str]:
    return (
        str(diagnostic.params.get("source_file") or ""),
        str(diagnostic.params.get("report_path") or ""),
        str(diagnostic.params.get("path") or diagnostic.params.get("filename") or ""),
    )


def _clean_quoted(value: str) -> str:
    return value.strip().strip("'\"")


def _diagnostic_count(diagnostic: AnalysisDiagnostic) -> int:
    count = diagnostic.params.get("count")
    if isinstance(count, int):
        return count
    rows = diagnostic.params.get("rows")
    if isinstance(rows, list) and rows:
        return len(rows)
    items = diagnostic.params.get("items")
    if isinstance(items, list) and items:
        return len(items)
    count = diagnostic.params.get("count")
    if isinstance(count, str) and count.isdigit():
        return int(count)
    return 1


def _canonicalize_diagnostic(diagnostic: AnalysisDiagnostic) -> AnalysisDiagnostic:
    message = diagnostic.message.strip()
    params = dict(diagnostic.params)
    match = _FOREX_IGNORED_RE.match(message)
    if match:
        rows = [
            {
                "row": match.group("row"),
                "symbol": match.group("symbol"),
                "execution_exchange": match.group("execution_exchange"),
                "review_status": _forex_review_status_label(match.group("reason")),
                "reason": match.group("reason"),
            }
        ]
        params.update({"count": 1, "rows": rows})
        return replace(
            diagnostic,
            code="FOREX_ROWS_IGNORED",
            message=(
                "Forex rows were ignored because taxable forex is not supported or "
                "Review Status is missing/unknown."
            ),
            params=params,
            technical_message_en=None,
        )

    match = _UNSUPPORTED_TRADES_RE.match(message)
    if match:
        rows = [
            {
                "row": match.group("row"),
                "asset_category": _clean_quoted(match.group("asset_category")),
                "reason": match.group("reason"),
            }
        ]
        params.update({"count": 1, "rows": rows})
        return replace(
            diagnostic,
            code="UNSUPPORTED_TRADES_ROWS",
            message="Unsupported Trades rows were skipped.",
            params=params,
            technical_message_en=None,
        )

    match = _UNKNOWN_DIVIDEND_RE.match(message)
    if match:
        rows = [
            {
                "row": match.group("row"),
                "description": _clean_quoted(match.group("description")),
                "reason": "unknown dividend description",
            }
        ]
        params.update({"count": 1, "rows": rows})
        return replace(
            diagnostic,
            code="UNKNOWN_DIVIDEND_ROWS",
            message="Dividend/payment-in-lieu rows require manual classification.",
            params=params,
            technical_message_en=None,
        )

    match = _IBKR_REVIEW_ROW_RE.match(message)
    if match and _looks_like_ibkr_manual_review_reason(match.group("reason")):
        rows = [
            {
                "row": match.group("row"),
                "section": "Trades",
                "symbol": match.group("symbol"),
                "listing_exchange": (
                    match.group("listing_exchange")
                    or "<missing from Financial Instrument Information>"
                ),
                "mapped_classification": match.group("mapped_classification") or "MISSING",
                "execution_exchange": match.group("execution_exchange"),
                "reason": match.group("reason"),
            }
        ]
        params.update({"count": 1, "rows": rows})
        return replace(
            diagnostic,
            code="IBKR_MANUAL_REVIEW_ROWS",
            message="IBKR trade rows require manual tax-treatment review.",
            params=params,
            technical_message_en=None,
        )

    if _contains_cyrillic(message):
        return _canonicalize_bulgarian_summary(diagnostic, params=params, message=message)

    if diagnostic.code:
        if diagnostic.code not in _KNOWN_DIAGNOSTIC_CODES:
            code = (
                f"UNCLASSIFIED_{diagnostic.severity}_GROUP"
                if diagnostic.severity in {"WARNING", "MANUAL_REVIEW"}
                else "GENERIC_ANALYZER_ERROR"
            )
            params.update({"count": 1, "raw_code": diagnostic.code, "examples": [message] if message else []})
            return replace(
                diagnostic,
                code=code,
                message=(
                    "Analyzer warnings require review."
                    if diagnostic.severity == "WARNING"
                    else "Analyzer diagnostic requires review."
                ),
                params=params,
                technical_message_en=None,
            )
        return diagnostic

    if _has_builtin_main_translation(message):
        return diagnostic

    if diagnostic.severity in {"WARNING", "MANUAL_REVIEW"}:
        code = f"UNCLASSIFIED_{diagnostic.severity}_GROUP"
        english_message = (
            "Analyzer warnings require review."
            if diagnostic.severity == "WARNING"
            else "Analyzer manual-review items require review."
        )
        params.update({"count": 1, "examples": [message] if message else []})
        return replace(
            diagnostic,
            code=code,
            message=english_message,
            params=params,
            technical_message_en=None,
        )

    return diagnostic


def _forex_review_status_label(reason: str) -> str:
    if "missing Review Status" in reason:
        return "missing"
    if "override TAXABLE" in reason:
        return "TAXABLE"
    if "unknown Review Status=" in reason:
        return reason.split("unknown Review Status=", 1)[1].split(" ", 1)[0]
    return "unknown"


def _looks_like_ibkr_manual_review_reason(reason: str) -> bool:
    lowered = reason.lower()
    return any(
        token in lowered
        for token in (
            "unmapped",
            "missing listing exchange",
            "invalid listing exchange",
            "unknown review status",
            "review status",
            "manual review",
            "classification",
        )
    )


def _canonicalize_bulgarian_summary(
    diagnostic: AnalysisDiagnostic,
    *,
    params: dict[str, Any],
    message: str,
) -> AnalysisDiagnostic:
    count_match = _IBKR_COUNT_RE.search(message)
    count = int(count_match.group("count")) if count_match else 1
    params.update({"count": count, "bulgarian_summary": message})

    code = ""
    english_message = "Analyzer issue requires review."
    if "Forex" in message:
        code = "FOREX_ROWS_IGNORED"
        english_message = (
            "Forex rows were ignored because taxable forex is not supported or "
            "Review Status is missing/unknown."
        )
    elif "записа с изисквана ръчна проверка" in message:
        code = "IBKR_MANUAL_REVIEW_ROWS"
        english_message = "IBKR rows require manual review."
    elif "неразпознат дивидентен ред" in message:
        code = "UNKNOWN_DIVIDEND_ROWS"
        english_message = "Dividend/payment-in-lieu rows require manual classification."
    elif "неуспешни sanity проверки" in message:
        code = "IBKR_SANITY_CHECK_FAILURES"
        english_message = "IBKR sanity checks failed."
    elif "непознат вид лихва" in message:
        code = "IBKR_UNKNOWN_INTEREST_ROWS"
        english_message = "Interest rows require manual classification."
    elif "дивидентни реда с невалиден ISIN/държава" in message:
        code = "IBKR_DIVIDEND_COUNTRY_ERRORS"
        english_message = "Dividend rows have invalid ISIN/country data."
    elif "реда удържан данък с невалиден ISIN/държава" in message:
        code = "IBKR_WITHHOLDING_COUNTRY_ERRORS"
        english_message = "Withholding tax rows have invalid ISIN/country data."
    elif "Открит е положителен ред в IBKR Withholding Tax" in message or (
        "Нетният чуждестранен данък" in message and "IBKR Withholding Tax" in message
    ):
        code = "IBKR_DIVIDEND_WHT_REVERSAL_REVIEW"
        english_message = "Positive dividend withholding tax rows require review."
    elif "непознат Review Status" in message:
        code = "IBKR_UNKNOWN_REVIEW_STATUS_ROWS"
        english_message = "Rows have unknown Review Status values."
    elif message.startswith("СПБ-8") or message.startswith("⚠️"):
        code = "IBKR_SPB8_REVIEW_REQUIRED"
        english_message = "IBKR SPB-8 data requires review."

    if not code:
        code = f"UNCLASSIFIED_{diagnostic.severity}_GROUP"
        params["examples"] = [message]
        english_message = (
            "Analyzer warnings require review."
            if diagnostic.severity == "WARNING"
            else "Analyzer manual-review items require review."
        )

    return replace(
        diagnostic,
        code=code,
        message=english_message,
        params=params,
        technical_message_en=None,
    )


def _group_key(diagnostic: AnalysisDiagnostic) -> tuple[Any, ...]:
    if diagnostic.code in _GROUPABLE_CODES:
        return (
            diagnostic.analyzer_alias,
            diagnostic.code,
            *_diagnostic_source_key(diagnostic),
        )
    return diagnostic_identity(diagnostic)


def _merge_diagnostics(
    current: AnalysisDiagnostic,
    incoming: AnalysisDiagnostic,
) -> AnalysisDiagnostic:
    if current.code not in _GROUPABLE_CODES:
        return current

    params = dict(current.params)
    incoming_params = dict(incoming.params)
    rows = _merge_unique_dict_rows(
        list(params.get("rows") or []),
        list(incoming_params.get("rows") or []),
    )
    items = _merge_unique_dict_rows(
        list(params.get("items") or []),
        list(incoming_params.get("items") or []),
    )
    examples = _merge_unique_texts(
        list(params.get("examples") or []),
        list(incoming_params.get("examples") or []),
    )
    count = max(_diagnostic_count(current), _diagnostic_count(incoming), len(rows), len(items), len(examples), 1)
    params.update({key: value for key, value in incoming_params.items() if key not in params})
    params["count"] = count
    if rows:
        params["rows"] = rows
    if items:
        params["items"] = items
    if examples:
        params["examples"] = examples
    severity = (
        incoming.severity
        if SEVERITY_PRIORITY[incoming.severity] < SEVERITY_PRIORITY[current.severity]
        else current.severity
    )
    return replace(current, severity=severity, params=params)


def _merge_unique_dict_rows(left: list[Any], right: list[Any]) -> list[Any]:
    rows: list[Any] = []
    seen: dict[Any, int] = {}
    for item in [*left, *right]:
        identity = _diagnostic_row_identity(item)
        if identity in seen:
            existing_index = seen[identity]
            existing = rows[existing_index]
            if isinstance(existing, dict) and isinstance(item, dict):
                rows[existing_index] = {**existing, **item}
            continue
        seen[identity] = len(rows)
        rows.append(item)
    return rows


def _diagnostic_row_identity(item: Any) -> Any:
    if not isinstance(item, dict):
        return _hashable(item)
    row = item.get("row")
    section = item.get("section")
    symbol = item.get("symbol")
    reason = item.get("reason")
    if row not in ("", None):
        return ("row", row, section or "", symbol or "", reason or "")
    return _hashable(item)


def _merge_unique_texts(left: list[Any], right: list[Any]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return values


def normalize_diagnostics(diagnostics: list[AnalysisDiagnostic]) -> list[AnalysisDiagnostic]:
    grouped: dict[tuple[Any, ...], AnalysisDiagnostic] = {}
    order: list[tuple[Any, ...]] = []
    for diagnostic in diagnostics:
        canonical = _canonicalize_diagnostic(diagnostic)
        identity = _group_key(canonical)
        if identity in grouped:
            grouped[identity] = _merge_diagnostics(grouped[identity], canonical)
            continue
        grouped[identity] = canonical
        order.append(identity)
    normalized = _drop_redundant_summary_diagnostics([grouped[identity] for identity in order])
    return sorted(
        normalized,
        key=lambda item: (
            SEVERITY_PRIORITY[item.severity],
            item.analyzer_alias,
            str(item.params.get("source_file", "")),
            item.code or "",
            item.message,
        ),
    )


def _drop_redundant_summary_diagnostics(
    diagnostics: list[AnalysisDiagnostic],
) -> list[AnalysisDiagnostic]:
    specific_keys = {
        (
            diagnostic.analyzer_alias,
            *_diagnostic_source_key(diagnostic),
        )
        for diagnostic in diagnostics
        if diagnostic.code
        in {
            "FOREX_ROWS_IGNORED",
            "UNSUPPORTED_TRADES_ROWS",
            "UNKNOWN_DIVIDEND_ROWS",
            "IBKR_MANUAL_REVIEW_ROWS",
            "IBKR_OPTIONS_UNHANDLED_ROWS",
        }
        and diagnostic.params.get("rows")
    }
    if not specific_keys:
        return diagnostics
    filtered: list[AnalysisDiagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.code == "IBKR_MANUAL_REVIEW_ROWS" and (
            diagnostic.analyzer_alias,
            *_diagnostic_source_key(diagnostic),
        ) in specific_keys and not diagnostic.params.get("rows"):
            continue
        if (
            diagnostic.code in {"UNCLASSIFIED_WARNING_GROUP", "UNCLASSIFIED_MANUAL_REVIEW_GROUP"}
            and (diagnostic.analyzer_alias, *_diagnostic_source_key(diagnostic)) in specific_keys
        ):
            continue
        filtered.append(diagnostic)
    return filtered


def classify_exception(
    exc: BaseException,
    *,
    analyzer_alias: str,
    input_path: Path | None = None,
) -> AnalysisDiagnostic:
    if isinstance(exc, UserFacingTaxError):
        return AnalysisDiagnostic(
            severity="ERROR",
            message=exc.technical_message_en or exc.code,
            analyzer_alias=analyzer_alias,
            code=exc.code,
            params=dict(exc.params),
            technical_message_en=exc.technical_message_en,
        )

    message = str(exc)
    params: dict[str, Any] = {
        "analyzer": analyzer_alias,
        "filename": input_path.name if input_path is not None else "",
        "path": format_path(input_path) if input_path is not None else "",
    }
    code = "GENERIC_ANALYZER_ERROR"

    if "missing required columns:" in message:
        code = "MISSING_REQUIRED_COLUMNS"
        raw_columns = message.split("missing required columns:", 1)[1].strip()
        try:
            columns = ast.literal_eval(raw_columns)
            if not isinstance(columns, list):
                columns = [str(columns)]
        except Exception:  # noqa: BLE001
            columns = [raw_columns]
        params["columns"] = [str(column) for column in columns]
        message = "missing required columns"
    elif "CSV header is missing" in message or "missing CSV header" in message:
        code = "MISSING_CSV_HEADER"
    elif "empty CSV input" in message:
        code = "EMPTY_INPUT_FILE"
    elif "input CSV does not exist" in message or "does not exist" in message:
        code = "INPUT_FILE_MISSING"
    elif "invalid tax year" in message:
        code = "INVALID_TAX_YEAR"
    elif "SPB-8" in message:
        code = "SPB8_INPUT_ERROR"

    return AnalysisDiagnostic(
        severity="ERROR",
        message=message,
        analyzer_alias=analyzer_alias,
        code=code,
        params=params,
        technical_message_en=str(exc),
    )


def user_message_lines_bg(diagnostic: AnalysisDiagnostic) -> list[str]:
    params = diagnostic.params
    filename = str(params.get("filename") or "").strip()
    analyzer = str(params.get("analyzer") or diagnostic.analyzer_alias)

    if diagnostic.code == "MISSING_REQUIRED_COLUMNS":
        columns = [str(item) for item in params.get("columns", [])]
        target = f"файлът {filename}" if filename else f"входният файл за {analyzer}"
        lines = [f"Грешка: {target} няма задължителни колони:"]
        lines.extend(f"- {column}" for column in columns)
        lines.extend(
            [
                "Какво да направите:",
                f"- Проверете дали сте подали правилния отчет за анализатора {analyzer}.",
                "- Експортирайте файла отново с очаквания формат и стартирайте инструмента пак.",
            ]
        )
        return lines

    if diagnostic.code == "MISSING_CSV_HEADER":
        target = f"файлът {filename}" if filename else f"входният файл за {analyzer}"
        return [
            f"Грешка: {target} няма CSV заглавен ред.",
            "Какво да направите:",
            "- Проверете дали файлът е правилният експорт и не е празен.",
        ]

    if diagnostic.code == "EMPTY_INPUT_FILE":
        target = f"файлът {filename}" if filename else f"входният файл за {analyzer}"
        return [
            f"Грешка: {target} е празен.",
            "Какво да направите:",
            "- Подайте непразен отчет от съответната платформа.",
        ]

    if diagnostic.code == "INPUT_FILE_MISSING":
        target = f"файлът {filename}" if filename else "входният файл"
        return [
            f"Грешка: {target} не съществува или не може да бъде прочетен.",
            "Какво да направите:",
            "- Проверете пътя до файла и стартирайте инструмента отново.",
        ]

    if diagnostic.code == "INVALID_TAX_YEAR":
        return [
            "Грешка: избраната данъчна година е невалидна.",
            "Какво да направите:",
            "- Подайте реална данъчна година с --tax-year.",
        ]

    if diagnostic.code == "SPB8_INPUT_ERROR":
        return [
            "Грешка: SPB-8 input CSV файлът е невалиден.",
            "Какво да направите:",
            "- Проверете заглавния ред, платформата, типа, държавата, валутата и сумите.",
            "- Коригирайте файла и стартирайте отново с --spb8-input-file <path>.",
        ]

    if diagnostic.code == "SPB8_MISSING_VALUES":
        missing_values = list(params.get("missing_values", []))
        lines = ["СПБ-8: липсват начални/крайни стойности за някои платформи."]
        if missing_values:
            lines.append("Липсващи стойности:")
            for item in missing_values:
                missing_labels = ", ".join(_missing_value_label_bg(value) for value in item.get("missing", []))
                if item.get("isin"):
                    target = f"{item.get('platform')} / ISIN {item.get('isin')} / тип {item.get('type')}"
                else:
                    target = (
                        f"{item.get('platform')} / {item.get('country')} / "
                        f"{item.get('currency')} / тип {item.get('type')}"
                    )
                lines.append(f"- {target}: {missing_labels}")
        lines.extend(
            [
                "Какво да направите:",
                "- Попълнете липсващите стойности в SPB-8 input файла.",
                "- Стартирайте отново с --spb8-input-file <path>.",
            ]
        )
        return lines

    if diagnostic.code == "IBKR_INCOMPLETE_CLOSED_LOTS":
        closing_trade_count = params.get("closing_trade_count")
        realized_summary_count = params.get("realized_summary_count")
        lines = [
            "IBKR отчетът изглежда непълен за данъчни цели: намерени са сделки, "
            "които вероятно затварят позиции, но липсват ClosedLot редове.",
        ]
        if closing_trade_count:
            lines.append(f"Намерени затварящи сделки без налични ClosedLot данни: {closing_trade_count}.")
        if realized_summary_count:
            lines.append(f"Намерени Total/SubTotal редове с реализирана печалба/загуба: {realized_summary_count}.")
        lines.extend(
            [
                "Те са необходими за надеждно изчисляване на печалба/загуба.",
                "Какво да направите:",
                "- Изтеглете пълен IBKR Activity Statement с включени Trades -> Closed Lots / Lot Details.",
                "- Стартирайте анализа отново с пълния отчет.",
            ]
        )
        return lines

    if diagnostic.code == "IBKR_UNSUPPORTED_BASE_CURRENCY":
        base_currency = str(params.get("base_currency") or "<missing>")
        return [
            f"Грешка: IBKR акаунтът е с неподдържана базова валута: {base_currency}.",
            "В момента се поддържат само IBKR акаунти с базова валута EUR за българско данъчно отчитане.",
            "Какво да направите:",
            "- Експортирайте Activity Statement за IBKR акаунт с Base Currency EUR.",
            "- Ако акаунтът не е в EUR, обработете го ръчно или разширете поддръжката на анализатора.",
        ]

    if diagnostic.code == "IBKR_FUTURES_MISSING_MTM_ROWS":
        return [
            "Открити са IBKR Futures сделки, но липсват Futures редове в Mark-to-Market Performance Summary.",
            "Не може надеждно да се изчисли данъчният резултат за фючърси без MTM секцията.",
            "Какво да направите:",
            "- Експортирайте пълен IBKR Activity Statement с включена Mark-to-Market Performance Summary секция.",
            "- Стартирайте анализа отново с пълния отчет.",
        ]

    if diagnostic.code == "IBKR_FUTURES_MISSING_MTM_COLUMNS":
        columns = [str(item) for item in params.get("columns", [])]
        lines = ["Грешка: Futures ред в Mark-to-Market Performance Summary няма задължителни колони:"]
        lines.extend(f"- {column}" for column in columns)
        lines.extend(
            [
                "Какво да направите:",
                "- Експортирайте IBKR Activity Statement с пълна Mark-to-Market Performance Summary секция.",
                "- Стартирайте анализа отново с пълния отчет.",
            ]
        )
        return lines

    if diagnostic.code and diagnostic.code.startswith("CRYPTO_"):
        return _structured_family_message_bg(
            diagnostic,
            analyzer=analyzer,
            summaries={
                "CRYPTO_PRICE_LOOKUP_FALLBACK": "използвана е резервна цена за {count} крипто записа.",
                "CRYPTO_MISSING_COST_BASIS": "има {count} крипто записа с липсваща или невалидна цена на придобиване.",
                "CRYPTO_UNSUPPORTED_TRANSACTION_TYPE": "има {count} неподдържани крипто транзакции, които са изключени.",
                "CRYPTO_ROW_REQUIRES_REVIEW": "има {count} крипто записа, които изискват ръчен преглед.",
            },
            actions=[
                "- Проверете засегнатите крипто редове в диагностичния файл.",
                "- Ако имат данъчно значение, коригирайте входните данни или ги обработете ръчно.",
            ],
        )

    if diagnostic.code and diagnostic.code.startswith("FUND_"):
        return _structured_family_message_bg(
            diagnostic,
            analyzer=analyzer,
            summaries={
                "FUND_FX_LOOKUP_FALLBACK": "използван е резервен валутен курс за {count} реда.",
                "FUND_UNSUPPORTED_ROW_TYPE": "има {count} неподдържани fund реда, които са изключени.",
                "FUND_ROW_REQUIRES_REVIEW": "има {count} fund реда, които изискват ръчен преглед.",
                "FUND_DIVIDEND_REVIEW_REQUIRED": "има {count} dividend реда, които изискват ръчен преглед.",
            },
            actions=[
                "- Проверете засегнатите fund редове в диагностичния файл.",
                "- Ако имат данъчно значение, коригирайте входните данни или ги обработете ръчно.",
            ],
        )

    if diagnostic.code and diagnostic.code.startswith("P2P_"):
        if diagnostic.code == "P2P_REPORTING_YEAR_MISMATCH":
            report_year = params.get("report_year", "-")
            tax_year = params.get("tax_year", "-")
            return [
                f"{_display_analyzer_name(analyzer)}: отчетната година в отчета ({report_year}) "
                f"се различава от избраната данъчна година ({tax_year}).",
                "Какво да направите:",
                "- Проверете дали този файл трябва да участва в отчета за избраната данъчна година.",
                "- Ако не трябва, премахнете го от входната директория или филтрирайте входовете.",
            ]
        return _structured_family_message_bg(
            diagnostic,
            analyzer=analyzer,
            summaries={
                "P2P_AMOUNT_NORMALIZED": "има {count} P2P суми с нормализиран знак.",
                "P2P_AMOUNT_OMITTED": "има {count} P2P суми, които не са включени автоматично.",
                "P2P_PROCESSING_INFO": "има {count} информационни бележки за P2P обработката.",
                "P2P_ROW_REQUIRES_REVIEW": "има {count} P2P реда, които изискват ръчен преглед.",
                "P2P_SECONDARY_MARKET_REVIEW_REQUIRED": (
                    "има {count} запис от вторичен пазар, който изисква ръчен преглед."
                ),
                "P2P_TOTAL_ROW_MISMATCH": "има несъответствие между P2P общия ред и детайлните редове.",
                "P2P_UNMAPPED_WITHHELD_TAX": "има удържан P2P данък без достатъчен контекст за автоматично отразяване.",
            },
            actions=[
                "- Проверете засегнатите P2P редове в диагностичния файл.",
                "- Потвърдете данъчното третиране с вашия счетоводител при съмнение.",
            ],
        )

    if diagnostic.code and diagnostic.code.startswith("BINANCE_FUTURES_"):
        return _structured_family_message_bg(
            diagnostic,
            analyzer=analyzer,
            summaries={
                "BINANCE_FUTURES_FUNDING_FEE_REVIEW_REQUIRED": (
                    "има {count} Binance Futures funding fee реда, които изискват преглед."
                ),
                "BINANCE_FUTURES_UNSUPPORTED_INCOME_TYPE": (
                    "има {count} Binance Futures реда с неподдържан тип доход."
                ),
            },
            actions=[
                "- Проверете засегнатите Binance Futures редове в диагностичния файл.",
                "- Ако имат данъчно значение, обработете ги ръчно или добавете поддръжка в анализатора.",
            ],
        )

    if diagnostic.code == "FOREX_ROWS_IGNORED":
        count = _diagnostic_count(diagnostic)
        lines = [
            f"{_display_analyzer_name(analyzer)}: има {count} Forex реда, които не са включени автоматично.",
            "Причина: липсващ, TAXABLE или непознат Review Status; автоматичното данъчно третиране на Forex не е поддържано.",
        ]
        row_numbers = _diagnostic_row_numbers(params)
        if row_numbers:
            lines.append(f"Засегнати редове: {row_numbers}")
        lines.extend(
            [
                "Какво да направите:",
                "- Проверете Forex редовете в диагностичния файл.",
                "- Ако имат данъчно значение, обработете ги ръчно преди подаване.",
            ]
        )
        return lines

    if diagnostic.code == "UNSUPPORTED_TRADES_ROWS":
        count = _diagnostic_count(diagnostic)
        lines = [
            f"{_display_analyzer_name(analyzer)}: има {count} реда от неподдържани Trades категории, които са пропуснати.",
        ]
        categories = _diagnostic_distinct_values(params, "asset_category")
        if categories:
            lines.append(f"Категории: {', '.join(categories)}")
        row_numbers = _diagnostic_row_numbers(params)
        if row_numbers:
            lines.append(f"Засегнати редове: {row_numbers}")
        lines.extend(
            [
                "Какво да направите:",
                "- Проверете дали тези инструменти трябва да участват в декларацията.",
                "- Ако са релевантни, обработете ги ръчно или разширете поддръжката на анализатора.",
            ]
        )
        return lines

    if diagnostic.code == "UNKNOWN_DIVIDEND_ROWS":
        count = _diagnostic_count(diagnostic)
        lines = [
            f"{_display_analyzer_name(analyzer)}: има {count} неразпознати dividend/payment-in-lieu реда.",
        ]
        row_numbers = _diagnostic_row_numbers(params)
        if row_numbers:
            lines.append(f"Засегнати редове: {row_numbers}")
        lines.extend(
            [
                "Какво да направите:",
                "- Проверете дали са дивиденти, payment-in-lieu или друг тип доход.",
                "- Потвърдете третирането преди подаване.",
            ]
        )
        return lines

    if diagnostic.code == "IBKR_MANUAL_REVIEW_ROWS":
        count = _diagnostic_count(diagnostic)
        listing_related = _is_listing_exchange_review(diagnostic)
        lines = [
            f"{_display_analyzer_name(analyzer)}: {diagnostic.code} - {count} реда изискват ръчна проверка.",
            "Категория: ръчен преглед.",
            f"Причина: {_diagnostic_reason_bg(diagnostic, default='класификацията или Review Status не позволяват автоматично данъчно третиране.')}",
        ]
        if listing_related:
            lines.append(
                "Важно: execution_exchange показва къде е изпълнена сделката, но не е достатъчен "
                "за класификацията при listed_symbol режим."
            )
        examples = _diagnostic_examples_bg(diagnostic.params, include_raw=False)
        if examples:
            lines.append("Примери:")
            lines.extend(f"- {example}" for example in examples)
        lines.append("Какво да направите:")
        if listing_related:
            lines.extend(
                [
                    "- Проверете Financial Instrument Information реда за съответния символ в IBKR Activity Statement.",
                    "- Ако борсата на листване е известна и трябва да бъде класифицирана, добавете/поправете мапинга в exchange classification таблицата.",
                    "- Ако не може да се класифицира автоматично, потвърдете данъчното третиране ръчно преди подаване.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Проверете тези конкретни редове в IBKR индивидуалния отчет и диагностичния файл.",
                    "- Потвърдете данъчното третиране преди подаване.",
                ]
            )
        return lines

    if diagnostic.code == "IBKR_DIVIDEND_WHT_REVERSAL_REVIEW":
        positive_rows = diagnostic.params.get("positive_wht_rows")
        non_positive_buckets = diagnostic.params.get("non_positive_net_buckets")
        lines = [
            "IBKR: открити са положителни Withholding Tax редове за дивиденти, които изглеждат като "
            "възстановен/коригиран чуждестранен данък.",
            "Инструментът ги приспада от чуждестранния данък за текущата година.",
        ]
        if positive_rows:
            lines.append(f"Положителни Withholding Tax редове: {positive_rows}.")
        if non_positive_buckets:
            lines.extend(
                [
                    f"Appendix 8 групи с нулев или отрицателен нетен чуждестранен данък: {non_positive_buckets}.",
                    "За тях инструментът не признава данъчен кредит.",
                ]
            )
        lines.extend(
            [
                "Какво да направите:",
                "- Проверете ръчно дали корекциите се отнасят за текущата година или за предходна данъчна година.",
                "- Ако са за предходна година, може да е необходимо да се коригира предходната декларация.",
                "- При прагматичен подход сумата може да се третира като намаление на данъчния кредит в текущата година.",
            ]
        )
        return lines

    if diagnostic.code == "IBKR_APPENDIX9_WHT_SOURCE_MISMATCH":
        return [
            "IBKR: открито е разминаване между детайлните Withholding Tax редове за лихви "
            "и Mark-to-Market Performance Summary / Withholding on Interest Received.",
            "Инструментът използва детайлните Withholding Tax редове за Приложение 9.",
            "Какво да направите:",
            "- Проверете ръчно данъка върху лихви.",
            "- Ако Mark-to-Market сумата е вярната за вашия отчет, коригирайте входните данни или обработете случая ръчно.",
        ]

    if diagnostic.code == "IBKR_APPENDIX9_POSITIVE_WHT_REVERSAL":
        positive_rows = diagnostic.params.get("positive_wht_rows")
        non_positive_buckets = diagnostic.params.get("non_positive_net_buckets")
        lines = [
            "IBKR: открити са положителни Withholding Tax редове за лихви, които изглеждат като "
            "възстановен/коригиран чуждестранен данък.",
            "Инструментът ги приспада от чуждестранния данък за текущата година.",
        ]
        if positive_rows:
            lines.append(f"Положителни Withholding Tax редове за лихви: {positive_rows}.")
        if non_positive_buckets:
            lines.extend(
                [
                    f"Appendix 9 групи с нулев или отрицателен нетен чуждестранен данък: {non_positive_buckets}.",
                    "За тях инструментът не признава данъчен кредит.",
                ]
            )
        lines.extend(
            [
                "Какво да направите:",
                "- Проверете дали корекциите се отнасят за текущата или предходна данъчна година.",
                "- Ако са за предходна година, може да е необходимо да се коригира предходната декларация.",
            ]
        )
        return lines

    if diagnostic.code and diagnostic.code.startswith("IBKR_"):
        if diagnostic.code == "IBKR_OPTIONS_UNHANDLED_ROWS":
            count = _diagnostic_count(diagnostic)
            lines = [
                f"{_display_analyzer_name(analyzer)}: {diagnostic.code} - {count} реда с опции изискват преглед.",
                "Категория: предупреждение.",
                "Причина: открити са option Trade редове без attached ClosedLot, затова не е създаден автоматичен данъчен резултат.",
            ]
            examples = _diagnostic_examples_bg(diagnostic.params)
            if examples:
                lines.append("Примери:")
                lines.extend(f"- {example}" for example in examples)
            lines.extend(
                [
                    "Какво да направите:",
                    "- Проверете дали тези опции са реализирани, изтекли, упражнени или assigned.",
                    "- Ако имат данъчно значение, обработете резултата ръчно или добавете поддръжка в анализатора.",
                ]
            )
            return lines
        return [
            f"{_display_analyzer_name(analyzer)}: {_ibkr_code_summary_bg(diagnostic)}",
            "Какво да направите:",
            "- Прегледайте детайлите в диагностичния файл.",
            "- Ако случаят има данъчно значение, обработете го ръчно преди подаване.",
        ]

    if diagnostic.code in {"UNCLASSIFIED_WARNING_GROUP", "UNCLASSIFIED_MANUAL_REVIEW_GROUP"}:
        count = _diagnostic_count(diagnostic)
        kind = "предупреждения" if diagnostic.severity == "WARNING" else "сигнала за ръчен преглед"
        reason = _unclassified_reason_bg(diagnostic)
        lines = [
            f"{_display_analyzer_name(analyzer)}: {diagnostic.code} - има {count} {kind}, които изискват преглед.",
            f"Причина: {reason}",
        ]
        examples = _diagnostic_examples_bg(diagnostic.params, include_raw=False)
        if examples:
            lines.append("Примери:")
            lines.extend(f"- {example}" for example in examples)
        lines.extend(
            [
                "Какво да направите:",
                "- Прегледайте детайлите в диагностичния файл.",
                "- Ако тези редове имат данъчно значение, обработете ги ръчно.",
            ]
        )
        return lines

    translated_message = _translate_diagnostic_message_bg(diagnostic)
    if translated_message:
        lines = [f"{_display_analyzer_name(analyzer)}: {translated_message}"]
        related_report = params.get("report_path")
        if related_report:
            lines.extend(["Свързан индивидуален отчет:", f"- {related_report}"])
        action_lines = _suggested_action_lines_bg(diagnostic)
        if action_lines:
            lines.append("Какво да направите:")
            lines.extend(action_lines)
        return lines

    if diagnostic.severity == "MANUAL_REVIEW":
        return [
            f"{_display_analyzer_name(analyzer)}: има случай, който изисква ръчен преглед.",
            "Какво да направите:",
            "- Прегледайте детайлите в диагностичния файл.",
            "- Потвърдете третирането преди подаване.",
        ]

    if diagnostic.severity == "WARNING":
        return [
            f"{_display_analyzer_name(analyzer)}: има предупреждение, което изисква преглед.",
            "Какво да направите:",
            "- Прегледайте детайлите в диагностичния файл.",
            "- Ако предупреждението влияе на декларацията, обработете случая ръчно.",
        ]

    return [
        f"Грешка: възникна проблем при обработката с анализатора {analyzer}.",
        "Какво да направите:",
        "- Проверете дали входният файл е в очаквания формат.",
        "- Вижте диагностичния файл за технически детайли.",
        "- Ако проблемът изглежда като бъг, запазете диагностичния файл.",
    ]


def _display_analyzer_name(analyzer: str) -> str:
    if analyzer == "spb8":
        return "СПБ-8"
    if analyzer == "ibkr":
        return "IBKR"
    return analyzer.replace("_", " ").title()


def _missing_value_label_bg(value: str) -> str:
    if value == "start_amount":
        return "начална стойност (start amount)"
    if value == "end_amount":
        return "крайна стойност (end amount)"
    return value


def _structured_family_message_bg(
    diagnostic: AnalysisDiagnostic,
    *,
    analyzer: str,
    summaries: dict[str, str],
    actions: list[str],
) -> list[str]:
    count = _diagnostic_count(diagnostic)
    template = summaries.get(diagnostic.code or "", "има {count} случая, които изискват преглед.")
    lines = [f"{_display_analyzer_name(analyzer)}: {template.format(count=count)}"]
    row_numbers = _diagnostic_row_numbers(diagnostic.params)
    if row_numbers:
        lines.append(f"Засегнати редове: {row_numbers}")
    lines.append("Какво да направите:")
    lines.extend(actions)
    return lines


def _diagnostic_row_numbers(params: dict[str, Any]) -> str:
    rows = params.get("rows") or params.get("items")
    if not isinstance(rows, list):
        return ""
    numbers = [
        str(item.get("row"))
        for item in rows
        if isinstance(item, dict) and item.get("row") not in ("", None)
    ]
    return ", ".join(numbers[:20])


def _diagnostic_distinct_values(params: dict[str, Any], key: str) -> list[str]:
    rows = params.get("rows")
    if not isinstance(rows, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _diagnostic_reason_bg(diagnostic: AnalysisDiagnostic, *, default: str) -> str:
    values = _diagnostic_distinct_values(diagnostic.params, "reason")
    if not values:
        return default
    if len(values) == 1:
        return _reason_bg(values[0])
    return "; ".join(_reason_bg(value) for value in values[:3])


def _reason_bg(reason: str) -> str:
    lowered = reason.lower()
    if "unmapped listing exchange" in lowered:
        return (
            "борсата на листване от IBKR Financial Instrument Information липсва или не е мапната "
            "за целите на данъчното освобождаване при режим listed_symbol."
        )
    if "missing listing exchange" in lowered:
        return (
            "борсата на листване от IBKR Financial Instrument Information липсва или не е мапната "
            "за целите на данъчното освобождаване при режим listed_symbol."
        )
    if "invalid listing exchange" in lowered:
        return "невалидна стойност за listing exchange."
    if "unknown review status" in lowered:
        return "непозната стойност в Review Status."
    return reason.rstrip(".") + "."


def _diagnostic_examples_bg(params: dict[str, Any], *, limit: int = 3, include_raw: bool = True) -> list[str]:
    rows = params.get("rows") or params.get("items")
    examples: list[str] = []
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            examples.append(_diagnostic_row_example(item))
            if len(examples) >= limit:
                return examples
    if include_raw:
        raw_examples = params.get("examples")
        if isinstance(raw_examples, list):
            for item in raw_examples:
                text = str(item).strip()
                if not text:
                    continue
                examples.append(text)
                if len(examples) >= limit:
                    return examples
        raw_detail = params.get("raw_detail")
        if raw_detail:
            examples.append(str(raw_detail).strip())
    return examples[:limit]


def _diagnostic_row_example(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if item.get("row") not in ("", None):
        parts.append(f"row {item['row']}")
    if item.get("section"):
        parts.append(f"section={item['section']}")
    if item.get("symbol"):
        parts.append(f"symbol={item['symbol']}")
    listing = item.get("listing_exchange_raw") or item.get("listing_exchange")
    if listing:
        parts.append(f"listing_exchange={listing}")
    if item.get("mapped_classification"):
        parts.append(f"mapped_classification={item['mapped_classification']}")
    if item.get("execution_exchange"):
        parts.append(f"execution_exchange={item['execution_exchange']}")
    if item.get("date"):
        parts.append(f"date={item['date']}")
    if item.get("type"):
        parts.append(f"type={item['type']}")
    if item.get("review_status"):
        parts.append(f"review_status={item['review_status']}")
    if item.get("reason"):
        parts.append(f"reason={item['reason']}")
    if not parts:
        return ", ".join(f"{key}={value}" for key, value in item.items() if value not in ("", None))
    return ", ".join(parts)


def _is_listing_exchange_review(diagnostic: AnalysisDiagnostic) -> bool:
    return any(
        "listing exchange" in str(item.get("reason", "")).lower()
        for item in diagnostic.params.get("rows", [])
        if isinstance(item, dict)
    )


def _unclassified_reason_bg(diagnostic: AnalysisDiagnostic) -> str:
    examples = [str(item) for item in diagnostic.params.get("examples", []) if str(item).strip()]
    combined = "\n".join(examples).lower()
    if "unmapped listing exchange" in combined:
        return "немапната борса/listing exchange."
    if "invalid listing exchange" in combined:
        return "невалидна стойност за listing exchange."
    if "missing listing exchange" in combined:
        return "липсваща класификация на listing exchange."
    if "execution_exchange" in combined or "listing exchange" in combined:
        return "нужна е проверка на борсова класификация."
    return "диагностиката няма структуриран код; вижте примерите и техническите детайли."


def _ibkr_code_summary_bg(diagnostic: AnalysisDiagnostic) -> str:
    count = _diagnostic_count(diagnostic)
    summaries = {
        "IBKR_SANITY_CHECK_FAILURES": f"има {count} неуспешни sanity проверки.",
        "IBKR_APPENDIX9_WHT_SOURCE_MISMATCH": "има разминаване в източниците за удържан данък върху лихви.",
        "IBKR_APPENDIX9_POSITIVE_WHT_REVERSAL": f"има {count} положителни Withholding Tax реда за лихви.",
        "IBKR_DIVIDEND_WHT_REVERSAL_REVIEW": f"има {count} случая с положителен Withholding Tax за дивиденти.",
        "IBKR_FUTURES_MTM_ARITHMETIC_MISMATCH": f"има {count} Futures MTM реда с аритметично разминаване.",
        "IBKR_FUTURES_MTM_OTHER_INCLUDED": f"има {count} Futures MTM реда с ненулева стойност в колоната Other.",
        "IBKR_UNKNOWN_INTEREST_ROWS": f"има {count} реда с непознат вид лихва.",
        "IBKR_DIVIDEND_COUNTRY_ERRORS": f"има {count} дивидентни реда с невалиден ISIN/държава.",
        "IBKR_WITHHOLDING_COUNTRY_ERRORS": f"има {count} реда удържан данък с невалиден ISIN/държава.",
        "IBKR_UNKNOWN_REVIEW_STATUS_ROWS": f"има {count} реда с непознат Review Status.",
        "IBKR_SPB8_REVIEW_REQUIRED": f"има {count} СПБ-8 предупреждения, които изискват преглед.",
    }
    return summaries.get(diagnostic.code or "", f"има {count} случая, които изискват преглед.")


def _contains_cyrillic(value: str) -> bool:
    return any("а" <= char.lower() <= "я" for char in value)


def _translate_diagnostic_message_bg(diagnostic: AnalysisDiagnostic) -> str | None:
    message = diagnostic.message.strip()
    if not message:
        return None
    if _contains_cyrillic(message):
        return message
    if message.startswith("reporting year in PDF (") and "differs from requested tax year" in message:
        import re

        match = re.search(r"reporting year in PDF \(([^)]+)\) differs from requested tax year \(([^)]+)\)", message)
        if match:
            return (
                f"отчетната година в PDF ({match.group(1)}) "
                f"се различава от избраната данъчна година ({match.group(2)})."
            )
    if message.startswith("Appendix total row mismatch vs parsed detail rows"):
        return "несъответствие между общия ред в приложението и сумите от детайлните редове."
    translations = {
        "manual row excluded": "има ред, изключен от декларационните суми и изискващ ръчен преглед.",
    }
    return translations.get(message)


def _has_builtin_main_translation(message: str) -> bool:
    return (
        message.startswith("reporting year in PDF (")
        and "differs from requested tax year" in message
    ) or message in {
        "Appendix total row mismatch vs parsed detail rows",
        "manual row excluded",
    }


def _suggested_action_lines_bg(diagnostic: AnalysisDiagnostic) -> list[str]:
    message = diagnostic.message.strip()
    if message.startswith("reporting year in PDF (") and "differs from requested tax year" in message:
        return [
            "- Проверете дали този файл трябва да участва в отчета за избраната данъчна година.",
            "- Ако не трябва, премахнете го от входната директория или филтрирайте входовете.",
        ]
    if diagnostic.severity == "MANUAL_REVIEW":
        return ["- Прегледайте посочения случай преди да използвате отчета за подаване."]
    if diagnostic.severity == "WARNING":
        return ["- Проверете предупреждението и потвърдете дали влияе на подаването."]
    return []


def _informational_note_count(notes: MainReportNotes) -> int:
    return sum(
        len(section)
        for section in (
            notes.spb8,
            notes.forex,
            notes.cfd_pil,
            notes.futures,
            notes.options,
            notes.appendix8,
            notes.general,
        )
    )


def _inline_note_count(body: str, *, title: str) -> int:
    lines = body.splitlines()
    count = 0
    index = 0
    while index < len(lines):
        if lines[index] != title:
            index += 1
            continue
        index += 1
        while index < len(lines) and lines[index].strip() == "":
            index += 1
        while index < len(lines) and lines[index].strip() != "":
            if lines[index].lstrip().startswith("- "):
                count += 1
            index += 1
    return count


def render_review_summary(
    diagnostics: list[AnalysisDiagnostic],
    *,
    informational_note_count: int = 0,
) -> list[str]:
    diagnostics = normalize_diagnostics(diagnostics)
    counts = diagnostic_counts(diagnostics)
    info_count = counts["info"] + informational_note_count
    return [
        "Обобщение за преглед",
        "",
        f"- Грешки: {counts['errors']}",
        f"- Изискват ръчен преглед: {counts['manual_review']}",
        f"- Предупреждения: {counts['warnings']}",
        f"- Информационни бележки: {info_count}",
    ]


def render_action_items(diagnostics: list[AnalysisDiagnostic]) -> list[str]:
    actionable = [
        diagnostic
        for diagnostic in normalize_diagnostics(diagnostics)
        if diagnostic.severity in {"ERROR", "MANUAL_REVIEW", "WARNING"}
    ]
    lines = ["Какво трябва да направите", ""]
    if not actionable:
        lines.append("- Няма задължителни действия.")
        return lines
    for diagnostic in actionable:
        rendered = user_message_lines_bg(diagnostic)
        if not rendered:
            continue
        lines.append(f"- {rendered[0]}")
        for line in rendered[1:]:
            if line.startswith("-"):
                lines.append(f"  {line}")
            else:
                lines.append(f"  {line}")
    return lines


def _notes_subsection(title: str, notes: list[str]) -> list[str]:
    cleaned = [note.strip() for note in notes if note.strip()]
    if not cleaned:
        return []
    return [title, *(f"- {note}" for note in cleaned)]


def _join_sections(sections: list[list[str]]) -> list[str]:
    lines: list[str] = []
    for section in sections:
        if not section:
            continue
        if lines:
            lines.append("")
        lines.extend(section)
    return lines


def render_assumptions_section(*, notes: MainReportNotes, diagnostics_path: Path) -> list[str]:
    sections = [
        _notes_subsection("СПБ-8", notes.spb8),
        _notes_subsection("Forex операции", notes.forex),
        _notes_subsection("CFD и PIL", notes.cfd_pil),
        _notes_subsection("Фючърси — IBKR daily cash-settled MTM", notes.futures),
        _notes_subsection("Опции върху акции и индекси", notes.options),
        _notes_subsection("Приложение 8", notes.appendix8),
        _notes_subsection(
            "Изчисления и визуализация",
            [
                "Всички изчисления се извършват в EUR.",
                "--display-currency влияе само на визуализацията в TXT отчета.",
                *notes.general,
            ],
        ),
        _notes_subsection(
            "Диагностика",
            [
                "Техническите детайли и диагностичната информация са записани в:\n  "
                f"{format_path(diagnostics_path)}",
            ],
        ),
    ]
    sections = [section for section in sections if section]
    if not sections:
        return []
    meaningful_group_sections = {
        section[0]
        for section in sections
        if section[0] not in {"Изчисления и визуализация", "Диагностика"}
    }
    lines = _join_sections(sections)
    if len(meaningful_group_sections) < 2:
        return lines
    return ["Бележки и допускания", "", *lines]


def _visible_note_count(section_lines: list[str]) -> int:
    return sum(1 for line in section_lines if line.lstrip().startswith("- "))


def render_main_report(
    *,
    status: AnalyzerStatus,
    tax_year: int | None = None,
    raw_declaration_text: str,
    diagnostics: list[AnalysisDiagnostic],
    diagnostics_path: Path,
    assumption_notes: list[str] | None = None,
) -> str:
    _ = assumption_notes
    diagnostics = normalize_diagnostics(diagnostics)
    declaration_text, _technical_lines = split_technical_details(raw_declaration_text)
    body = clean_declaration_body(declaration_text)
    body, notes = extract_main_report_notes(body)
    assumptions = render_assumptions_section(notes=notes, diagnostics_path=diagnostics_path)
    informational_note_count = (
        _visible_note_count(assumptions)
        + _inline_note_count(
            body,
            title="Бележки към СПБ-8",
        )
    )
    lines: list[str] = [STATUS_BANNER_BG[status]]
    if tax_year is not None:
        lines.append(f"Данъчна година: {tax_year}")
    lines.extend(
        [
            "",
            *render_review_summary(
                diagnostics,
                informational_note_count=informational_note_count,
            ),
            "",
            *render_action_items(diagnostics),
        ]
    )
    if body:
        lines.extend(["", body])
    if assumptions:
        lines.extend(["", *assumptions])
    return "\n".join(lines).rstrip() + "\n"


def render_diagnostics_report(
    *,
    title: str,
    status: AnalyzerStatus,
    raw_declaration_text: str,
    diagnostics: list[AnalysisDiagnostic],
    extra_lines: list[str] | None = None,
    exception: BaseException | None = None,
) -> str:
    _declaration_text, technical_lines = split_technical_details(raw_declaration_text)
    diagnostics = normalize_diagnostics(diagnostics)
    lines: list[str] = [
        "Technical Details",
        "",
        title,
        f"- status: {status}",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    if technical_lines:
        lines.extend(["", "Analyzer technical details", *technical_lines])
    if diagnostics:
        lines.extend(["", "Diagnostics"])
        for index, diagnostic in enumerate(diagnostics):
            if index:
                lines.append("")
            lines.extend(_render_diagnostic_detail(diagnostic))
    if exception is not None:
        lines.extend(
            [
                "",
                "Raw exception",
                f"- type: {type(exception).__name__}",
                f"- message: {exception}",
                "",
                "Traceback",
                *traceback.format_exception(type(exception), exception, exception.__traceback__),
            ]
        )
    return "\n".join(line.rstrip("\n") for line in lines).rstrip() + "\n"


def _render_diagnostic_detail(diagnostic: AnalysisDiagnostic) -> list[str]:
    header_parts = [f"[{diagnostic.severity}]", f"[{diagnostic.analyzer_alias}]"]
    if diagnostic.code:
        header_parts.append(diagnostic.code)
    lines = [" ".join(header_parts)]
    if diagnostic.message:
        lines.append(f"message: {diagnostic.message}")
    params = {key: value for key, value in diagnostic.params.items() if value not in ("", None, [], {})}
    remaining_params = _render_known_diagnostic_fields(lines, diagnostic, params)
    if remaining_params:
        lines.append("context:")
        _append_structured_value(lines, remaining_params, indent=2)
    technical_detail = (diagnostic.technical_message_en or "").strip()
    if (
        technical_detail
        and diagnostic.code not in {"MISSING_REQUIRED_COLUMNS"}
        and technical_detail != diagnostic.message.strip()
        and technical_detail not in "\n".join(lines)
    ):
        lines.append(f"technical_detail: {technical_detail}")
    return lines


def _render_known_diagnostic_fields(
    lines: list[str],
    diagnostic: AnalysisDiagnostic,
    params: dict[str, Any],
) -> dict[str, Any]:
    remaining = dict(params)
    for ignored in ("analyzer", "code"):
        remaining.pop(ignored, None)
    path = remaining.pop("path", None)
    filename = remaining.pop("filename", None)
    if path:
        lines.append(f"file: {path}")
    elif filename:
        lines.append(f"filename: {filename}")
    source_file = remaining.pop("source_file", None)
    if source_file:
        lines.append(f"source file: {source_file}")
    report_path = remaining.pop("report_path", None)
    if report_path:
        lines.append(f"report: {report_path}")
    columns = remaining.pop("columns", None)
    if columns:
        label = "missing columns" if diagnostic.code == "MISSING_REQUIRED_COLUMNS" else "columns"
        lines.append(f"{label}:")
        _append_structured_value(lines, list(columns), indent=2)
    missing_values = remaining.pop("missing_values", None)
    if missing_values:
        lines.append("missing values:")
        _append_missing_values(lines, list(missing_values), indent=2)
    count = remaining.pop("count", None)
    if count:
        lines.append(f"count: {count}")
    rows = remaining.pop("rows", None)
    if rows:
        lines.append("examples:" if diagnostic.code == "IBKR_MANUAL_REVIEW_ROWS" else "rows:")
        _append_structured_value(lines, list(rows), indent=2)
    items = remaining.pop("items", None)
    if items:
        lines.append("items:")
        _append_structured_value(lines, list(items), indent=2)
    examples = remaining.pop("examples", None)
    if examples:
        lines.append("examples:")
        _append_structured_value(lines, list(examples), indent=2)
    remaining.pop("bulgarian_summary", None)
    return {
        key: value
        for key, value in remaining.items()
        if value not in ("", None, [], {}) and str(value) != diagnostic.message.strip()
    }


def _append_missing_values(lines: list[str], missing_values: list[Any], *, indent: int) -> None:
    prefix = " " * indent
    for item in missing_values:
        if not isinstance(item, dict):
            lines.append(f"{prefix}- {item}")
            continue
        lines.append(f"{prefix}- platform: {item.get('platform', '')}")
        for key in ("country", "currency", "type", "isin"):
            if item.get(key):
                lines.append(f"{prefix}  {key}: {item[key]}")
        missing = item.get("missing")
        if missing:
            lines.append(f"{prefix}  missing:")
            _append_structured_value(lines, list(missing), indent=indent + 4)


_COMPACT_LIST_SAMPLE_SIZE = 10


def _is_compact_scalar(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _compact_scalar_list(value: list[Any]) -> str | None:
    if not value or not all(_is_compact_scalar(item) for item in value):
        return None
    sample = value[:_COMPACT_LIST_SAMPLE_SIZE]
    rendered = ", ".join(str(item) for item in sample)
    if len(value) > _COMPACT_LIST_SAMPLE_SIZE:
        rendered = f"{rendered}, ..."
    return f"[{rendered}]"


def _append_structured_value(lines: list[str], value: Any, *, indent: int) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        for key in _ordered_diagnostic_keys(value):
            item = value[key]
            if item in ("", None, [], {}):
                continue
            if isinstance(item, dict):
                lines.append(f"{prefix}{key}:")
                _append_structured_value(lines, item, indent=indent + 2)
            elif isinstance(item, list | tuple | set):
                item_list = list(item)
                compact = _compact_scalar_list(item_list)
                if compact is not None:
                    label = f"{key}_sample" if len(item_list) > _COMPACT_LIST_SAMPLE_SIZE else key
                    lines.append(f"{prefix}{label}: {compact}")
                else:
                    lines.append(f"{prefix}{key}:")
                    _append_structured_value(lines, item_list, indent=indent + 2)
            else:
                lines.append(f"{prefix}{key}: {item}")
        return
    if isinstance(value, list | tuple | set):
        value_list = list(value)
        compact = _compact_scalar_list(value_list)
        if compact is not None:
            lines.append(f"{prefix}{compact}")
            return
        for item in value_list:
            if item in ("", None, [], {}):
                continue
            if isinstance(item, dict):
                lines.append(f"{prefix}-")
                _append_structured_value(lines, item, indent=indent + 2)
            elif isinstance(item, list | tuple | set):
                lines.append(f"{prefix}-")
                _append_structured_value(lines, list(item), indent=indent + 2)
            else:
                lines.append(f"{prefix}- {item}")
        return
    lines.append(f"{prefix}{value}")


def _ordered_diagnostic_keys(value: dict[Any, Any]) -> list[Any]:
    preferred = [
        "row",
        "section",
        "symbol",
        "date",
        "listing_exchange",
        "listing_exchange_raw",
        "listing_exchange_normalized",
        "mapped_classification",
        "execution_exchange",
        "review_status",
        "code",
        "reason",
    ]
    keys = list(value)
    ordered = [key for key in preferred if key in value]
    ordered.extend(sorted(key for key in keys if key not in preferred))
    return ordered


def write_standardized_reports(
    *,
    main_report_path: Path,
    raw_report_text: str,
    status: AnalyzerStatus,
    tax_year: int | None = None,
    diagnostics: list[AnalysisDiagnostic],
    diagnostics_title: str,
    diagnostics_extra_lines: list[str] | None = None,
    assumption_notes: list[str] | None = None,
    exception: BaseException | None = None,
) -> Path:
    diagnostics_path = diagnostics_path_for(main_report_path)
    main_report_path.write_text(
        render_main_report(
            status=status,
            tax_year=tax_year,
            raw_declaration_text=raw_report_text,
            diagnostics=diagnostics,
            diagnostics_path=diagnostics_path,
            assumption_notes=assumption_notes,
        ),
        encoding="utf-8",
    )
    diagnostics_path.write_text(
        render_diagnostics_report(
            title=diagnostics_title,
            status=status,
            raw_declaration_text=raw_report_text,
            diagnostics=diagnostics,
            extra_lines=diagnostics_extra_lines,
            exception=exception,
        ),
        encoding="utf-8",
    )
    return diagnostics_path


def print_operational_summary(
    *,
    status: AnalyzerStatus,
    main_report_path: Path,
    diagnostics_path: Path,
    diagnostics: list[AnalysisDiagnostic],
) -> None:
    diagnostics = normalize_diagnostics(diagnostics)
    counts = diagnostic_counts(diagnostics)
    print(f"STATUS: {STDOUT_STATUS[status]}")
    print(f"Main report: {format_path(main_report_path)}")
    print(f"Diagnostics: {format_path(diagnostics_path)}")
    print("Summary:")
    print(f"errors={counts['errors']}")
    print(f"manual_review={counts['manual_review']}")
    print(f"warnings={counts['warnings']}")
