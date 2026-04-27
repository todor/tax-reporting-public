# Shared Fund Engine (`integrations.fund.shared`)

This package is the single source of truth for fund-style accounting and tax math used by fund integrations.

Provider-specific modules (for example Finexify) must only:

- parse raw provider files
- map rows to fund IR (`FundIrRow`)
- call the shared analyzer/output helpers

They must not duplicate state, realization, or tax-summary logic.

## Modules

- `fund_ir_models.py`: IR dataclasses, validation, summary/result/state models
- `generic_fund_analyzer.py`: chronological IR processing and withdrawal realization
- `fund_outputs.py`: enriched IR CSV, declaration TXT, carry-forward state JSON

## IR Schema (`FundIrRow`)

Core fields:

- `timestamp`
- `operation_id`
- `transaction_type`: `deposit | profit | withdraw`
- `currency`
- `currency_type`: `fiat | crypto`
- `amount` (positive for `deposit` and `withdraw`; signed delta for `profit`)

Audit/source fields:

- `source_exchange`
- `source_row_number`
- `source_transaction_type`

## State Model

Per currency the analyzer stores:

- `native_deposit_balance`
- `eur_deposit_balance`
- `native_profit_balance`

And derives:

- `native_total_balance = native_deposit_balance + native_profit_balance`

## Tax Realization Rules

- `deposit`: non-taxable, increases deposit balances
- `profit`: non-taxable, updates native profit balance only
- `withdraw`: taxable realization point

For withdrawal `W_native` from current balances `D_native`, `P_native`, `T_native=D_native+P_native`:

- `X = W_native / T_native`
- `realized_deposit_native = X * D_native`
- `realized_profit_native = X * P_native`
- `purchase_price_eur = X * eur_deposit_balance`
- `sale_price_eur = EUR value of W_native at withdrawal timestamp`
- `net_profit_eur = sale_price_eur - purchase_price_eur`

Balances are reduced proportionally by these realized components so year-end state can be carried forward.

## FX Timing

- Deposit EUR basis: converted at deposit timestamp
- Withdrawal sale value: converted at withdrawal timestamp
- Profit rows are not EUR-valued on arrival; realized only through withdrawals

Performance note:

- fund integrations pass plain currency symbols to `crypto_fx` in single-symbol mode (no pair-metadata lookups), which avoids unnecessary exchange metadata network calls during analysis.

## Outputs

- Enriched IR CSV (`*_modified.csv`)
- Declaration TXT (`*_declaration.txt`) with `Приложение 5 / Таблица 2`
- Year-end state JSON (`*_state_end_<year>.json`)
- CLI summary lines (status + output paths only)

Enriched IR CSV columns:

- `Timestamp`, `Operation ID`, `Type`, `Currency`, `Currency Type`, `Amount`
- `Amount (EUR)` (row `Amount` converted at row timestamp; keeps `Amount` sign)
- `Balance` (portfolio balance in native currency after the row)
- `Balance (EUR)` (`Balance` converted at row timestamp)
- `Deposit to Date (EUR)` (cumulative gross deposits converted at each deposit timestamp; withdrawals do not reduce this audit column)
- `Source Exchange`, `Source Row`, `Source Type`
- `Purchase Price (EUR)`, `Sale Price (EUR)`, `Profit Win (EUR)`, `Profit Loss (EUR)`, `Net Profit (EUR)`

Tax columns are populated only on withdrawal rows with non-zero net PnL.

Interpretation note:

- `Balance (EUR)` is a point-in-time FX view of native balance at the row timestamp.
- `Deposit to Date (EUR)` is historical accumulated deposit basis in EUR (time-of-deposit converted).

State JSON schema:

- `state_tax_year_end`
- `state_by_currency` object with per-currency:
- `currency_type`
- `native_deposit_balance`
- `eur_deposit_balance`
- `native_profit_balance`
- `native_total_balance`

## Validation Policy

- Hard fail:
- invalid/missing required values for supported rows
- invalid IR rows
- withdrawal without available balance or above available balance
- Profit update that drives total native balance below zero

- Warning + manual-check required:
- unsupported/unknown provider transaction types

CLI status contract for fund integrations:

- `STATUS: SUCCESS` (no manual-check issues)
- `STATUS: MANUAL CHECK REQUIRED` (warning-level issues exist)
- `STATUS: ERROR` (run failed)

## Opening-State Year Window Contract

When opening state is provided:

- opening state must include valid `state_tax_year_end`
- for requested `tax_year`, state year must be strictly `< tax_year` (otherwise analyzer fails fast)
- ledger/state math is applied only for rows where:
- `state_tax_year_end < row.timestamp.year <= tax_year`
- rows with `row.timestamp.year <= state_tax_year_end` are ignored
- rows with `row.timestamp.year > tax_year` are ignored
- declaration totals still include only rows in requested `tax_year`

When opening state is not provided:

- analyzer processes full input history to build basis/state path
- declaration totals still include only rows in requested `tax_year`
