from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from integrations.p2p.shared.appendix6_models import (
    Appendix6Part1Row,
    InformativeRow,
    P2PAppendix6Result,
    P2PValidationError,
    SECONDARY_MARKET_MODE_APPENDIX_6,
    ZERO,
)
from integrations.p2p.shared.runtime import validate_secondary_market_mode
from integrations.p2p.shared.text_money import normalize_text_line, parse_decimal_text
from services.pdf_reader import read_pdf_pages

from .constants import PLATFORM_NAME
from .models import AfrangaSummaryMetrics

DECIMAL_TWO = Decimal("0.01")

SUMMARY_PATTERNS = {
    "reporting_year": re.compile(r"Reporting\s+year\s*:\s*(?P<value>\d{4})", re.IGNORECASE),
    "statement_period": re.compile(
        r"for\s+the\s+period\s+between\s+(?P<start>.+?)\s+till\s+(?P<end>.+?)(?:\n|$)",
        re.IGNORECASE,
    ),
    "interest_received": re.compile(
        r"^Income\s+from\s+interest\s+received\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "late_interest_received": re.compile(
        r"^Income\s+from\s+late\s+interest\s+received\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "bonuses": re.compile(
        r"^Bonuses\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "secondary_market": re.compile(
        r"^Income/loss\s+from\s+secondary\s+market\s+discount/premium\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        re.IGNORECASE | re.MULTILINE,
    ),
}

COMPANY_PATTERN = re.compile(
    r"^(?P<company>.+?),\s*company\s+number\s+(?P<number>\d+)\s+registered\s+in\s+(?P<country>.+)$",
    re.IGNORECASE,
)

DETAIL_PATTERN = re.compile(
    r"^(?P<label>Income\s+from\s+interest|Income\s+from\s+late\s+interest)\s+"
    r"EUR\s+(?P<gross>-?[\d.,]+)\s+(?P<wht_pct>-?[\d.,]+)\s*%\s+"
    r"(?P<wht>-?[\d.,]+)\s+(?P<net>-?[\d.,]+)$",
    re.IGNORECASE,
)

