# Kraken Report Analyzer

Entry point (user-facing):

- `PYTHONPATH=src pyenv exec python -m report_analyzer kraken ...`


## Architecture

Kraken follows the same IR architecture as Coinbase:

- `kraken_parser.py`: raw Kraken CSV parsing + field normalization
- `kraken_to_ir.py`: Kraken rows -> normalized crypto IR rows
- `report_analyzer.py`: orchestration only (parse -> map -> generic analyze -> shared outputs)

All holdings/PnL/tax logic runs in `integrations.crypto.shared`.

## Kraken CSV Format

Expected base columns:

- `txid`
- `refid`
- `time`
- `type`
- `subtype`
- `aclass`
- `subclass`
- `asset`
- `wallet`
- `amount`
- `fee`
- `balance`

Optional manual-basis columns:

- `Review Status`
- `Cost Basis (EUR)`

Timestamp format:

- `YYYY-MM-DD HH:MM:SS` (interpreted as UTC)

Practical input notes:

- Unknown `type/subtype/subclass` combinations are not silently remapped.
- They are surfaced as warnings/manual-check and excluded from tax calculations.

## Mapping Rules

### Pairing and operation grouping

- Multi-row operations are grouped by `refid` and mapped to one `Operation ID`.
- Paired rows are consumed once and not processed twice.

### `deposit` (fiat)

- IR `Deposit`, `Asset Type=fiat`, positive quantity.
- Fiat `Deposit/Withdraw` rows are ignored for taxable PnL by the shared analyzer.

### `deposit` (non-fiat) and standalone `receive`

- Mapped as receive-like IR `Deposit` with `source_transaction_type=Receive`.
- Require manual basis fields:
- `Review Status` in:
- `CARRY_OVER_BASIS`
- `RESET_BASIS_FROM_PRIOR_TAX_EVENT`
- `NON-TAXABLE` (non-taxable inventory movement)
- `Cost Basis (EUR)` present and non-negative for basis-carrying statuses.
- If `Review Status` is `NON-TAXABLE`:
- row is mapped as non-taxable receive movement (affects holdings/state, no taxable PnL).
- `Cost Basis (EUR)` is not required and no warning/manual-check is emitted.
- If those manual basis fields are missing/invalid for basis-carrying statuses:
- warning + manual-check required, and row is excluded from tax calculations.
- Quantity is net of same-asset fee (`amount - fee`).

### `spend` + `receive` pair (same `refid`)

- Mapped to one IR `Buy` row (acquired asset from `receive` leg).
- Acquisition value uses the `spend` leg amount converted to EUR.
- Buy fee uses the `spend` leg fee converted to EUR.
- This path prefers direct fiat/stablecoin valuation from the paired data and avoids crypto FX when direct valuation exists.

### `trade / tradespot` pair (same `refid`)

- Mapped to two IR rows:
- IR `Sell` for negative-amount leg
- IR `Buy` for positive-amount leg
- Trade value EUR is derived with priority:
- EUR leg
- USD leg
- USDC/USDT leg (treated as USD 1:1)
- fallback to crypto FX only if no direct EUR/USD/USDC/USDT leg exists
- Buy quantity is net of same-asset fee on bought leg.
- Buy fee in asset units is valued by trade-implied rate:
- `trade_value_eur / bought_amount_before_fee`
- Sell proceeds are net of sell-side fee.

### `earn / reward`

- Mapped to IR `Earn`
- quantity = `amount - fee`
- zero-cost basis (`proceeds=0`, `cost_basis=0`)

### Ignored internal operations

- `transfer / spotfromfutures`
- `earn / autoallocation`

### Unknown or malformed combinations

- Added as warnings/manual-check (`unsupported_transaction_rows`)
- excluded from tax calculations
- never silently remapped

## Outputs

Output format is shared across IR-based crypto analyzers:

- Enriched IR CSV (`*_modified.csv`)
- Bulgarian declaration text (`*_declaration.txt`)
- Year-end holdings/state JSON (`*_state_end_<year>.json`)

No Kraken-shaped patched CSV is emitted as primary output.

Enriched IR CSV numeric formatting:

- IR numeric columns (`Quantity`, `Proceeds (EUR)`, `Fee (EUR)`, `Cost Basis (EUR)`) keep Decimal precision from mapping/analysis.
- Tax-result columns (`Purchase Price`, `Sale Price`, `Profit Win/Loss`, `Net Profit`) keep fixed 8-decimal formatting.

## CLI

```bash
PYTHONPATH=src pyenv exec python -m report_analyzer kraken \
  --input "path/to/kraken_ledger.csv" \
  --tax-year 2025
```

CLI options:

- `--input` (required)
- `--tax-year` (required)
- `--opening-state-json` (optional prior state)
- `--output-dir` (optional, default `output/kraken`)
- `--cache-dir` (optional FX cache override)
- `--display-currency {EUR,BGN}` (optional, TXT rendering only; calculations stay in EUR)
- `--log-level` (optional, default `INFO`)

CLI stdout policy:

- `STATUS: SUCCESS` or `STATUS: MANUAL CHECK REQUIRED` on successful runs
- `STATUS: ERROR` on failure
- output file paths only (no duplicated diagnostic counters)

With opening state:

```bash
PYTHONPATH=src pyenv exec python -m report_analyzer kraken \
  --input "path/to/kraken_ledger_2026.csv" \
  --tax-year 2026 \
  --opening-state-json output/kraken/<previous_run_state_end_2025>.json
```
