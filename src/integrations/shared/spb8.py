from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .countries import normalize_country_name

SPB8_CSV_HEADER = [
    "account name",
    "platform",
    "type",
    "country",
    "ISIN",
    "currency",
    "start amount",
    "end amount",
]

SPB8_TYPES: dict[str, str] = {
    "01": "01. Предоставени финансови кредити",
    "02": "02. Получени финансови кредити",
    "03": "03. Сметки, открити в чужбина",
    "04": "04. Придобити ценни книжа",
}

PLATFORM_COUNTRY: dict[str, str] = {
    "lendermarket": "Ирландия",
    "afranga": "България",
    "estateguru": "Естония",
    "robocash": "Хърватия",
    "iuvo": "Естония",
    "bondora_go_grow": "Естония",
    "bondora_go_and_grow": "Естония",
    "bondora": "Естония",
    "ibkr": "Ирландия",
    "interactive_brokers": "Ирландия",
    "interactivebrokers": "Ирландия",
    "binance": "Франция",
    "binance_futures": "Франция",
    "finexify": "България",
    "karol": "България",
    "kraken": "Ирландия",
    "coinbase": "Ирландия",
    "crypto_com": "Малта",
    "crypto.com": "Малта",
    "revolut": "Литва",
}

CRYPTO_PLATFORMS = {"binance", "binance_futures", "kraken", "coinbase", "crypto_com", "crypto.com"}
P2P_PLATFORMS = {
    "lendermarket",
    "afranga",
    "estateguru",
    "robocash",
    "iuvo",
    "bondora_go_grow",
    "bondora_go_and_grow",
    "bondora",
}
FUND_PLATFORMS = {"finexify", "karol", "revolut"}
AUTOMATED_SPB8_PLATFORMS = {"ibkr"}