TOTAL_PATTERN = re.compile(
    r"^Total\s+(?P<gross>-?[\d.,]+)\s+(?P<wht>-?[\d.,]+)\s+(?P<net>-?[\d.,]+)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _CompanyAccumulator:
    name: str
    eik: str
    gross_603: Decimal = ZERO
    wht: Decimal = ZERO
    net: Decimal = ZERO


@dataclass(slots=True)
class _AppendixParseResult:
    part1_rows: list[Appendix6Part1Row]
    gross_sum: Decimal
    net_sum: Decimal
    wht_sum: Decimal
    warnings: list[str]


def _extract_single_match(pattern: re.Pattern[str], text: str, *, field_name: str) -> re.Match[str]:
    matches = list(pattern.finditer(text))
    if not matches:
        raise P2PValidationError(f"missing required field in Afranga PDF: {field_name}")
    if len(matches) > 1:
        raise P2PValidationError(f"ambiguous matches for Afranga field: {field_name}")
    return matches[0]


def _extract_summary_metrics(full_text: str) -> AfrangaSummaryMetrics:
    year_match = _extract_single_match(SUMMARY_PATTERNS["reporting_year"], full_text, field_name="Reporting year")
    period_match = _extract_single_match(
        SUMMARY_PATTERNS["statement_period"],
        full_text,
        field_name="statement period",
    )
    interest_match = _extract_single_match(
        SUMMARY_PATTERNS["interest_received"],
        full_text,
        field_name="Income from interest received",
    )
    late_interest_match = _extract_single_match(
        SUMMARY_PATTERNS["late_interest_received"],
        full_text,
        field_name="Income from late interest received",
    )
    bonuses_match = _extract_single_match(
        SUMMARY_PATTERNS["bonuses"],
        full_text,
        field_name="Bonuses",
    )
    secondary_match = _extract_single_match(
        SUMMARY_PATTERNS["secondary_market"],
        full_text,
        field_name="Income/loss from secondary market discount/premium",
    )

    start = normalize_text_line(period_match.group("start"))
    end = normalize_text_line(period_match.group("end"))
    return AfrangaSummaryMetrics(
        reporting_year=int(year_match.group("value")),
        statement_period=f"{start} till {end}",
        interest_received_eur=parse_decimal_text(interest_match.group("amount"), field_name="interest_received"),
        late_interest_received_eur=parse_decimal_text(
            late_interest_match.group("amount"),
            field_name="late_interest_received",
        ),
        bonuses_eur=parse_decimal_text(bonuses_match.group("amount"), field_name="bonuses"),
        secondary_market_result_eur=parse_decimal_text(
            secondary_match.group("amount"),
            field_name="secondary_market",
        ),
    )


def _extract_appendix_lines(pages: list[str]) -> list[str]:
    all_lines: list[str] = []
    for page in pages:
        for raw_line in page.splitlines():
            line = normalize_text_line(raw_line)
            if line != "":
                all_lines.append(line)

    start_idx: int | None = None
    for idx, line in enumerate(all_lines):
        if "appendix no. 1" in line.lower():
            start_idx = idx
            break

    if start_idx is None:
        return []

    return all_lines[start_idx:]


def _is_non_data_line(line: str) -> bool:
    lower = line.lower()
    if lower.startswith("appendix no."):
        return True
    if "break-down of income earned by borrower country and income type" in lower:
        return True
    if "income type" in lower and "gross amount" in lower:
        return True
    if lower.startswith("period") and "country" in lower:
        return True
    if lower.startswith("currency") and "gross" in lower:
        return True
    if line.isupper() and re.fullmatch(r"[A-Z\- ]+", line) is not None:
        return True
    return False


def _parse_appendix_companies(appendix_lines: list[str]) -> _AppendixParseResult:
    if not appendix_lines:
        return _AppendixParseResult(part1_rows=[], gross_sum=ZERO, net_sum=ZERO, wht_sum=ZERO, warnings=[])

    companies: list[_CompanyAccumulator] = []
    by_key: dict[tuple[str, str], _CompanyAccumulator] = {}
    current_company: _CompanyAccumulator | None = None

    detail_gross_sum = ZERO
    detail_net_sum = ZERO
    detail_wht_sum = ZERO

    total_rows: list[tuple[Decimal, Decimal, Decimal]] = []

    for raw_line in appendix_lines:
        line = normalize_text_line(raw_line)
        if line == "" or _is_non_data_line(line):
            continue

        company_match = COMPANY_PATTERN.match(line)
        if company_match is not None:
            name = normalize_text_line(company_match.group("company"))
            eik = company_match.group("number").strip()
            key = (name, eik)
            current_company = by_key.get(key)
            if current_company is None:
                current_company = _CompanyAccumulator(name=name, eik=eik)
                by_key[key] = current_company
                companies.append(current_company)
            continue

        detail_match = DETAIL_PATTERN.match(line)
        if detail_match is not None:
            if current_company is None:
                raise P2PValidationError(
                    "Afranga appendix detail row appears before any company entry; cannot assign payer"
                )
            gross = parse_decimal_text(detail_match.group("gross"), field_name="appendix detail gross")
            wht = parse_decimal_text(detail_match.group("wht"), field_name="appendix detail wht")
            net = parse_decimal_text(detail_match.group("net"), field_name="appendix detail net")
            current_company.gross_603 += gross
            current_company.wht += wht
            current_company.net += net
            detail_gross_sum += gross
            detail_wht_sum += wht
            detail_net_sum += net
            continue

        total_match = TOTAL_PATTERN.match(line)
        if total_match is not None:
            total_rows.append(
                (
                    parse_decimal_text(total_match.group("gross"), field_name="appendix total gross"),
                    parse_decimal_text(total_match.group("wht"), field_name="appendix total wht"),
                    parse_decimal_text(total_match.group("net"), field_name="appendix total net"),
                )
            )
            continue

    part1_rows = [
        Appendix6Part1Row(
            payer_name=company.name,
            payer_eik=company.eik,
            code="603",
            amount=company.gross_603,
        )
        for company in companies
    ]

    warnings: list[str] = []
    if total_rows:
        total_gross, total_wht, total_net = total_rows[-1]

        def q2(value: Decimal) -> Decimal:
            return value.quantize(DECIMAL_TWO, rounding=ROUND_HALF_UP)

        if q2(total_gross) != q2(detail_gross_sum) or q2(total_wht) != q2(detail_wht_sum) or q2(total_net) != q2(detail_net_sum):
            warnings.append(
                "Appendix total row mismatch vs parsed detail rows "
                f"(detail gross={q2(detail_gross_sum)}, wht={q2(detail_wht_sum)}, net={q2(detail_net_sum)}; "
                f"appendix gross={q2(total_gross)}, wht={q2(total_wht)}, net={q2(total_net)})"
            )

    return _AppendixParseResult(
        part1_rows=part1_rows,
        gross_sum=detail_gross_sum,
        net_sum=detail_net_sum,
        wht_sum=detail_wht_sum,
        warnings=warnings,
    )


def parse_afranga_pages(
    *,
    pages: list[str],
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)

    full_text = "\n\n".join(page for page in pages if page.strip() != "")
    metrics = _extract_summary_metrics(full_text)

    appendix_lines = _extract_appendix_lines(pages)
    appendix = _parse_appendix_companies(appendix_lines)

    total_interest_received = metrics.interest_received_eur + metrics.late_interest_received_eur
    aggregate_code_603 = total_interest_received - appendix.net_sum
    if aggregate_code_603 < ZERO:
        raise P2PValidationError(
            "invalid Afranga aggregate_code_603 (negative result): "
            f"Income from interest received={metrics.interest_received_eur}, "
            f"Income from late interest received={metrics.late_interest_received_eur}, "
            f"Total interest received={total_interest_received}, "
            f"Net Sum from Appendix={appendix.net_sum}, "
            f"aggregate_code_603={aggregate_code_603}"
        )

    aggregate_code_606 = metrics.bonuses_eur
    if metrics.secondary_market_result_eur > ZERO:
        aggregate_code_606 += metrics.secondary_market_result_eur

    taxable_code_603 = sum((row.amount for row in appendix.part1_rows), ZERO) + aggregate_code_603
    taxable_code_606 = aggregate_code_606

    informative_rows = [
        InformativeRow("Reporting year", str(metrics.reporting_year)),
        InformativeRow("Statement period", metrics.statement_period),
        InformativeRow("Income from interest received (EUR)", metrics.interest_received_eur),
        InformativeRow("Income from late interest received (EUR)", metrics.late_interest_received_eur),
        InformativeRow("Bonuses (EUR)", metrics.bonuses_eur),
        InformativeRow(
            "Income/loss from secondary market discount/premium (EUR)",
            metrics.secondary_market_result_eur,
        ),
        InformativeRow("Net Sum from Appendix (EUR)", appendix.net_sum),
        InformativeRow("Total WHT from Appendix (EUR)", appendix.wht_sum),
        InformativeRow("Secondary-market mode used", secondary_market_mode),
    ]

    return P2PAppendix6Result(
        platform=PLATFORM_NAME,
        tax_year=metrics.reporting_year,
        part1_rows=appendix.part1_rows,
        aggregate_code_603=aggregate_code_603,
        aggregate_code_606=aggregate_code_606,
        taxable_code_603=taxable_code_603,
        taxable_code_606=taxable_code_606,
        withheld_tax=appendix.wht_sum,
        informative_rows=informative_rows,
        warnings=appendix.warnings,
    )


def parse_afranga_pdf(
    *,
    input_pdf: str | Path,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    pages = read_pdf_pages(input_pdf)
    return parse_afranga_pages(
        pages=pages,
        secondary_market_mode=secondary_market_mode,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
