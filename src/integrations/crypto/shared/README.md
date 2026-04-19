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

Enriched CSV tax columns:

- `Purchase Price (EUR)`
- `Sale Price (EUR)`
- `Profit Win (EUR)`
- `Profit Loss (EUR)`
- `Net Profit (EUR)`

These fields are populated only for rows with a non-zero realized closing PnL.
