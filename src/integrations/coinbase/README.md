# Coinbase Report Analyzer

Module:

- `integrations.coinbase.report_analyzer`

Purpose:

- Parse Coinbase CSV export format (`Coinbase Report - since inception.csv` style).
- Compute EUR acquisition/disposal/profit using per-asset average-cost model.
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

- quantity
- total acquisition cost (EUR)
- average price = total_cost / quantity

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
- Tax basis math uses normalized absolute values by transaction semantics:
- `Buy` acquisition basis uses `abs(Total (EUR))`
- `Sell` / `Convert` sale leg uses `abs(Subtotal (EUR))`
- taxable `Send` also computes disposal-form values in row output for downstream transfer workflows, but is not accumulated in Appendix 5

### Buy

- acquisition cost = `Total (EUR)`
- increases holdings

### Sell

- taxable disposal
- sale price = `Subtotal (EUR)`
- purchase price = average price * sold quantity
- net profit = sale - purchase
- reduces holdings

### Convert

- `Notes` must match exactly:
- `Converted <qty_sold> <asset_sold> to <qty_bought> <asset_bought>`
- source disposal uses `Subtotal (EUR)` as sale price
- target acquisition uses same `Subtotal (EUR)` as acquisition cost
- invalid notes format fails fast

### Send

- always reduces holdings with average-cost basis
- `Review Status` controls taxable handling:
- `TAXABLE`: computes transfer row values using `Subtotal (EUR)` and `Purchase Price (EUR)` for downstream analyzer workflows
- `NON-TAXABLE`: no taxable gain/loss
- both `TAXABLE` and `NON-TAXABLE` `Send` are excluded from Appendix 5 totals in this analyzer
- missing/invalid status triggers warning + manual-check required

### Receive

- no taxable gain/loss at receipt time
- requires `Review Status` in:
- `CARRY_OVER_BASIS`
- `RESET_BASIS_FROM_PRIOR_TAX_EVENT`
- requires `Purchase Price` (EUR total basis)
- missing/invalid receive basis metadata fails fast

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
- `total_cost_eur` (string decimal)

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
- manual-check status
- Appendix 5 totals (`sale_price_eur`, `purchase_price_eur`, `wins_eur`, `losses_eur`, `net_result_eur`)
- all EUR totals are printed with 2 decimal places
- output file paths
- includes path to year-end state JSON

## Current Limitations

- `Deposit` / `Withdraw` support is fiat-only in this version.
- Unknown transaction types are not auto-modeled; they are excluded and flagged for manual review.
- `Send` requires manual review status to decide taxable vs non-taxable behavior.
- `Receive` requires explicit basis metadata (`Review Status` + `Purchase Price`).