class SPB8Error(Exception):
    """Raised when SPB-8 input cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class SPB8Row:
    account_name: str
    platform: str
    type_code: str
    country: str
    currency: str
    start_nav: Decimal | None = None
    end_nav: Decimal | None = None
    maturity: str = ""
    isin: str = ""
    source: str = "analyzer"

    @property
    def type_label(self) -> str:
        return SPB8_TYPES[self.type_code]

    @property
    def is_bulgaria(self) -> bool:
        return self.country.strip().casefold() in {"българия", "bulgaria"}


def normalize_header_name(value: str) -> str:
    normalized = re.sub(r"[_-]+", " ", value.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    aliases = {
        "account": "account name",
        "account name": "account name",
        "platform": "platform",
        "type": "type",
        "country": "country",
        "isin": "ISIN",
        "currency": "currency",
        "start": "start amount",
        "start amount": "start amount",
        "start value": "start amount",
        "start nav": "start amount",
        "end": "end amount",
        "end amount": "end amount",
        "end value": "end amount",
        "end nav": "end amount",
    }
    return aliases.get(normalized, normalized)


def normalize_type(value: str) -> str:
    raw = value.strip()
    if raw == "":
        return ""
    match = re.match(r"^0?([1-4])(?:\D|$)", raw)
    if not match:
        raise SPB8Error(f"invalid SPB-8 type: {value!r}")
    return f"0{match.group(1)}"


def canonical_platform(value: str) -> str:
    platform = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "go_grow": "bondora_go_grow",
        "bondora_go_and_grow": "bondora_go_grow",
        "interactive_brokers": "ibkr",
        "interactivebrokers": "ibkr",
        "crypto.com": "crypto_com",
    }
    return aliases.get(platform, platform)


def infer_type_for_platform(platform: str, *, ibkr_kind: str | None = None) -> str:
    platform = canonical_platform(platform)
    if platform == "ibkr":
        return "04" if ibkr_kind == "securities" else "03"
    if platform in P2P_PLATFORMS:
        return "01"
    if platform in CRYPTO_PLATFORMS or platform in FUND_PLATFORMS:
        return "03"
    raise SPB8Error(f"unknown SPB-8 platform: {platform!r}")


def infer_country_for_platform(platform: str) -> str:
    platform = canonical_platform(platform)
    country = PLATFORM_COUNTRY.get(platform)
    if country is None:
        raise SPB8Error(f"unknown SPB-8 platform: {platform!r}")
    return country


def normalize_country(value: str, *, platform: str, row_number: int | None = None) -> str:
    if value.strip() == "":
        return infer_country_for_platform(platform)
    normalized = normalize_country_name(value)
    if normalized == "":
        prefix = f"row {row_number}: " if row_number is not None else ""
        raise SPB8Error(f"{prefix}unknown SPB-8 country: {value!r}")
    return normalized


def _parse_decimal(value: str, *, field: str, row_number: int) -> Decimal | None:
    raw = value.strip()
    if raw == "":
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise SPB8Error(f"row {row_number}: invalid {field}: {value!r}") from exc


def _validate_type_specific_fields(
    *,
    type_code: str,
    isin: str,
    currency: str,
    row_number: int,
) -> tuple[str, str]:
    normalized_isin = isin.strip().upper()
    normalized_currency = currency.strip().upper()
    if type_code == "04":
        if normalized_isin in {"", "-"}:
            raise SPB8Error(f"row {row_number}: ISIN is required for SPB-8 type 04 rows")
        if normalized_currency not in {"", "-"}:
            raise SPB8Error(f"row {row_number}: currency must be '-' for SPB-8 type 04 rows")
        return normalized_isin, ""
    if normalized_isin not in {"", "-"}:
        raise SPB8Error(f"row {row_number}: ISIN must be '-' for SPB-8 type {type_code} rows")
    if not re.fullmatch(r"[A-Z]{3}", normalized_currency):
        raise SPB8Error(f"row {row_number}: currency must be a 3-letter ISO code for SPB-8 type {type_code} rows")
    return "", normalized_currency


def read_spb8_csv(path: Path) -> list[SPB8Row]:
    with path.expanduser().open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SPB8Error("SPB-8 input CSV is empty")
        normalized_fields = [normalize_header_name(field) for field in reader.fieldnames]
        required_fields = [field for field in SPB8_CSV_HEADER if field != "ISIN"]
        missing = [field for field in required_fields if field not in normalized_fields]
        if missing:
            expected = ", ".join(SPB8_CSV_HEADER)
            raise SPB8Error(
                f"SPB-8 input CSV is missing columns: {', '.join(missing)}. Expected header: {expected}"
            )

        rows: list[SPB8Row] = []
        for row_number, raw_row in enumerate(reader, start=2):
            normalized = {
                normalize_header_name(key): (value or "")
                for key, value in raw_row.items()
                if key is not None
            }
            platform = canonical_platform(normalized["platform"])
            if platform not in {canonical_platform(item) for item in PLATFORM_COUNTRY}:
                raise SPB8Error(f"row {row_number}: unknown SPB-8 platform: {platform!r}")
            type_code = normalize_type(normalized["type"]) if normalized["type"].strip() else infer_type_for_platform(platform)
            if type_code not in SPB8_TYPES:
                raise SPB8Error(f"row {row_number}: unsupported SPB-8 type: {normalized['type']!r}")
            country = normalize_country(normalized["country"], platform=platform, row_number=row_number)
            isin, currency = _validate_type_specific_fields(
                type_code=type_code,
                isin=normalized.get("ISIN", "-"),
                currency=normalized["currency"],
                row_number=row_number,
            )
            row = SPB8Row(
                account_name=normalized["account name"].strip() or platform,
                platform=platform,
                type_code=type_code,
                country=country,
                currency=currency,
                start_nav=_parse_decimal(normalized["start amount"], field="start amount", row_number=row_number),
                end_nav=_parse_decimal(normalized["end amount"], field="end amount", row_number=row_number),
                isin=isin,
                source="csv",
            )
            rows.append(row)
    return rows


def write_spb8_csv(path: Path, rows: list[SPB8Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SPB8_CSV_HEADER)
        for row in rows:
            writer.writerow(
                [
                    row.account_name,
                    row.platform,
                    row.type_code,
                    row.country,
                    row.isin if row.type_code == "04" else "-",
                    "-" if row.type_code == "04" else row.currency,
                    _decimal_to_csv(row.start_nav),
                    _decimal_to_csv(row.end_nav),
                ]
            )


def _decimal_to_csv(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def default_platform_rows(*, platform: str, account_name: str, currency: str = "EUR") -> list[SPB8Row]:
    platform = canonical_platform(platform)
    if platform == "ibkr":
        return [
            SPB8Row(
                account_name=f"{account_name} securities",
                platform=platform,
                type_code="04",
                country=infer_country_for_platform(platform),
                currency="",
                isin="-",
            ),
            SPB8Row(
                account_name=f"{account_name} cash",
                platform=platform,
                type_code="03",
                country=infer_country_for_platform(platform),
                currency=currency,
            ),
        ]
    return [
        SPB8Row(
            account_name=account_name,
            platform=platform,
            type_code=infer_type_for_platform(platform),
            country=infer_country_for_platform(platform),
            currency=currency,
        )
    ]


def manual_input_template_rows_for_platform(
    *,
    platform: str,
    account_name: str,
    currency: str = "EUR",
) -> list[SPB8Row]:
    platform = canonical_platform(platform)
    if platform in AUTOMATED_SPB8_PLATFORMS:
        return []
    return default_platform_rows(platform=platform, account_name=account_name, currency=currency)


def filter_rows_for_options(
    rows: list[SPB8Row],
    *,
    enabled: bool,
    exclude_crypto: bool,
) -> tuple[list[SPB8Row], list[str]]:
    if not enabled:
        return [], []
    notes: list[str] = []
    filtered = list(rows)
    crypto = {canonical_platform(platform) for platform in CRYPTO_PLATFORMS}
    if exclude_crypto:
        filtered = [row for row in filtered if canonical_platform(row.platform) not in crypto]
        notes.append("Крипто платформите са изключени от СПБ-8, защото е използван --spb8-exclude-crypto.")
    elif any(canonical_platform(row.platform) in crypto for row in filtered):
        notes.append(
            "Използвана интерпретация за този отчет: крипто платформите са включени като "
            "03. Сметки, открити в чужбина, с валута EUR по подразбиране. "
            "Потвърдете с вашия счетоводител."
        )
    if any(row.is_bulgaria for row in filtered):
        notes.append("Не се включва в СПБ-8: държава България.")
    return filtered, notes


def aggregate_spb8_rows(rows: list[SPB8Row]) -> list[SPB8Row]:
    buckets: dict[tuple[str, str, str, str, str], SPB8Row] = {}
    for row in rows:
        if row.is_bulgaria:
            continue
        if row.type_code == "04" and row.isin:
            key = (row.type_code, row.isin, row.country, row.currency, row.maturity)
        else:
            key = (row.type_code, "", row.country, row.currency, row.maturity)
        current = buckets.get(key)
        if current is None:
            buckets[key] = row
            continue
        buckets[key] = replace(
            current,
            start_nav=_sum_optional(current.start_nav, row.start_nav),
            end_nav=_sum_optional(current.end_nav, row.end_nav),
            account_name=f"{current.account_name}, {row.account_name}",
        )
    return [buckets[key] for key in sorted(buckets)]


def _sum_optional(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return left + right


def merge_external_platform_rows(analyzer_rows: list[SPB8Row], external_rows: list[SPB8Row]) -> list[SPB8Row]:
    analyzer_by_key = {_override_key(row): row for row in analyzer_rows}
    merged: list[SPB8Row] = []
    used_keys: set[tuple[str, ...]] = set()
    for external in external_rows:
        key = _override_key(external)
        analyzer = analyzer_by_key.get(key)
        if analyzer is None:
            merged.append(external)
        else:
            used_keys.add(key)
            merged.append(
                replace(
                    analyzer,
                    account_name=external.account_name or analyzer.account_name,
                    country=external.country or analyzer.country,
                    currency=external.currency if external.type_code != "04" else "",
                    start_nav=external.start_nav if external.start_nav is not None else analyzer.start_nav,
                    end_nav=external.end_nav if external.end_nav is not None else analyzer.end_nav,
                    source="csv",
                )
            )
    for analyzer in analyzer_rows:
        if _override_key(analyzer) not in used_keys:
            merged.append(analyzer)
    return merged


def missing_spb8_value_notes(rows: list[SPB8Row]) -> list[str]:
    notes: list[str] = []
    for row in rows:
        if row.is_bulgaria:
            continue
        if row.type_code != "04" and _row_has_zero_rendered_end_value(row):
            continue
        if row.start_nav is None:
            notes.append(_missing_value_note(row, field_label="start amount"))
        if row.end_nav is None:
            notes.append(_missing_value_note(row, field_label="end amount"))
    return notes


def _override_key(row: SPB8Row) -> tuple[str, ...]:
    platform = canonical_platform(row.platform)
    if row.type_code == "04":
        return (platform, row.type_code, row.isin.strip().upper())
    return (
        platform,
        row.type_code,
        row.country.strip().casefold(),
        row.currency.strip().upper(),
        row.maturity.strip().casefold(),
    )


def _missing_value_note(row: SPB8Row, *, field_label: str) -> str:
    if row.type_code == "04":
        return (
            "СПБ-8: липсва стойност за "
            f"{field_label} за ISIN={row.isin}; попълнете я в SPB-8 input файла."
        )
    return (
        "СПБ-8: липсва стойност за "
        f"{field_label} за platform={row.platform}, type={row.type_code}, country={row.country}, currency={row.currency}; "
        "попълнете я в SPB-8 input файла."
    )


def render_spb8_section(
    rows: list[SPB8Row],
    *,
    notes: list[str] | None = None,
    aggregate: bool = False,
) -> list[str]:
    note_lines = list(notes or [])
    if aggregate:
        note_lines.append("Детайлите по платформи са налични в индивидуалните TXT файлове.")
    filing_source_rows = [row for row in rows if not row.is_bulgaria]
    rows_are_blocked = bool(missing_spb8_value_notes(filing_source_rows))
    filing_rows = [row for row in filing_source_rows if _row_has_renderable_values(row)]
    if rows_are_blocked:
        filing_rows = []
    if not filing_rows and not note_lines:
        return []
    lines = ["СПБ-8"]
    if filing_rows:
        lines.append("Данни за попълване")
        for row in filing_rows:
            lines.extend(_render_row(row))
    if note_lines:
        if filing_rows:
            lines.append("")
        lines.append("Бележки към СПБ-8")
        lines.extend(f"- {note}" for note in note_lines)
    return lines


def render_spb8_notes_section(notes: list[str] | None = None, *, aggregate: bool = False) -> list[str]:
    return render_spb8_section([], notes=notes, aggregate=aggregate)


def _row_has_renderable_values(row: SPB8Row) -> bool:
    return _row_has_nonzero_rendered_end_value(row)


def _render_row(row: SPB8Row) -> list[str]:
    if row.type_code == "04":
        return [
            f"- Тип на вземането: {row.type_label}",
            f"  ISIN: {row.isin or '-'}",
            f"  Размер в началото на отчетната година: {_format_optional(row.start_nav)}",
            f"  Размер в края на отчетната година: {_format_optional(row.end_nav)}",
        ]
    return [
        f"- Тип на вземането: {row.type_label}",
        f"  Матуритет: {row.maturity}",
        f"  Държава: {row.country}",
        f"  Валута: {row.currency}",
        "  Размер в началото на отчетната година (в хиляди валутни единици): "
        f"{_format_thousands(row.start_nav)}",
        "  Размер в края на отчетната година (в хиляди валутни единици): "
        f"{_format_thousands(row.end_nav)}",
    ]


def _format_thousands(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format((value / Decimal("1000")).quantize(Decimal("0.01")), "f")


def _format_optional(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _row_has_nonzero_rendered_end_value(row: SPB8Row) -> bool:
    end_value = _format_optional(row.end_nav) if row.type_code == "04" else _format_thousands(row.end_nav)
    if end_value == "":
        return False
    return Decimal(end_value) != Decimal("0")


def _row_has_zero_rendered_end_value(row: SPB8Row) -> bool:
    end_value = _format_optional(row.end_nav) if row.type_code == "04" else _format_thousands(row.end_nav)
    if end_value == "":
        return False
    return Decimal(end_value) == Decimal("0")


def rows_by_platform(rows: list[SPB8Row]) -> dict[str, list[SPB8Row]]:
    grouped: dict[str, list[SPB8Row]] = defaultdict(list)
    for row in rows:
        grouped[canonical_platform(row.platform)].append(row)
    return dict(grouped)
