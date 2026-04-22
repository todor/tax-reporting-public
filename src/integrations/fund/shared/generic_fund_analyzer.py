from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .runtime import FundEurUnitRateProvider

from .fund_ir_models import (
    FundAnalysisResult,
    FundAnalysisSummary,
    FundCurrencyState,
    FundEnrichedRow,
    FundIrRow,
    GenericFundAnalyzerError,
    ZERO,
    validate_ir_row,
)

TOLERANCE = Decimal("0.000000000001")


def _context(row: FundIrRow) -> str:
    src = f"row {row.source_row_number}" if row.source_row_number is not None else "row ?"
    return f"{src} op={row.operation_id} type={row.transaction_type}"


def _round_to_zero(value: Decimal) -> Decimal:
    if abs(value) <= TOLERANCE:
        return ZERO
    return value


def _currency_state(
    state_by_currency: dict[str, FundCurrencyState],
    *,
    currency: str,
    currency_type: str,
) -> FundCurrencyState:
    existing = state_by_currency.get(currency)
    if existing is None:
        existing = FundCurrencyState(currency=currency, currency_type=currency_type)
        state_by_currency[currency] = existing
        return existing
    if existing.currency_type != currency_type:
        raise GenericFundAnalyzerError(
            f"currency type mismatch for {currency}: existing={existing.currency_type} incoming={currency_type}"
        )
    return existing


def _eur_unit_rate(
    *,
    currency: str,
    currency_type: str,
    timestamp,
    eur_unit_rate_provider: FundEurUnitRateProvider,
    ctx: str,
) -> Decimal:
    normalized = currency.strip().upper()
    if normalized == "":
        raise GenericFundAnalyzerError(f"{ctx}: missing currency")
    try:
        rate = eur_unit_rate_provider(normalized, currency_type, timestamp)
    except Exception as exc:  # noqa: BLE001
        raise GenericFundAnalyzerError(
            f"{ctx}: FX conversion failed "
            f"(currency={normalized}, currency_type={currency_type}, timestamp={timestamp.isoformat()})"
        ) from exc

    if rate <= ZERO:
        raise GenericFundAnalyzerError(f"{ctx}: invalid EUR rate for {normalized}: {rate}")
    return rate


def _apply_realized_disposal(
    summary: FundAnalysisSummary,
    *,
    sale_price_eur: Decimal,
    purchase_price_eur: Decimal,
) -> None:
    net = sale_price_eur - purchase_price_eur
    summary.appendix_5.sale_price_eur += sale_price_eur
    summary.appendix_5.purchase_price_eur += purchase_price_eur
    if net > ZERO:
        summary.appendix_5.wins_eur += net
    elif net < ZERO:
        summary.appendix_5.losses_eur += -net
    summary.appendix_5.rows += 1


def _set_disposal_fields(row: FundEnrichedRow, *, purchase_price_eur: Decimal, sale_price_eur: Decimal) -> None:
    net = sale_price_eur - purchase_price_eur
    if net == ZERO:
        return

    row.purchase_price_eur = purchase_price_eur
    row.sale_price_eur = sale_price_eur
    row.net_profit_eur = net
    if net > ZERO:
        row.profit_win_eur = net
        row.profit_loss_eur = ZERO
    else:
        row.profit_win_eur = ZERO
        row.profit_loss_eur = -net


