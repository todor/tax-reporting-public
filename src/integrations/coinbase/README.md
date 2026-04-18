# Coinbase Report Analyzer

Module:

- `integrations.coinbase.report_analyzer`

Purpose:

- Parse Coinbase CSV export format (`Coinbase Report - since inception.csv` style).
- Compute EUR acquisition/disposal/profit using per-asset average-cost model with signed positions (long + short).
- Process full history for holdings/basis continuity, while declaration totals are limited to selected tax year.
- Produce:
- modified CSV with EUR/tax columns
- declaration-oriented TXT for `Приложение 5 / Таблица 2`

## Input Format

Analyzer ignores any preamble rows before the first CSV header line that contains required Coinbase columns (`Timestamp`, `Transaction Type`, `Asset`, `Quantity Transacted`, `Price Currency`).

- Coinbase exports with a leading `ID` column are also supported (for example `ID,Timestamp,...`)

Timestamp format supported:

- `YYYY-MM-DD HH:MM:SS UTC` (for example `2025-06-26 10:47:50 UTC`)

Required core columns:

- `Timestamp`
- `Transaction Type`
- `Asset`
- `Quantity Transacted`
- `Price Currency`
- `Subtotal`
- `Total` or `Total (inclusive of fees and/or spread)`
- `Notes`

Optional manual-review columns:

- `Review Status`
- `Purchase Price`

## Supported Transaction Types

- `Buy`
- `Sell`
- `Convert`
- `Send`
- `Receive`
- `Deposit`
- `Withdraw`
- `Withdrawal` (treated as `Withdraw`)

Unknown `Transaction Type` rows:

- are excluded from tax calculations
- are emitted as warnings
- trigger manual-check required status

## EUR Conversion

Price/value strings may include currency markers, for example:

- `€4032.61610`
- `BGN6579`

Analyzer strips markers and parses numeric value, then converts by row timestamp and `Price Currency`:

- fiat via `services.bnb_fx`
- crypto/stable via `services.crypto_fx` (`binance` pricing path)

Required conversion failures fail fast with row details.

## Average-Cost Model

Holdings are tracked per asset with:

- signed quantity
- signed total cost (EUR)
- average entry price = `abs(total_cost) / abs(quantity)` when quantity is non-zero

Position interpretation:

- `quantity > 0`: long
- `quantity < 0`: short
- `quantity == 0`: flat (`total_cost == 0`)

### Processing Order

- Input row order is preserved in output CSV.
- Tax/ledger processing runs in chronological order:
- if input rows are reverse-chronological, analyzer reverses them before processing
- otherwise analyzer sorts by timestamp before processing
- This guarantees correct average-cost basis evolution regardless of export order.
- Declaration totals (`Приложение 5`) include only taxable disposals whose timestamp year equals `--tax-year`.

### Sign Handling (Important)

- For `Buy` / `Sell` / `Send` / `Receive`, `Quantity Transacted` is treated by absolute value (`abs(quantity)`).
- `Subtotal (EUR)` and `Total (EUR)` output columns preserve the signed converted values for audit/debug.
- Value sourcing stays unchanged:
- `Buy`/`Receive` leg execution value uses `abs(Total (EUR))` or `Purchase Price` (for Receive)
- `Sell` / `Convert` source / `Send` sale-leg execution value uses `abs(Subtotal (EUR))`
- Realization is on closing legs only (not on every `Sell`):
- same-direction extension: no realized PnL
- opposite-direction trade: closes existing position first (realized PnL on closed quantity only), then opens opposite position with any remainder
- For partial-close/flip rows, `Purchase Price (EUR)` / `Sale Price (EUR)` / `Net Profit (EUR)` represent the realized closing portion only.

### Buy

- if current position is long/flat: opens or extends long (no realized PnL)
- if current position is short: closes short up to available quantity; realized PnL is computed on closed part only; remainder (if any) opens long

### Sell

- if current position is short/flat: opens or extends short (no realized PnL)
- if current position is long: closes long up to available quantity; realized PnL is computed on closed part only; remainder (if any) opens short

### Convert

- `Notes` must match exactly:
- `Converted <qty_sold> <asset_sold> to <qty_bought> <asset_bought>`
- Convert is processed as two legs with existing value conventions:
- source leg behaves like `Sell` of `asset_sold`
- target leg behaves like `Buy` of `asset_bought`
- either leg may close an opposite-direction open position and realize PnL
- invalid notes format fails fast

