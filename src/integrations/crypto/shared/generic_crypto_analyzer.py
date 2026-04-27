from __future__ import annotations

from decimal import Decimal

from .crypto_ir_models import (
    CryptoIrRow,
    GenericCryptoAnalyzerError,
    IrAnalysisResult,
    IrAnalysisSummary,
    IrEnrichedRow,
    SEND_REVIEW_STATUSES,
    RECEIVE_REVIEW_STATUSES,
    ZERO,
    validate_ir_row,
)
from .generic_ledger import DECIMAL_EIGHT, GenericAverageCostLedger


def _apply_disposal(summary: IrAnalysisSummary, *, sale_price_eur: Decimal, purchase_price_eur: Decimal) -> None:
    net = sale_price_eur - purchase_price_eur
    summary.appendix_5.sale_price_eur += sale_price_eur
    summary.appendix_5.purchase_price_eur += purchase_price_eur
    if net > ZERO:
        summary.appendix_5.wins_eur += net
    elif net < ZERO:
        summary.appendix_5.losses_eur += -net
    summary.appendix_5.rows += 1


def _set_disposal_fields(row: IrEnrichedRow, *, purchase_price_eur: Decimal, sale_price_eur: Decimal) -> None:
    net = sale_price_eur - purchase_price_eur
    if net == ZERO:
        return
    row.purchase_price_eur = purchase_price_eur
    row.sale_price_eur = sale_price_eur
    row.net_profit_eur = net
    if net > ZERO:
        row.profit_win_eur = net
        row.profit_loss_eur = ZERO
    elif net < ZERO:
        row.profit_win_eur = ZERO
        row.profit_loss_eur = -net
    else:
        row.profit_win_eur = ZERO
        row.profit_loss_eur = ZERO


def _apply_grouped_disposals(
    summary: IrAnalysisSummary,
    *,
    grouped: dict[str, tuple[Decimal, Decimal]],
) -> None:
    for _, (sale_price_eur, purchase_price_eur) in grouped.items():
        _apply_disposal(
            summary,
            sale_price_eur=sale_price_eur,
            purchase_price_eur=purchase_price_eur,
        )


def _context(row: CryptoIrRow) -> str:
    src = f"row {row.source_row_number}" if row.source_row_number is not None else "row ?"
    return f"{src} op={row.operation_id} tx={row.transaction_type}"


