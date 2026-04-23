from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from integrations.p2p.shared.appendix6_models import (
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
from .models import RobocashSummaryMetrics

_YEAR_PATTERN = re.compile(r"Tax\s+report\s+for\s+the\s+year\s+ended\s+\d{2}\.\d{2}\.(?P<year>\d{4})", re.IGNORECASE)


def _normalized_text(pages: list[str]) -> str:
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = normalize_text_line(raw)
            if line != "":
                lines.append(line)
    return "\n".join(lines)


def _extract_single_amount(pattern: str, text: str, *, field_name: str) -> Decimal:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if match is None:
        raise P2PValidationError(f"missing required field in Robocash PDF: {field_name}")
    return parse_decimal_text(match.group("amount"), field_name=field_name)


def parse_robocash_pages(
    *,
    pages: list[str],
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)

    text = _normalized_text(pages)
    year_match = _YEAR_PATTERN.search(text)
    if year_match is None:
        raise P2PValidationError("missing required field in Robocash PDF: tax year marker")
    reporting_year = int(year_match.group("year"))

    interest = _extract_single_amount(
        r"^Earned\s+interest\s+(?:€|EUR)\s*(?P<amount>-?[\d.,]+)$",
        text,
        field_name="Earned interest",
    )
    bonus_income = _extract_single_amount(
        r"^Earned\s+income\s+from\s+bonuses\s+(?:€|EUR)\s*(?P<amount>-?[\d.,]+)$",
        text,
        field_name="Earned income from bonuses",
    )
    taxes_withheld = _extract_single_amount(
        r"^Taxes\s+withheld\s+(?:€|EUR)\s*(?P<amount>-?[\d.,]+)$",
        text,
        field_name="Taxes withheld",
    )

    metrics = RobocashSummaryMetrics(
        reporting_year=reporting_year,
        earned_interest_eur=interest,
        earned_bonus_income_eur=bonus_income,
        taxes_withheld_eur=taxes_withheld,
    )

    warnings: list[str] = []
    informational_messages: list[str] = []

    code_603_total = metrics.earned_interest_eur
    if code_603_total < ZERO:
        raise P2PValidationError(
            f"invalid Robocash code_603_total: negative result ({code_603_total})"
        )

    code_606_total = ZERO
    if metrics.earned_bonus_income_eur > ZERO:
        code_606_total = metrics.earned_bonus_income_eur
    elif metrics.earned_bonus_income_eur < ZERO:
        warnings.append(
            "Robocash Earned income from bonuses is negative and is not included in Appendix 6 code 606"
        )

    if metrics.taxes_withheld_eur > ZERO:
        informational_messages.append(
            "Robocash Taxes withheld is parsed but not mapped to structured tax-credit logic due missing country/payer context"
        )

    informative_rows = [
        InformativeRow("Reporting year", str(metrics.reporting_year)),
        InformativeRow("Earned interest (EUR)", metrics.earned_interest_eur),
        InformativeRow("Earned income from bonuses (EUR)", metrics.earned_bonus_income_eur),
        InformativeRow("Taxes withheld (EUR)", metrics.taxes_withheld_eur),
        InformativeRow("Secondary-market mode used", secondary_market_mode),
    ]

    return P2PAppendix6Result(
        platform=PLATFORM_NAME,
        tax_year=metrics.reporting_year,
        part1_rows=[],
        aggregate_code_603=code_603_total,
        aggregate_code_606=code_606_total,
        taxable_code_603=code_603_total,
        taxable_code_606=code_606_total,
        withheld_tax=ZERO,
        informative_rows=informative_rows,
        warnings=warnings,
        informational_messages=informational_messages,
    )


def parse_robocash_pdf(
    *,
    input_pdf: str | Path,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    pages = read_pdf_pages(input_pdf)
    return parse_robocash_pages(
        pages=pages,
        secondary_market_mode=secondary_market_mode,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
