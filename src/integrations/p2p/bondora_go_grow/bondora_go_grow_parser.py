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
from .models import BondoraGoGrowSummaryMetrics

_PERIOD_PATTERN = re.compile(
    r"Go\s*&\s*Grow\s+Tax\s+Report\s*[–-]\s*(?P<start>\d{2}/\d{2}/\d{4})\s*[-–—]\s*(?P<end>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)

_BONUS_LABEL_PATTERN = re.compile(
    r"^bonus\s*income\s*received\s*on\s*bondora\s*account\*?$",
    re.IGNORECASE,
)

_AMOUNT_PATTERN = re.compile(
    r"(?:€|EUR)\s*(?P<amount>-?[\d.,]+)|(?P<amount2>-?[\d.,]+)\s*(?:€|EUR)",
    re.IGNORECASE,
)


def _normalized_lines(pages: list[str]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = normalize_text_line(raw)
            if line != "":
                lines.append(line)
    return lines


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_text_line(text)).strip().lower()


def _extract_euro_amounts(line: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in _AMOUNT_PATTERN.finditer(line):
        token = match.group("amount") or match.group("amount2")
        if token is None:
            continue
        values.append(parse_decimal_text(token, field_name="Bondora amount"))
    return values


def _extract_reporting_period(lines: list[str]) -> tuple[int, str]:
    text = "\n".join(lines)
    match = _PERIOD_PATTERN.search(text)
    if match is None:
        raise P2PValidationError("missing required field in Bondora Go & Grow PDF: report period")
    start = match.group("start")
    end = match.group("end")
    return int(end[-4:]), f"{start} - {end}"


def _extract_portfolio_row_amounts(lines: list[str]) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    row_idx = None
    for idx, line in enumerate(lines):
        if _normalize_label(line) == "go & grow":
            row_idx = idx
            break
    if row_idx is None:
        raise P2PValidationError("missing required Bondora row: Go & Grow")

    amounts: list[Decimal] = []
    for probe in lines[row_idx + 1 :]:
        probe_norm = _normalize_label(probe)
        if probe_norm.startswith("total"):
            break
        amounts.extend(_extract_euro_amounts(probe))

    if len(amounts) < 6:
        raise P2PValidationError(
            "invalid Bondora Go & Grow row: expected at least 6 portfolio amounts"
        )

    return tuple(amounts[:6])  # type: ignore[return-value]


def _extract_bonus_income(lines: list[str]) -> Decimal:
    for idx, line in enumerate(lines):
        if _BONUS_LABEL_PATTERN.match(_normalize_label(line)) is None:
            continue

        inline = _extract_euro_amounts(line)
        if inline:
            return inline[0]

        for probe in lines[idx + 1 :]:
            probe_norm = _normalize_label(probe)
            if probe_norm.startswith("grand total"):
                break
            probe_amounts = _extract_euro_amounts(probe)
            if probe_amounts:
                return probe_amounts[0]

        raise P2PValidationError("missing numeric value for Bondora field: Bonus income")

    raise P2PValidationError(
        "missing required field in Bondora Go & Grow PDF: Bonus income received on Bondora account"
    )


def parse_bondora_go_grow_pages(
    *,
    pages: list[str],
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)

    lines = _normalized_lines(pages)
    text = "\n".join(lines)
    if "go & grow tax report" not in text.lower():
        raise P2PValidationError("missing required Bondora report marker: Go & Grow Tax Report")

    reporting_year, statement_period = _extract_reporting_period(lines)
    (
        capital_invested,
        capital_withdrawn,
        withdrawal_fees,
        profit_realized,
        interest_accrued,
        net_profit,
    ) = _extract_portfolio_row_amounts(lines)
    bonus_income = _extract_bonus_income(lines)

    metrics = BondoraGoGrowSummaryMetrics(
        reporting_year=reporting_year,
        statement_period=statement_period,
        capital_invested_eur=capital_invested,
        capital_withdrawn_eur=capital_withdrawn,
        withdrawal_fees_eur=withdrawal_fees,
        profit_realized_eur=profit_realized,
        interest_accrued_eur=interest_accrued,
        net_profit_eur=net_profit,
        bonus_income_eur=bonus_income,
    )

    warnings: list[str] = []
    informational_messages: list[str] = []
    if (
        metrics.capital_invested_eur != ZERO
        or metrics.capital_withdrawn_eur != ZERO
        or metrics.withdrawal_fees_eur != ZERO
        or metrics.profit_realized_eur != ZERO
        or metrics.net_profit_eur != ZERO
    ):
        informational_messages.append(
            "Bondora Capital/Profit/Net portfolio fields are informational only and are not mapped to Appendix 6 totals"
        )

    code_603_total = metrics.interest_accrued_eur
    if code_603_total < ZERO:
        raise P2PValidationError(
            f"invalid Bondora code_603_total: negative result ({code_603_total})"
        )

    code_606_total = metrics.bonus_income_eur if metrics.bonus_income_eur > ZERO else ZERO

    informative_rows = [
        InformativeRow("Reporting year", str(metrics.reporting_year)),
        InformativeRow("Statement period", metrics.statement_period),
        InformativeRow("Capital invested (EUR)", metrics.capital_invested_eur),
        InformativeRow("Capital withdrawn (EUR)", metrics.capital_withdrawn_eur),
        InformativeRow("Withdrawal fees (EUR)", metrics.withdrawal_fees_eur),
        InformativeRow("Profit realized (EUR)", metrics.profit_realized_eur),
        InformativeRow("Interest Accrued (EUR)", metrics.interest_accrued_eur),
        InformativeRow("Net profit (EUR)", metrics.net_profit_eur),
        InformativeRow("Bonus income received on Bondora account (EUR)", metrics.bonus_income_eur),
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


def parse_bondora_go_grow_pdf(
    *,
    input_pdf: str | Path,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    pages = read_pdf_pages(input_pdf)
    return parse_bondora_go_grow_pages(
        pages=pages,
        secondary_market_mode=secondary_market_mode,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