### Send

- modeled as a sell-like leg for signed accounting
- `Review Status` controls taxable handling:
- `TAXABLE`: computes transfer row values (`Purchase Price (EUR)`, `Sale Price (EUR)`, `Net Profit (EUR)`) from the closing leg only, for downstream analyzer workflows
- `NON-TAXABLE`: no taxable gain/loss
- both `TAXABLE` and `NON-TAXABLE` `Send` are excluded from Appendix 5 totals in this analyzer
- missing/invalid status triggers warning + manual-check required
- current analyzer intentionally validates `Send` only against existing long holdings and rejects send-against-short flows with a clear error

### Receive

- requires `Review Status` in:
- `CARRY_OVER_BASIS`
- `RESET_BASIS_FROM_PRIOR_TAX_EVENT`
- requires `Purchase Price` (EUR total basis)
- missing/invalid receive basis metadata fails fast
- behaves as buy/open-long leg with provided basis
- if current position is short, `Receive` first closes short quantity (realizing PnL on closed part) and any remainder opens/adds long

### Deposit / Withdraw

- currently treated as fiat-only rows and ignored for tax/basis calculations
- if asset is crypto, analyzer fails fast (explicit unsupported flow)

## Output Files

Default output directory:

- `output/coinbase/`

Files:

- `<input_stem>_modified.csv`
- `<input_stem>_declaration.txt`
- `<input_stem>_state_end_<tax_year>.json`

### Added CSV Columns

- `Subtotal (EUR)`
- `Total (EUR)`
- `Purchase Price (EUR)`
- `Sale Price (EUR)`
- `Profit Win (EUR)`
- `Profit Loss (EUR)`
- `Net Profit (EUR)`

### TXT Structure

Manual-check summary is always printed at top (`REQUIRED` or `NOT REQUIRED`), then:

- `Приложение 5`
- `Таблица 2`
- `продажна цена (EUR) - код 5082`
- `цена на придобиване (EUR) - код 5082`
- `печалба (EUR) - код 5082`
- `загуба (EUR) - код 5082`
- `Информативни`
- `нетен резултат (EUR)`
- `брой сделки`
- informational metrics including `manual check overrides (Review Status non-empty)`

Additional conditional footer:

- `ИНСТРУКЦИЯ ЗА СЛЕДВАЩ АНАЛИЗАТОР`
- printed only when there is at least one taxable `Send` event
- instructs using `Purchase Price (EUR)` from modified CSV as input for the next analyzer/platform

### Year-End State JSON

The analyzer writes an end-of-year holdings state file for the requested `--tax-year`.

Schema:

- `state_tax_year_end`: integer year represented by this state
- `holdings_by_asset`: object keyed by asset symbol
- each asset entry contains:
- `quantity` (string decimal)
- `total_cost_eur` (string decimal; can be negative for short positions)

This enables incremental workflow:

1. Run year `N` and keep `*_state_end_N.json`.
2. For year `N+1`, pass only `N+1` operations CSV plus `--opening-state-json *_state_end_N.json`.
3. The analyzer computes year `N+1` declaration totals with the same basis continuity as full-history input.

## CLI

```bash
PYTHONPATH=src pyenv exec python -m integrations.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025
```

Optional:

```bash
PYTHONPATH=src pyenv exec python -m integrations.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025 \
  --opening-state-json output/coinbase/coinbase_report_since_inception_state_end_2024.json \
  --output-dir output/coinbase \
  --cache-dir ~/.cache/tax_reporting
```

CLI stdout includes:

- processed row count
- `manual_check_overrides_rows` (informational count of rows where `Review Status` is non-empty)
- manual-check status
- Appendix 5 totals (`sale_price_eur`, `purchase_price_eur`, `wins_eur`, `losses_eur`, `net_result_eur`)
- all EUR totals are printed with 2 decimal places
- output file paths
- includes path to year-end state JSON

## Current Limitations

- `Deposit` / `Withdraw` support is fiat-only in this version.
- Unknown transaction types are not auto-modeled; they are excluded and flagged for manual review.
- `Send` requires manual review status to decide taxable vs non-taxable behavior and is allowed only against existing long holdings.
- `Receive` requires explicit basis metadata (`Review Status` + `Purchase Price`).
