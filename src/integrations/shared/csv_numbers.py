from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Literal

CsvDecimalSeparatorMode = Literal["auto", "dot", "comma"]
CsvDecimalSeparator = Literal["dot", "comma"]

CSV_DECIMAL_SEPARATOR_MODES: tuple[CsvDecimalSeparatorMode, ...] = ("auto", "dot", "comma")
DEFAULT_CSV_DECIMAL_SEPARATOR: CsvDecimalSeparator = "dot"
_CURRENT_CSV_DECIMAL_SEPARATOR: ContextVar[CsvDecimalSeparator] = ContextVar(
    "current_csv_decimal_separator",
    default=DEFAULT_CSV_DECIMAL_SEPARATOR,
)

_SPACE_CHARS = " \u00a0\u202f"
_SPACES_TRANSLATION = {ord(char): None for char in _SPACE_CHARS}
_NUMERIC_CLEAN_RE = re.compile(r"[^0-9+\-., \u00a0\u202f]")


class CsvDecimalParseError(ValueError):
    pass


class CsvDecimalSeparatorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CsvDecimalEvidence:
    row_number: int
    column_name: str
    value: str
    evidence: Literal["dot", "comma", "ambiguous"]


@dataclass(frozen=True, slots=True)
class CsvDecimalFormatInfo:
    analyzer_alias: str
    input_path: Path
    mode: CsvDecimalSeparatorMode
    separator: CsvDecimalSeparator
    source: Literal["explicit", "auto", "default"]
    evidence: tuple[CsvDecimalEvidence, ...] = ()
    ambiguous_values: tuple[CsvDecimalEvidence, ...] = ()

    @property
    def separator_symbol(self) -> str:
        return "." if self.separator == "dot" else ","

    @property
    def main_label_bg(self) -> str:
        if self.source == "explicit":
            return f'десетичен разделител "{self.separator_symbol}" зададен ръчно'
        if self.source == "auto":
            return f'автоматично разпознат десетичен разделител "{self.separator_symbol}"'
        return f'използван е стандартният десетичен разделител "{self.separator_symbol}"'


@dataclass(slots=True)
class CsvDecimalDetector:
    analyzer_alias: str
    input_path: Path
    trusted_default: CsvDecimalSeparator = DEFAULT_CSV_DECIMAL_SEPARATOR
    _dot_evidence: list[CsvDecimalEvidence] = field(default_factory=list)
    _comma_evidence: list[CsvDecimalEvidence] = field(default_factory=list)
    _ambiguous_values: list[CsvDecimalEvidence] = field(default_factory=list)

    def observe(self, value: str, *, row_number: int, column_name: str) -> None:
        evidence = classify_csv_decimal_evidence(value)
        if evidence is None:
            return
        item = CsvDecimalEvidence(
            row_number=row_number,
            column_name=column_name,
            value=value,
            evidence=evidence,
        )
        if evidence == "dot":
            self._dot_evidence.append(item)
        elif evidence == "comma":
            self._comma_evidence.append(item)
        else:
            self._ambiguous_values.append(item)

    def observe_rows(self, rows: Iterable[dict[str, str]], *, row_number_key: str | None = None) -> None:
        for ordinal, row in enumerate(rows, start=1):
            if row_number_key is not None:
                try:
                    row_number = int(row.get(row_number_key, ordinal))
                except ValueError:
                    row_number = ordinal
            else:
                row_number = ordinal
            for column_name, value in row.items():
                self.observe(value, row_number=row_number, column_name=column_name)

    def resolve(self, mode: CsvDecimalSeparatorMode) -> CsvDecimalFormatInfo:
        if mode in {"dot", "comma"}:
            return CsvDecimalFormatInfo(
                analyzer_alias=self.analyzer_alias,
                input_path=self.input_path,
                mode=mode,
                separator=mode,
                source="explicit",
                evidence=tuple(self._evidence_for(mode)),
                ambiguous_values=tuple(self._ambiguous_values),
            )
        if self._dot_evidence and self._comma_evidence:
            raise mixed_csv_decimal_separator_error(
                analyzer_alias=self.analyzer_alias,
                input_path=self.input_path,
                dot_evidence=self._dot_evidence,
                comma_evidence=self._comma_evidence,
            )
        if self._comma_evidence:
            return CsvDecimalFormatInfo(
                analyzer_alias=self.analyzer_alias,
                input_path=self.input_path,
                mode=mode,
                separator="comma",
                source="auto",
                evidence=tuple(self._comma_evidence),
                ambiguous_values=tuple(self._ambiguous_values),
            )
        if self._dot_evidence:
            return CsvDecimalFormatInfo(
                analyzer_alias=self.analyzer_alias,
                input_path=self.input_path,
                mode=mode,
                separator="dot",
                source="auto",
                evidence=tuple(self._dot_evidence),
                ambiguous_values=tuple(self._ambiguous_values),
            )
        return CsvDecimalFormatInfo(
            analyzer_alias=self.analyzer_alias,
            input_path=self.input_path,
            mode=mode,
            separator=self.trusted_default,
            source="default",
            evidence=(),
            ambiguous_values=tuple(self._ambiguous_values),
        )

    def _evidence_for(self, separator: CsvDecimalSeparator) -> list[CsvDecimalEvidence]:
        return self._dot_evidence if separator == "dot" else self._comma_evidence


def parse_csv_decimal(
    value: str,
    *,
    decimal_separator: CsvDecimalSeparator | None = None,
    strip_non_numeric: bool = False,
) -> Decimal:
    resolved_separator = decimal_separator or current_csv_decimal_separator()
    normalized = normalize_csv_decimal_text(
        value,
        decimal_separator=resolved_separator,
        strip_non_numeric=strip_non_numeric,
    )
    if normalized in {"", "+", "-", ".", "+.", "-."}:
        raise CsvDecimalParseError(value)
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise CsvDecimalParseError(value) from exc


