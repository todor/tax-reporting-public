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
from .models import IuvoSummaryMetrics

_PERIOD_PATTERN = re.compile(
    r"period\s+(?P<start>\d{4}-\d{2}-\d{2})\s*[-–—]\s*(?P<end>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_AMOUNT_WITH_CCY_PATTERN = re.compile(r"(?P<amount>-?[\d.,]+)\s*(?:EUR|€)", re.IGNORECASE)


_TOP_LEVEL_LABELS = {
    "interest income",
    "late fees",
    "secondary market gains",
    "campaign rewards",
    "interest income iuvosave",
    "secondary market fees",
    "secondary market losses",
    "early withdraw fees iuvosave",
}


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_text_line(text)).strip().lower()


def _normalized_lines(pages: list[str]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = normalize_text_line(raw)
            if line != "":
                lines.append(line)
    return lines


def _extract_reporting_period(lines: list[str]) -> tuple[int, str]:
    text = "\n".join(lines)
    match = _PERIOD_PATTERN.search(text)
    if match is None:
        raise P2PValidationError("missing required field in Iuvo PDF: reporting period")
    start = match.group("start")
    end = match.group("end")
    return int(end[:4]), f"{start} - {end}"


def _amounts_in_line(line: str) -> list[Decimal]:
    matches = _AMOUNT_WITH_CCY_PATTERN.findall(line)
    return [parse_decimal_text(match, field_name="Iuvo amount") for match in matches]


def _is_block_boundary(line_norm: str, *, current_label: str) -> bool:
    if line_norm.startswith("total "):
        return True
    for top_label in _TOP_LEVEL_LABELS:
        if line_norm == top_label or line_norm.startswith(top_label + " "):
            return top_label != current_label
    if line_norm.startswith("yours sincerely"):
        return True
    return False


def _extract_category_amount(lines: list[str], *, label: str) -> Decimal:
    label_norm = _normalize_label(label)

    for idx, line in enumerate(lines):
        line_norm = _normalize_label(line)
        if not (line_norm == label_norm or line_norm.startswith(label_norm + " ")):
            continue

        inline_amounts = _amounts_in_line(line)
        if inline_amounts:
            return inline_amounts[0]

        block_amounts: list[Decimal] = []
        for probe in lines[idx + 1 :]:
            probe_norm = _normalize_label(probe)
            if _is_block_boundary(probe_norm, current_label=label_norm):
                break
            block_amounts.extend(_amounts_in_line(probe))

        if not block_amounts:
            raise P2PValidationError(f"missing numeric value for Iuvo field: {label}")
        return block_amounts[-1]

    raise P2PValidationError(f"missing required field in Iuvo PDF: {label}")


def parse_iuvo_pages(
    *,
    pages: list[str],
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)

    lines = _normalized_lines(pages)
    text = "\n".join(lines)
    text_lower = text.lower()
    if "profit statement" not in text_lower and "your income for the period" not in text_lower:
        raise P2PValidationError("missing required Iuvo report marker: Profit statement")

    reporting_year, statement_period = _extract_reporting_period(lines)

    metrics = IuvoSummaryMetrics(
        reporting_year=reporting_year,
        statement_period=statement_period,
        interest_income_eur=_extract_category_amount(lines, label="Interest income"),
        late_fees_eur=_extract_category_amount(lines, label="Late fees"),
        secondary_market_gains_eur=_extract_category_amount(lines, label="Secondary market gains"),
        campaign_rewards_eur=_extract_category_amount(lines, label="Campaign rewards"),
        interest_income_iuvosave_eur=_extract_category_amount(lines, label="Interest income iuvoSAVE"),
        secondary_market_fees_eur=_extract_category_amount(lines, label="Secondary market fees"),
        secondary_market_losses_eur=_extract_category_amount(lines, label="Secondary market losses"),
        early_withdraw_fees_iuvosave_eur=_extract_category_amount(lines, label="Early withdraw fees iuvoSAVE"),
    )

    warnings: list[str] = []
    informational_messages: list[str] = []

    secondary_losses = metrics.secondary_market_losses_eur
    secondary_fees = metrics.secondary_market_fees_eur
    if secondary_losses > ZERO:
        secondary_losses = -secondary_losses
        warnings.append(
            "Iuvo secondary market losses was positive in the report and was normalized as a negative value"
        )
    if secondary_fees > ZERO:
        secondary_fees = -secondary_fees
        warnings.append(
            "Iuvo secondary market fees was positive in the report and was normalized as a negative value"
        )

    secondary_market_aggregate = metrics.secondary_market_gains_eur + secondary_losses + secondary_fees

    code_603_total = (
        metrics.interest_income_eur
        + metrics.late_fees_eur
        + metrics.interest_income_iuvosave_eur
    )
    if code_603_total < ZERO:
        raise P2PValidationError(
            f"invalid Iuvo code_603_total: negative result ({code_603_total})"
        )

    code_606_total = ZERO
    if metrics.campaign_rewards_eur > ZERO:
        code_606_total += metrics.campaign_rewards_eur
    elif metrics.campaign_rewards_eur < ZERO:
        warnings.append("Iuvo Campaign rewards is negative and is not included in Appendix 6 code 606")

    if secondary_market_aggregate > ZERO:
        code_606_total += secondary_market_aggregate
    else:
        informational_messages.append(
            "Iuvo secondary-market aggregate is <= 0 and is omitted from Appendix 6 code 606"
        )

    if metrics.early_withdraw_fees_iuvosave_eur != ZERO:
        informational_messages.append(
            "Iuvo Early withdraw fees iuvoSAVE is informational only and is not mapped to Appendix 6 totals"
        )

    informative_rows = [
        InformativeRow("Reporting year", str(metrics.reporting_year)),
        InformativeRow("Statement period", metrics.statement_period),
        InformativeRow("Interest income (EUR)", metrics.interest_income_eur),
        InformativeRow("Late fees (EUR)", metrics.late_fees_eur),
        InformativeRow("Secondary market gains (EUR)", metrics.secondary_market_gains_eur),
        InformativeRow("Campaign rewards (EUR)", metrics.campaign_rewards_eur),
        InformativeRow("Interest income iuvoSAVE (EUR)", metrics.interest_income_iuvosave_eur),
        InformativeRow("Secondary market fees (EUR)", metrics.secondary_market_fees_eur),
        InformativeRow("Secondary market losses (EUR)", metrics.secondary_market_losses_eur),
        InformativeRow("Secondary market aggregate used for code 606 (EUR)", secondary_market_aggregate),
        InformativeRow("Early withdraw fees iuvoSAVE (EUR)", metrics.early_withdraw_fees_iuvosave_eur),
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


def parse_iuvo_pdf(
    *,
    input_pdf: str | Path,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    pages = read_pdf_pages(input_pdf)
    return parse_iuvo_pages(
        pages=pages,
        secondary_market_mode=secondary_market_mode,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
