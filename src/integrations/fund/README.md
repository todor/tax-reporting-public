# Fund Integrations

Fund integrations process fund-like account histories through a shared IR and a shared
realization engine.

## Scope

Supported IR transaction types:

- `deposit`
- `profit`
- `withdraw`

Current provider integration:

- `finexify`

## Architecture

Provider modules are intentionally thin:

1. parse provider input
2. map to shared fund IR
3. call shared analyzer and shared outputs

Shared source of truth:

- `integrations.fund.shared`

Provider-specific adapter:

- `integrations.fund.finexify`

## Tax model (high-level)

- Deposits are non-taxable.
- Profit updates are non-taxable operational rows.
- Tax realization happens only on withdrawal rows.
- Withdrawal realizes proportional parts of deposit and profit balances.

Per currency, shared state stores:

- `native_deposit_balance`
- `eur_deposit_balance`
- `native_profit_balance`

Core proportional formulas (for withdrawal `W`, current total `T = D + P`):

- `X = W / T`
- `purchase_price_eur = X * eur_deposit_balance`
- `sale_price_eur = EUR value of W at withdrawal timestamp`
- `net_profit_eur = sale_price_eur - purchase_price_eur`

## FX timing rules

- Deposit amounts are converted to EUR at deposit timestamp.
- Withdraw amounts are converted to EUR at withdrawal timestamp.
- Profit rows are not EUR-realized on arrival; they realize only through withdrawals.

## Outputs

All fund integrations use shared output writers and produce:

- enriched IR CSV (`*_modified.csv`)
- declaration TXT (`*_declaration.txt`, Приложение 5 / Таблица 2)
- year-end carry-forward state JSON (`*_state_end_<year>.json`)

Only withdrawal rows contribute to Appendix 5 taxable totals.

## Manual checks and warnings

- Unknown provider row types are surfaced as warnings and excluded.
- Invalid or impossible accounting states fail loudly (for example withdraw above available total).

## Provider docs

- Shared fund engine: [src/integrations/fund/shared/README.md](../shared/README.md)
- Finexify adapter: [src/integrations/fund/finexify/README.md](../finexify/README.md)

Provider adapters:

- `integrations.fund.finexify`