def try_parse_csv_decimal(
    value: str,
    *,
    decimal_separator: CsvDecimalSeparator | None = None,
    strip_non_numeric: bool = False,
) -> Decimal | None:
    try:
        return parse_csv_decimal(
            value,
            decimal_separator=decimal_separator,
            strip_non_numeric=strip_non_numeric,
        )
    except CsvDecimalParseError:
        return None


def normalize_csv_decimal_text(
    value: str,
    *,
    decimal_separator: CsvDecimalSeparator | None = None,
    strip_non_numeric: bool = False,
) -> str:
    resolved_separator = decimal_separator or current_csv_decimal_separator()
    text = value.strip()
    if strip_non_numeric:
        text = _NUMERIC_CLEAN_RE.sub("", text)
    text = text.translate(_SPACES_TRANSLATION)
    if text == "":
        return ""
    sign = ""
    if text[0] in "+-":
        sign = text[0]
        text = text[1:]
    if text in {"", ".", ","}:
        return sign + text
    if resolved_separator == "dot":
        return sign + _normalize_dot_decimal_text(text)
    return sign + _normalize_comma_decimal_text(text)


def current_csv_decimal_separator() -> CsvDecimalSeparator:
    return _CURRENT_CSV_DECIMAL_SEPARATOR.get()


def set_current_csv_decimal_separator(separator: CsvDecimalSeparator) -> Token[CsvDecimalSeparator]:
    return _CURRENT_CSV_DECIMAL_SEPARATOR.set(separator)


def reset_current_csv_decimal_separator(token: Token[CsvDecimalSeparator]) -> None:
    _CURRENT_CSV_DECIMAL_SEPARATOR.reset(token)


@contextmanager
def csv_decimal_separator_context(separator: CsvDecimalSeparator):
    token = set_current_csv_decimal_separator(separator)
    try:
        yield
    finally:
        reset_current_csv_decimal_separator(token)


def classify_csv_decimal_evidence(value: str) -> Literal["dot", "comma", "ambiguous"] | None:
    text = _clean_numeric_candidate(value)
    if text is None:
        return None
    if text[0] in "+-":
        text = text[1:]
    if "." in text and "," in text:
        return "dot" if text.rfind(".") > text.rfind(",") and _valid_dot_decimal_text(text) else None
    if "." in text:
        parts = text.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit():
            return "ambiguous"
        return "dot" if _valid_dot_decimal_text(text) else None
    if "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit():
            return "ambiguous"
        return "comma" if _valid_comma_decimal_text(text) else None
    return None


def mixed_csv_decimal_separator_error(
    *,
    analyzer_alias: str,
    input_path: Path,
    dot_evidence: list[CsvDecimalEvidence],
    comma_evidence: list[CsvDecimalEvidence],
) -> CsvDecimalSeparatorError:
    dot = dot_evidence[0]
    comma = comma_evidence[0]
    return CsvDecimalSeparatorError(
        "CSV file appears to contain mixed decimal separators "
        f"(analyzer={analyzer_alias}, file={input_path}, "
        f"dot_evidence=row {dot.row_number} column {dot.column_name} value {dot.value!r}, "
        f"comma_evidence=row {comma.row_number} column {comma.column_name} value {comma.value!r}). "
        f"Use --{analyzer_alias}-csv-decimal-separator dot or "
        f"--{analyzer_alias}-csv-decimal-separator comma only if you know the correct convention."
    )


def _clean_numeric_candidate(value: str) -> str | None:
    text = value.strip()
    if text == "":
        return None
    cleaned = text.translate(_SPACES_TRANSLATION)
    if cleaned in {"", "+", "-", ".", ",", "+.", "-.", "+,", "-,"}:
        return None
    if not re.fullmatch(r"[+-]?[0-9][0-9.,]*", cleaned):
        return None
    return cleaned


def _normalize_dot_decimal_text(text: str) -> str:
    if "," not in text:
        return text
    if "." in text:
        integer, fraction = text.rsplit(".", 1)
        if not fraction.isdigit() or not _valid_grouped_integer(integer, ","):
            raise CsvDecimalParseError(text)
        return integer.replace(",", "") + "." + fraction
    if not _valid_grouped_integer(text, ","):
        raise CsvDecimalParseError(text)
    return text.replace(",", "")


def _normalize_comma_decimal_text(text: str) -> str:
    if "." in text:
        raise CsvDecimalParseError(text)
    if "," not in text:
        return text
    integer, fraction = text.rsplit(",", 1)
    if not integer.isdigit() or not fraction.isdigit():
        raise CsvDecimalParseError(text)
    return integer + "." + fraction


def _valid_dot_decimal_text(text: str) -> bool:
    if "." not in text:
        return False
    integer, fraction = text.rsplit(".", 1)
    return fraction.isdigit() and (integer.isdigit() or _valid_grouped_integer(integer, ","))


def _valid_comma_decimal_text(text: str) -> bool:
    if "." in text or "," not in text:
        return False
    integer, fraction = text.rsplit(",", 1)
    return integer.isdigit() and fraction.isdigit()


def _valid_grouped_integer(text: str, separator: str) -> bool:
    parts = text.split(separator)
    if len(parts) == 1:
        return parts[0].isdigit()
    if not parts[0].isdigit() or len(parts[0]) not in {1, 2, 3}:
        return False
    return all(part.isdigit() and len(part) == 3 for part in parts[1:])


__all__ = [name for name in globals() if not name.startswith("__")]
