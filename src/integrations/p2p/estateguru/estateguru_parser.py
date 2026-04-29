from __future__ import annotations

import re
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
from .models import EstateguruSummaryMetrics

_PERIOD_PATTERN = re.compile(
    r"Selected\s+period\s+(?P<start>\d{2}\.\d{2}\.\d{4})\s*[-–—]\s*(?P<end>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
_NUMERIC_PATTERN = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _normalized_lines(pages: list[str]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = normalize_text_line(raw)
            if line != "":
                lines.append(line)
    return lines


def _extract_period(lines: list[str]) -> tuple[int, str]:
    text = "\n".join(lines)
    match = _PERIOD_PATTERN.search(text)
    if match is None:
        raise P2PValidationError("missing required field in Estateguru PDF: Selected period")
    start = match.group("start")
    end = match.group("end")
    try:
        year = int(end[-4:])
    except ValueError as exc:  # pragma: no cover - guarded by regex
        raise P2PValidationError("invalid Estateguru Selected period year") from exc
    return year, f"{start} - {end}"


def _extract_totals_line(lines: list[str]) -> str:
    candidates = [
        line
        for line in lines
        if line.lower().startswith("total")
        and ("€" in line or "eur" in line.lower())
    ]
    if not candidates:
        raise P2PValidationError("missing required field in Estateguru PDF: totals row")
    if len(candidates) > 1:
        raise P2PValidationError("ambiguous Estateguru totals row: multiple candidates detected")
    return candidates[0]


def _parse_totals_row(line: str) -> EstateguruSummaryMetrics:
    numeric_tokens = _NUMERIC_PATTERN.findall(line)
    numbers = [
        parse_decimal_text(token, field_name="Estateguru totals row")
        for token in numeric_tokens
    ]
    if len(numbers) < 9:
        raise P2PValidationError(
            "invalid Estateguru totals row: expected at least 9 numeric values "
            f"(got {len(numbers)})"
        )

    interest = numbers[0]
    bonus_borrower = numbers[1]
    penalty = numbers[2]
    indemnity = numbers[3]
    bonus_eg = numbers[4]
    secondary = numbers[5]
    sale_fee = numbers[6]
    aum_fee = numbers[7]
    total = numbers[8]

    return EstateguruSummaryMetrics(
        reporting_year=0,
        statement_period="",
        interest_eur=interest,
        bonus_borrower_eur=bonus_borrower,
        penalty_eur=penalty,
        indemnity_eur=indemnity,
        bonus_eg_eur=bonus_eg,
        secondary_market_profit_loss_eur=secondary,
        sale_fee_eur=sale_fee,
        aum_fee_eur=aum_fee,
        total_eur=total,
    )


def parse_estateguru_pages(
    *,
    pages: list[str],
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)

    lines = _normalized_lines(pages)
    year, statement_period = _extract_period(lines)
    totals_line = _extract_totals_line(lines)
    metrics = _parse_totals_row(totals_line)
    metrics.reporting_year = year
    metrics.statement_period = statement_period

    warnings: list[str] = []
    informational_messages: list[str] = []

    code_603_total = metrics.interest_eur + metrics.penalty_eur + metrics.indemnity_eur
    if code_603_total < ZERO:
        raise P2PValidationError(
            "invalid Estateguru code_603_total: negative result "
            f"({code_603_total})"
        )

    code_606_total = ZERO
    if metrics.bonus_borrower_eur > ZERO:
        code_606_total += metrics.bonus_borrower_eur
    elif metrics.bonus_borrower_eur < ZERO:
        warnings.append("Estateguru Bonus (Borrower) is negative and is not included in Appendix 6 code 606")

    if metrics.bonus_eg_eur > ZERO:
        code_606_total += metrics.bonus_eg_eur
    elif metrics.bonus_eg_eur < ZERO:
        warnings.append("Estateguru Bonus (EG) is negative and is not included in Appendix 6 code 606")

    if metrics.secondary_market_profit_loss_eur > ZERO:
        code_606_total += metrics.secondary_market_profit_loss_eur
    else:
        informational_messages.append(
            "Estateguru secondary-market aggregate is <= 0 and is omitted from Appendix 6 code 606"
        )

    if metrics.sale_fee_eur != ZERO or metrics.aum_fee_eur != ZERO:
        informational_messages.append(
            "Estateguru Sale fee and AUM fee are informational only and are not mapped to Appendix 6 totals"
        )

    informative_rows = [
        InformativeRow("Reporting year", str(metrics.reporting_year)),
        InformativeRow("Statement period", metrics.statement_period),
        InformativeRow("Interest (EUR)", metrics.interest_eur),
        InformativeRow("Bonus (Borrower) (EUR)", metrics.bonus_borrower_eur),
        InformativeRow("Penalty (EUR)", metrics.penalty_eur),
        InformativeRow("Indemnity (EUR)", metrics.indemnity_eur),
        InformativeRow("Bonus (EG) (EUR)", metrics.bonus_eg_eur),
        InformativeRow("Secondary market profit/loss (EUR)", metrics.secondary_market_profit_loss_eur),
        InformativeRow("Sale fee (EUR)", metrics.sale_fee_eur),
        InformativeRow("AUM fee (EUR)", metrics.aum_fee_eur),
        InformativeRow("Total (EUR)", metrics.total_eur),
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


def parse_estateguru_pdf(
    *,
    input_pdf: str | Path,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    pages = read_pdf_pages(input_pdf)
    return parse_estateguru_pages(
        pages=pages,
        secondary_market_mode=secondary_market_mode,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
