# Shared Crypto Engine (`integrations.crypto.shared`)

This package is the single source of truth for crypto accounting and tax math used by crypto integrations.

Provider-specific modules (for example Coinbase) must only:

- parse raw provider files
- map rows to IR (`CryptoIrRow`)
- call the shared analyzer/output helpers

They must not duplicate holdings, cost-basis, or PnL logic.

## Modules

- `crypto_ir_models.py`: IR dataclasses, validation, summary/result models
- `generic_ledger.py`: signed average-cost ledger (long/short aware)
- `generic_crypto_analyzer.py`: chronological IR processing + disposal realization
- `crypto_outputs.py`: enriched IR CSV, declaration TXT, holdings/state JSON + CLI summary lines
- `runtime.py`: shared EUR rate-provider fallback and standard enriched-output path builders

## IR Schema (`CryptoIrRow`)

Required core fields:

- `timestamp`
- `operation_id`
- `transaction_type`: `Deposit | Withdraw | Buy | Sell | Earn`
- `asset`
- `asset_type`: `fiat | crypto`
- `quantity` (signed by transaction semantics)
- `proceeds_eur` (required for `Buy` and `Sell`)
- `fee_eur` (optional informational)
- `cost_basis_eur` (optional explicit basis, used for manual workflows)
- `review_status` (optional)

Audit/source fields:

- `source_exchange`
- `source_row_number`
- `source_transaction_type`
- `operation_leg` (for multi-leg operations such as `Convert`)

## Position Model

Per asset, the ledger stores signed state:

- `quantity > 0`: long
- `quantity < 0`: short
- `quantity == 0`: flat

`total_cost_eur` follows the same sign convention.

Average entry price:

- `abs(total_cost_eur) / abs(quantity)` when quantity is non-zero

## Realization Rules

PnL is realized only on the closing leg:

- same-direction extension: no realized PnL
- opposite-direction row: close existing position first
- any remaining quantity opens the opposite-direction position

This supports:

- partial closes
- full closes
- long->short flips
- short->long flips

`Withdraw` (crypto movement) uses proportional basis reduction only.
`Deposit`/`Earn` can close short if they increase quantity against an existing short.

## Outputs

`crypto_outputs.py` provides shared outputs:

- Enriched IR CSV
- Bulgarian declaration TXT (`Приложение 5 / Таблица 2`)
- Holdings/state JSON (`quantity`, `total_cost_eur`, `average_price_eur`)
- CLI summary lines (status + output paths only)

Enriched IR CSV column schema:

- IR columns:
- `Timestamp`, `Operation ID`, `Transaction Type`, `Asset`, `Asset Type`
- `Quantity`, `Proceeds (EUR)`, `Fee (EUR)`, `Cost Basis (EUR)`, `Review Status`
- `Source Exchange`, `Source Row`, `Source Transaction Type`, `Operation Leg`
- Tax columns:
- `Purchase Price (EUR)`, `Sale Price (EUR)`, `Profit Win (EUR)`, `Profit Loss (EUR)`, `Net Profit (EUR)`

Enriched IR CSV numeric formatting:

- `Quantity`, `Proceeds (EUR)`, `Fee (EUR)`, and `Cost Basis (EUR)` keep Decimal precision from mapping/analysis (no forced 8-decimal quantization).
- Tax-result columns below keep fixed 8-decimal formatting.

Enriched CSV tax columns:

- `Purchase Price (EUR)`
- `Sale Price (EUR)`
- `Profit Win (EUR)`
- `Profit Loss (EUR)`
- `Net Profit (EUR)`

These fields are populated only for rows with a non-zero realized closing PnL.

State JSON schema (produced by `write_holdings_state_json`):

- top-level:
- `state_tax_year_end`
- `holdings_by_asset` (object)
- per asset object:
- `quantity`
- `total_cost_eur`
- `average_price_eur`

Opening state JSON schema (consumed by `load_holdings_state_json`):

- required top-level object: `holdings_by_asset`
- each asset entry must include:
- `quantity`
- `total_cost_eur`
- optional top-level: `state_tax_year_end`

CLI status contract for crypto integrations:

- `STATUS: SUCCESS` (no manual-check issues)
- `STATUS: MANUAL CHECK REQUIRED` (warning-level issues exist)
- `STATUS: ERROR` (run failed)

## Validation Policy

To keep behavior consistent across providers:

- Hard fail:
- schema/header issues
- unparseable timestamps/numbers
- unrecoverable FX conversion errors
- Warning + manual-check + row excluded:
- unsupported transaction combinations
- malformed multi-row operation groupings
- missing/invalid manual review inputs for receive-like basis rows (for example `Review Status` / `Cost Basis (EUR)`)
- Silent exclude (no warning/manual-check):
- none by default

- Receive-like status handling:
- `CARRY_OVER_BASIS`: use provided `Cost Basis (EUR)` as acquisition basis
- `GIFT`: force `Cost Basis (EUR)=0`
- `NON-TAXABLE`: inventory movement at market EUR value, no taxable PnL

- Non-taxable receive-like rows (`Review Status=NON-TAXABLE`):
- are mapped as non-taxable inventory movements (affect holdings/state)
- do not create taxable PnL
- do not require `Cost Basis (EUR)` input
