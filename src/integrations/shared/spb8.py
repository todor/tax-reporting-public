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
    "currency",
    "start nav",
    "end nav",
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
        "currency": "currency",
        "start": "start nav",
        "start value": "start nav",
        "start nav": "start nav",
        "end": "end nav",
        "end value": "end nav",
        "end nav": "end nav",
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


def _validate_required_decimal(row: SPB8Row, *, row_number: int) -> None:
    if row.is_bulgaria:
        return
    if row.type_code == "04" and row.isin == "":
        return
    if row.start_nav is None:
        raise SPB8Error(f"row {row_number}: start nav is required for SPB-8 filing rows")
    if row.end_nav is None:
        raise SPB8Error(f"row {row_number}: end nav is required for SPB-8 filing rows")


def read_spb8_csv(path: Path) -> list[SPB8Row]:
    with path.expanduser().open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SPB8Error("SPB-8 input CSV is empty")
        normalized_fields = [normalize_header_name(field) for field in reader.fieldnames]
        missing = [field for field in SPB8_CSV_HEADER if field not in normalized_fields]
        if missing:
            raise SPB8Error(f"SPB-8 input CSV is missing columns: {', '.join(missing)}")

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
            currency = normalized["currency"].strip().upper()
            if currency == "":
                raise SPB8Error(f"row {row_number}: currency is required")
            row = SPB8Row(
                account_name=normalized["account name"].strip() or platform,
                platform=platform,
                type_code=type_code,
                country=country,
                currency=currency,
                start_nav=_parse_decimal(normalized["start nav"], field="start nav", row_number=row_number),
                end_nav=_parse_decimal(normalized["end nav"], field="end nav", row_number=row_number),
                source="csv",
            )
            _validate_required_decimal(row, row_number=row_number)
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
                    row.currency,
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
                currency=currency,
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
        notes.append("Crypto platforms were excluded from SPB-8 because --spb8-exclude-crypto was used.")
    elif any(canonical_platform(row.platform) in crypto for row in filtered):
        notes.append("Crypto SPB-8 treatment may depend on accountant interpretation.")
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
    external_platform_types = {(canonical_platform(row.platform), row.type_code) for row in external_rows}
    merged = list(external_rows)
    for row in analyzer_rows:
        platform_type = (canonical_platform(row.platform), row.type_code)
        if row.type_code != "04" and platform_type in external_platform_types:
            continue
        merged.append(row)
    return merged


def render_spb8_section(rows: list[SPB8Row]) -> list[str]:
    filing_rows = [row for row in rows if not row.is_bulgaria and _row_has_renderable_values(row)]
    if not filing_rows:
        return []
    lines = ["СПБ-8"]
    for row in filing_rows:
        lines.extend(_render_row(row))
    return lines


def render_spb8_notes_section(notes: list[str] | None = None, *, aggregate: bool = False) -> list[str]:
    note_lines = list(notes or [])
    if aggregate:
        note_lines.append("Детайлите по платформи са налични в индивидуалните TXT файлове.")
    if not note_lines:
        return []
    return ["Забележки за СПБ-8", *(f"- {note}" for note in note_lines)]


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


def rows_by_platform(rows: list[SPB8Row]) -> dict[str, list[SPB8Row]]:
    grouped: dict[str, list[SPB8Row]] = defaultdict(list)
    for row in rows:
        grouped[canonical_platform(row.platform)].append(row)
    return dict(grouped)