def analyze_ir_rows(
    *,
    ir_rows: list[CryptoIrRow],
    tax_year: int,
    summary: IrAnalysisSummary,
    opening_holdings: dict[str, tuple[Decimal, Decimal]] | None = None,
) -> IrAnalysisResult:
    if tax_year < 2009 or tax_year > 2100:
        raise GenericCryptoAnalyzerError(f"invalid tax year: {tax_year}")

    for ir_row in ir_rows:
        validate_ir_row(ir_row)

    ledger = GenericAverageCostLedger()
    if opening_holdings:
        for asset, (quantity, total_cost_eur) in opening_holdings.items():
            ledger.seed(asset, quantity=quantity, total_cost_eur=total_cost_eur, context="opening state")

    enriched_by_index: dict[int, IrEnrichedRow] = {
        idx: IrEnrichedRow(ir_row=row) for idx, row in enumerate(ir_rows)
    }

    sorted_rows = sorted(
        enumerate(ir_rows),
        key=lambda item: (
            item[1].timestamp,
            item[1].source_row_number if item[1].source_row_number is not None else 0,
            item[1].sort_index,
            item[0],
        ),
    )

    year_end_snapshot_captured = False
    year_end_holdings_by_asset: dict[str, tuple[Decimal, Decimal]] = {}
    grouped_disposals_by_operation: dict[str, tuple[Decimal, Decimal]] = {}

    for original_index, row in sorted_rows:
        ctx = _context(row)
        include_in_appendix = row.timestamp.year == tax_year
        enriched = enriched_by_index[original_index]

        if not year_end_snapshot_captured and row.timestamp.year > tax_year:
            holdings_before_row = ledger.snapshot()
            year_end_holdings_by_asset = {
                key: (item.quantity, item.total_cost_eur) for key, item in holdings_before_row.items()
            }
            year_end_snapshot_captured = True

        tx = row.transaction_type

        if tx == "Buy":
            if row.proceeds_eur is None:
                raise GenericCryptoAnalyzerError(f"{ctx}: missing proceeds for Buy")
            buy_result = ledger.buy(
                row.asset,
                quantity=abs(row.quantity),
                execution_value_eur=abs(row.proceeds_eur),
                context=ctx,
            )
            if buy_result.has_closing_leg:
                if include_in_appendix:
                    previous = grouped_disposals_by_operation.get(row.operation_id, (ZERO, ZERO))
                    grouped_disposals_by_operation[row.operation_id] = (
                        previous[0] + buy_result.closing_sale_price_eur,
                        previous[1] + buy_result.closing_purchase_price_eur,
                    )
                _set_disposal_fields(
                    enriched,
                    purchase_price_eur=buy_result.closing_purchase_price_eur,
                    sale_price_eur=buy_result.closing_sale_price_eur,
                )

        elif tx == "Sell":
            if row.proceeds_eur is None:
                raise GenericCryptoAnalyzerError(f"{ctx}: missing proceeds for Sell")
            sell_result = ledger.sell(
                row.asset,
                quantity=abs(row.quantity),
                execution_value_eur=abs(row.proceeds_eur),
                context=ctx,
            )
            if sell_result.has_closing_leg:
                if include_in_appendix:
                    previous = grouped_disposals_by_operation.get(row.operation_id, (ZERO, ZERO))
                    grouped_disposals_by_operation[row.operation_id] = (
                        previous[0] + sell_result.closing_sale_price_eur,
                        previous[1] + sell_result.closing_purchase_price_eur,
                    )
                _set_disposal_fields(
                    enriched,
                    purchase_price_eur=sell_result.closing_purchase_price_eur,
                    sale_price_eur=sell_result.closing_sale_price_eur,
                )

        elif tx == "Withdraw":
            if row.asset_type == "fiat":
                summary.ignored_fiat_deposit_withdraw_rows += 1
            else:
                if (row.source_transaction_type or "").strip().upper() == "SEND":
                    current_quantity = ledger.quantity(row.asset)
                    requested_quantity = abs(row.quantity)
                    if current_quantity <= ZERO:
                        raise GenericCryptoAnalyzerError(
                            f"row {row.source_row_number}: Send requires existing long holdings; "
                            f"asset={row.asset} available_qty={current_quantity}"
                        )
                    if requested_quantity > current_quantity + DECIMAL_EIGHT:
                        raise GenericCryptoAnalyzerError(
                            f"row {row.source_row_number}: insufficient holdings for Send; "
                            f"asset={row.asset} requested_qty={requested_quantity} available_qty={current_quantity}"
                        )

                ledger.decrease_without_realization(
                    row.asset,
                    quantity=abs(row.quantity),
                    context=ctx,
                )

                if (row.source_transaction_type or "").strip().upper() == "SEND":
                    review_status = (row.review_status or "").strip().upper()
                    if review_status == "TAXABLE":
                        if include_in_appendix:
                            summary.taxable_send_rows += 1
                    elif review_status == "NON-TAXABLE":
                        summary.non_taxable_send_rows += 1
                    else:
                        summary.invalid_send_review_rows += 1
                        summary.unknown_send_review_statuses.add(review_status or "EMPTY")
                        summary.warnings.append(
                            f"row {row.source_row_number}: Send without valid Review Status; accepted values: "
                            f"{sorted(SEND_REVIEW_STATUSES)}"
                        )

        elif tx == "Deposit":
            if row.asset_type == "fiat":
                summary.ignored_fiat_deposit_withdraw_rows += 1
            else:
                source_tx = (row.source_transaction_type or "").strip().upper()
                review_status = (row.review_status or "").strip().replace("-", "_").upper()
                if review_status == "NON_TAXABLE":
                    execution_value = row.proceeds_eur or ZERO
                    ledger.increase_without_realization(
                        row.asset,
                        quantity=abs(row.quantity),
                        execution_value_eur=abs(execution_value),
                        context=ctx,
                    )
                    position_after = ledger.position(row.asset)
                    if position_after is None:
                        enriched.position_quantity_after = ZERO
                        enriched.total_cost_after_eur = ZERO
                        enriched.average_price_after_eur = ZERO
                    else:
                        enriched.position_quantity_after = position_after.quantity
                        enriched.total_cost_after_eur = position_after.total_cost_eur
                        enriched.average_price_after_eur = position_after.average_price_eur
                    continue
                if source_tx == "RECEIVE":
                    if review_status not in RECEIVE_REVIEW_STATUSES:
                        raise GenericCryptoAnalyzerError(
                            f"row {row.source_row_number}: invalid Review Status for Receive={row.review_status!r}; "
                            f"accepted values: {sorted(RECEIVE_REVIEW_STATUSES)}"
                        )
                    if row.cost_basis_eur is None:
                        raise GenericCryptoAnalyzerError(
                            f"row {row.source_row_number}: missing Cost Basis for Receive"
                        )
                    execution_value = row.cost_basis_eur
                else:
                    execution_value = row.cost_basis_eur if row.cost_basis_eur is not None else (row.proceeds_eur or ZERO)

                buy_result = ledger.buy(
                    row.asset,
                    quantity=abs(row.quantity),
                    execution_value_eur=abs(execution_value),
                    context=ctx,
                )
                if buy_result.has_closing_leg:
                    if include_in_appendix:
                        previous = grouped_disposals_by_operation.get(row.operation_id, (ZERO, ZERO))
                        grouped_disposals_by_operation[row.operation_id] = (
                            previous[0] + buy_result.closing_sale_price_eur,
                            previous[1] + buy_result.closing_purchase_price_eur,
                        )
                    _set_disposal_fields(
                        enriched,
                        purchase_price_eur=buy_result.closing_purchase_price_eur,
                        sale_price_eur=buy_result.closing_sale_price_eur,
                    )

        elif tx == "Earn":
            execution_value = row.cost_basis_eur if row.cost_basis_eur is not None else (row.proceeds_eur or ZERO)
            buy_result = ledger.buy(
                row.asset,
                quantity=abs(row.quantity),
                execution_value_eur=abs(execution_value),
                context=ctx,
            )
            if buy_result.has_closing_leg:
                if include_in_appendix:
                    previous = grouped_disposals_by_operation.get(row.operation_id, (ZERO, ZERO))
                    grouped_disposals_by_operation[row.operation_id] = (
                        previous[0] + buy_result.closing_sale_price_eur,
                        previous[1] + buy_result.closing_purchase_price_eur,
                    )
                _set_disposal_fields(
                    enriched,
                    purchase_price_eur=buy_result.closing_purchase_price_eur,
                    sale_price_eur=buy_result.closing_sale_price_eur,
                )

        else:
            raise GenericCryptoAnalyzerError(f"{ctx}: unsupported IR transaction type={tx!r}")

        position_after = ledger.position(row.asset)
        if position_after is None:
            enriched.position_quantity_after = ZERO
            enriched.total_cost_after_eur = ZERO
            enriched.average_price_after_eur = ZERO
        else:
            enriched.position_quantity_after = position_after.quantity
            enriched.total_cost_after_eur = position_after.total_cost_eur
            enriched.average_price_after_eur = position_after.average_price_eur

    _apply_grouped_disposals(summary, grouped=grouped_disposals_by_operation)

    summary.holdings_by_asset = ledger.snapshot()
    if not year_end_snapshot_captured:
        year_end_holdings_by_asset = {
            key: (item.quantity, item.total_cost_eur) for key, item in summary.holdings_by_asset.items()
        }

    enriched_rows = [enriched_by_index[idx] for idx in range(len(ir_rows))]
    return IrAnalysisResult(
        summary=summary,
        enriched_rows=enriched_rows,
        year_end_holdings_by_asset=year_end_holdings_by_asset,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
