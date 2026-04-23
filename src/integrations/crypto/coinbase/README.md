# Coinbase Report Analyzer

Entry point:

- `integrations.crypto.coinbase.report_analyzer`

## Architecture

Coinbase is now split into parse/map/orchestrate layers:

- `coinbase_parser.py`: raw CSV parsing + field normalization
- `coinbase_to_ir.py`: Coinbase rows -> normalized crypto IR rows
- `report_analyzer.py`: orchestration only (parse -> map -> generic analyze -> outputs)

Shared crypto engine is under `integrations.crypto.shared`:

- `crypto_ir_models.py`: IR schema/types + validation
- `generic_crypto_analyzer.py`: single accounting/tax engine
- `generic_ledger.py`: signed average-cost position ledger
- `crypto_outputs.py`: enriched CSV, declaration TXT, holdings state JSON
- `runtime.py`: default EUR rate-provider fallback + standard output path naming

Coinbase modules do not implement holdings/PnL/tax math directly.

## Coinbase Statements Value Rules

For Coinbase Statements CSV:

- `Total = Subtotal + Fees`
- Use `Total` for all economic values
- Exception for `Convert`:
- source sell leg proceeds use `Subtotal`
- target buy leg cost uses `Total`

`Fees and/or Spread` is parsed explicitly and kept in IR as informational fee data.

## Input CSV Schema

Canonical required columns:

- `Timestamp`
- `Transaction Type`
- `Asset`
- `Quantity Transacted`
- `Price Currency`
- `Subtotal`
- `Total` (or `Total (inclusive of fees and/or spread)`)
- `Notes`

Optional columns:

- `Fees and/or Spread` (alias accepted: `Fees`)
- `Review Status`
- `Cost Basis (EUR)`

Practical input notes:

- Coinbase preamble rows before the header are ignored automatically.
- Header is accepted with or without leading `ID` column.

## IR Mapping Rules

Supported Coinbase transaction types:

- `Buy`
- `Sell`
- `Convert`
- `Send`
- `Receive`
- `Deposit`
- `Withdraw`
- `Withdrawal` (alias to `Withdraw`)

Mapping highlights:

- `Buy` -> IR `Buy` (positive quantity, proceeds from `Total`)
- `Sell` -> IR `Sell` (negative quantity, proceeds from `Total`)
- `Convert` -> two IR rows with same `Operation ID`:
- source `Sell` (`Subtotal` proceeds)
- target `Buy` (`Total` cost)
- `Send` -> IR `Withdraw` (`source_transaction_type=Send`)
- `Receive` -> IR `Deposit` (`source_transaction_type=Receive`, with manual basis workflow)
- Coinbase transaction semantics are applied directly:
- `Deposit`/`Withdraw` are treated as fiat movements
- `Send`/`Receive` are treated as crypto movements

Unknown transaction types are excluded from tax calculations, warned, and force manual-check required.

## Signed Position Model

Per asset state is signed:

- `quantity > 0`: long
- `quantity < 0`: short
- `quantity == 0`: flat

`total_cost_eur` follows quantity sign and average price is:

- `abs(total_cost_eur) / abs(quantity)` when quantity is non-zero

Realization is only on closing legs:

- same direction extension: no realized PnL
- opposite direction trade: close existing position first, then open opposite remainder if any
- supports partial closes, full closes, and long<->short flips in one row

## Convert / Send / Receive Behavior

`Convert`:

- source leg behaves like sell of source asset
- target leg behaves like buy of target asset
- target buy may close an existing short
- Appendix 5 totals are grouped by `Operation ID` so multi-leg convert keeps prior Coinbase totals behavior

`Send`:

- decreases quantity and removes proportional basis only
- no PnL realization in this analyzer
- `Review Status` accepted values:
- `TAXABLE`
- `NON-TAXABLE`
- invalid/missing value triggers warning + manual-check required

`Receive`:

- accepts `Review Status`:
- `CARRY_OVER_BASIS`
- `RESET_BASIS_FROM_PRIOR_TAX_EVENT`
- `NON-TAXABLE` (non-taxable inventory movement)
- requires `Cost Basis (EUR)` as explicit basis for basis-carrying statuses
- if `Review Status` is `NON-TAXABLE`:
- row is mapped as non-taxable receive movement (affects holdings/state, no taxable PnL)
- `Cost Basis (EUR)` is not required and no warning/manual-check is emitted
- if `Review Status` / `Cost Basis (EUR)` is missing or invalid (for basis-carrying statuses):
- warning + manual-check required, and row is excluded from tax calculations
- can close existing short quantity:
- basis-carrying statuses realize PnL on the closed short portion
- `NON-TAXABLE` closes/opens inventory without taxable PnL realization

## Outputs

Default output directory:

- `output/coinbase/`

Generated files:

- `<input_stem>_modified.csv` (enriched IR CSV)
- `<input_stem>_declaration.txt`
- `<input_stem>_state_end_<tax_year>.json`

Note:

- there is no separate `*_ir.csv` file; `*_modified.csv` is the primary enriched IR CSV output.

Enriched CSV includes:

- IR columns: `Timestamp`, `Operation ID`, `Transaction Type`, `Asset`, `Asset Type`, `Quantity`, `Proceeds (EUR)`, `Fee (EUR)`, `Cost Basis (EUR)`, `Review Status`, source/audit columns
- Tax columns: `Purchase Price (EUR)`, `Sale Price (EUR)`, `Profit Win (EUR)`, `Profit Loss (EUR)`, `Net Profit (EUR)`

Numeric formatting policy:

- IR numeric columns (`Quantity`, `Proceeds (EUR)`, `Fee (EUR)`, `Cost Basis (EUR)`) keep Decimal precision from mapping/analysis.
- Tax-result columns keep fixed 8-decimal formatting.

Tax column policy:

- these tax columns are populated only for closing legs that realize non-zero profit/loss
- non-closing rows (for example position extensions, Send basis movements, non-closing Receive) keep them empty

Declaration TXT preserves `Приложение 5 / Таблица 2` structure and includes:

- manual-check block
- informational `manual check overrides (Review Status non-empty)` metric

Holdings JSON includes:

- `quantity`
- `total_cost_eur`
- `average_price_eur`

for each asset, plus `state_tax_year_end`.

## CLI

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025
```

CLI options:

- `--input` (required)
- `--tax-year` (required)
- `--opening-state-json` (optional prior state)
- `--output-dir` (optional, default `output/coinbase`)
- `--cache-dir` (optional FX cache override)
- `--log-level` (optional, default `INFO`)

CLI stdout policy:

- `STATUS: SUCCESS` or `STATUS: MANUAL CHECK REQUIRED` on successful runs
- `STATUS: ERROR` on failure
- output file paths only (no duplicated diagnostic counters)

With opening state:

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - 2026.csv" \
  --tax-year 2026 \
  --opening-state-json output/coinbase/<previous_run_state_end_2025>.json
```