def analyze_fund_ir_rows(
    *,
    ir_rows: list[FundIrRow],
    tax_year: int,
    summary: FundAnalysisSummary,
    eur_unit_rate_provider: FundEurUnitRateProvider,
    opening_state_by_currency: dict[str, FundCurrencyState] | None = None,
) -> FundAnalysisResult:
    if tax_year < 2009 or tax_year > 2100:
        raise GenericFundAnalyzerError(f"invalid tax year: {tax_year}")

    for ir_row in ir_rows:
        validate_ir_row(ir_row)

    state_by_currency: dict[str, FundCurrencyState] = {}
    gross_deposit_eur_by_currency: dict[str, Decimal] = {}
    if opening_state_by_currency:
        for currency, state in opening_state_by_currency.items():
            state_by_currency[currency] = replace(state)
            gross_deposit_eur_by_currency[currency] = state.eur_deposit_balance

    enriched_by_index: dict[int, FundEnrichedRow] = {idx: FundEnrichedRow(ir_row=row) for idx, row in enumerate(ir_rows)}

    sorted_rows = sorted(
        enumerate(ir_rows),
        key=lambda item: (
            item[1].timestamp,
            item[1].sort_index,
            item[1].source_row_number if item[1].source_row_number is not None else 0,
            item[0],
        ),
    )

    year_end_snapshot_captured = False
    year_end_state_by_currency: dict[str, FundCurrencyState] = {}

    for original_index, row in sorted_rows:
        include_in_appendix = row.timestamp.year == tax_year
        enriched = enriched_by_index[original_index]
        ctx = _context(row)

        if not year_end_snapshot_captured and row.timestamp.year > tax_year:
            year_end_state_by_currency = {
                currency: replace(state) for currency, state in state_by_currency.items()
            }
            year_end_snapshot_captured = True

        state = _currency_state(
            state_by_currency,
            currency=row.currency,
            currency_type=row.currency_type,
        )
        if row.currency not in gross_deposit_eur_by_currency:
            gross_deposit_eur_by_currency[row.currency] = ZERO

        eur_unit_rate = _eur_unit_rate(
            currency=row.currency,
            currency_type=row.currency_type,
            timestamp=row.timestamp,
            eur_unit_rate_provider=eur_unit_rate_provider,
            ctx=ctx,
        )
        enriched.amount_eur = row.amount * eur_unit_rate

        if row.transaction_type == "deposit":
            deposit_eur = abs(row.amount) * eur_unit_rate
            state.native_deposit_balance += row.amount
            state.eur_deposit_balance += deposit_eur
            gross_deposit_eur_by_currency[row.currency] += deposit_eur

        elif row.transaction_type == "profit":
            state.native_profit_balance += row.amount
            if state.native_total_balance < -TOLERANCE:
                raise GenericFundAnalyzerError(
                    f"{ctx}: profit update makes total native balance negative for {row.currency}; "
                    f"total={state.native_total_balance}"
                )

        elif row.transaction_type == "withdraw":
            withdrawal_native = row.amount
            if withdrawal_native <= ZERO:
                raise GenericFundAnalyzerError(f"{ctx}: withdrawal amount must be positive")

            total_native = state.native_total_balance
            if total_native <= ZERO:
                raise GenericFundAnalyzerError(
                    f"{ctx}: withdrawal requires positive balance for {row.currency}; total_native={total_native}"
                )
            if withdrawal_native > total_native + TOLERANCE:
                raise GenericFundAnalyzerError(
                    f"{ctx}: withdrawal exceeds current total balance for {row.currency}; "
                    f"requested={withdrawal_native} available={total_native}"
                )

            if abs(withdrawal_native - total_native) <= TOLERANCE:
                withdrawal_native = total_native

            ratio = withdrawal_native / total_native
            realized_deposit_native = ratio * state.native_deposit_balance
            realized_profit_native = ratio * state.native_profit_balance
            purchase_price_eur = ratio * state.eur_deposit_balance
            sale_price_eur = abs(withdrawal_native) * eur_unit_rate

            state.native_deposit_balance -= realized_deposit_native
            state.eur_deposit_balance -= purchase_price_eur
            state.native_profit_balance -= realized_profit_native

            state.native_deposit_balance = _round_to_zero(state.native_deposit_balance)
            state.eur_deposit_balance = _round_to_zero(state.eur_deposit_balance)
            state.native_profit_balance = _round_to_zero(state.native_profit_balance)

            if include_in_appendix:
                _apply_realized_disposal(
                    summary,
                    sale_price_eur=sale_price_eur,
                    purchase_price_eur=purchase_price_eur,
                )

            _set_disposal_fields(
                enriched,
                purchase_price_eur=purchase_price_eur,
                sale_price_eur=sale_price_eur,
            )

        else:
            raise GenericFundAnalyzerError(f"{ctx}: unsupported IR transaction type={row.transaction_type!r}")

        enriched.balance_native = state.native_total_balance
        enriched.balance_eur = state.native_total_balance * eur_unit_rate
        enriched.deposit_to_date_eur = gross_deposit_eur_by_currency[row.currency]

    summary.state_by_currency = {currency: replace(state) for currency, state in state_by_currency.items()}
    if not year_end_snapshot_captured:
        year_end_state_by_currency = {
            currency: replace(state) for currency, state in state_by_currency.items()
        }

    enriched_rows = [enriched_by_index[idx] for idx in range(len(ir_rows))]
    return FundAnalysisResult(
        summary=summary,
        enriched_rows=enriched_rows,
        year_end_state_by_currency=year_end_state_by_currency,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
