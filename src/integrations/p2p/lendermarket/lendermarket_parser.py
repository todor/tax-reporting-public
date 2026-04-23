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
from .models import LendermarketSummaryMetrics

_PERIOD_PATTERN = re.compile(
    r"from\s+(?P<start>\d{2}\.\d{2}\.\d{4})\s*[-–—]\s*(?P<end>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)


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
        raise P2PValidationError(f"missing required field in Lendermarket PDF: {field_name}")
    return parse_decimal_text(match.group("amount"), field_name=field_name)


def parse_lendermarket_pages(
    *,
    pages: list[str],
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    validate_secondary_market_mode(mode=secondary_market_mode, allow_appendix_5=False)

    text = _normalized_text(pages)
    if "Tax statement for operations on Lendermarket" not in text:
        raise P2PValidationError("missing required Lendermarket report marker")

    period_match = _PERIOD_PATTERN.search(text)
    if period_match is None:
        raise P2PValidationError("missing required field in Lendermarket PDF: statement period")
    start = period_match.group("start")
    end = period_match.group("end")
    statement_period = f"{start} - {end}"
    reporting_year = int(end[-4:])

    payments_received = _extract_single_amount(
        r"^Payments\s+Received\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        text,
        field_name="Payments Received",
    )
    principal_amount = _extract_single_amount(
        r"^-\s*Principal\s+Amount\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        text,
        field_name="Principal Amount",
    )
    interest = _extract_single_amount(
        r"^-\s*Interest\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        text,
        field_name="Interest",
    )
    late_payment_fees = _extract_single_amount(
        r"^-\s*Late\s+Payment\s+Fees\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        text,
        field_name="Late Payment Fees",
    )
    pending_payment_interest = _extract_single_amount(
        r"^-\s*Pending\s+Payment\s+interest\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        text,
        field_name="Pending Payment interest",
    )
    campaign_rewards_and_bonuses = _extract_single_amount(
        r"^-\s*Campaign\s+rewards\s+and\s+bonuses\s+(?P<amount>-?[\d.,]+)\s+EUR$",
        text,
        field_name="Campaign rewards and bonuses",
    )

    metrics = LendermarketSummaryMetrics(
        reporting_year=reporting_year,
        statement_period=statement_period,
        payments_received_eur=payments_received,
        principal_amount_eur=principal_amount,
        interest_eur=interest,
        late_payment_fees_eur=late_payment_fees,
        pending_payment_interest_eur=pending_payment_interest,
        campaign_rewards_and_bonuses_eur=campaign_rewards_and_bonuses,
    )

    warnings: list[str] = []
    informational_messages: list[str] = []
    if metrics.pending_payment_interest_eur != ZERO:
        informational_messages.append(
            "Lendermarket Pending Payment interest is excluded from Appendix 6 totals "
            "until explicitly confirmed as taxable received income"
        )

    code_603_total = metrics.interest_eur + metrics.late_payment_fees_eur
    if code_603_total < ZERO:
        raise P2PValidationError(
            f"invalid Lendermarket code_603_total: negative result ({code_603_total})"
        )

    code_606_total = metrics.campaign_rewards_and_bonuses_eur
    if code_606_total < ZERO:
        warnings.append(
            "Lendermarket Campaign rewards and bonuses is negative and is not included in Appendix 6 code 606"
        )
        code_606_total = ZERO

    informative_rows = [
        InformativeRow("Reporting year", str(metrics.reporting_year)),
        InformativeRow("Statement period", metrics.statement_period),
        InformativeRow("Payments Received (EUR)", metrics.payments_received_eur),
        InformativeRow("Principal Amount (EUR)", metrics.principal_amount_eur),
        InformativeRow("Interest (EUR)", metrics.interest_eur),
        InformativeRow("Late Payment Fees (EUR)", metrics.late_payment_fees_eur),
        InformativeRow("Pending Payment interest (EUR)", metrics.pending_payment_interest_eur),
        InformativeRow("Campaign rewards and bonuses (EUR)", metrics.campaign_rewards_and_bonuses_eur),
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


def parse_lendermarket_pdf(
    *,
    input_pdf: str | Path,
    secondary_market_mode: str = SECONDARY_MARKET_MODE_APPENDIX_6,
) -> P2PAppendix6Result:
    pages = read_pdf_pages(input_pdf)
    return parse_lendermarket_pages(
        pages=pages,
        secondary_market_mode=secondary_market_mode,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
