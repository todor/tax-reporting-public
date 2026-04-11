# IBKR Activity Statement Analyzer (Phase 1)

This module analyzes Interactive Brokers Activity Statement CSV files and prepares tax-oriented outputs for Bulgarian annual reporting in EUR.

Module:

- `integrations.ibkr.activity_statement_analyzer`

## Quick Start

```bash
PYTHONPATH=src pyenv exec python -m integrations.ibkr.activity_statement_analyzer \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol
```

Optional multi-account alias:

```bash
PYTHONPATH=src pyenv exec python -m integrations.ibkr.activity_statement_analyzer \
  --input path/to/ibkr_activity_statement_account2.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol \
  --report-alias account2
```

## CLI Options

- `--input`: IBKR Activity Statement CSV (required)
- `--tax-year`: target tax year, for example `2025` (required)
- `--tax-exempt-mode`: `listed_symbol` or `execution_exchange` (required)
- `--report-alias`: optional alias added in output filenames
- `--output-dir`: optional output root (default `output/ibkr/activity_statement`)
- `--cache-dir`: optional `bnb_fx` cache override
- `--log-level`: logging level (default `INFO`)

## Scope (Phase 1)

- Input: IBKR Activity Statement CSV (multi-section format)
- Processed sections: `Trades`, `Interest`
- Withholding source section for interest credit: `Mark-to-Market Performance Summary`
- Symbol listing source: `Financial Instrument Information`
- FX source: existing `services.bnb_fx` (`get_exchange_rate`)
- Outputs:
- modified CSV (multi-section preserved, `Trades` and `Interest` sections extended)
- declaration text file (Bulgarian)
- sanity debug artifacts (`_sanity_debug`)
- stdout diagnostics

Out of scope in this phase:

- Appendix 8
- full Forex tax treatment
- non-`Stocks` / non-`Treasury Bills` asset support

## Core Principles

- deterministic processing
- preserve row order
- `Decimal` only
- no silent assumptions
- fail loudly on structural/data errors

## Header Scoping (Critical)

IBKR CSV is multi-section and sections can contain multiple header rows.

Rule:

- a `Header` row applies only to following rows of the same section
- when a new `Header` row for that section appears, it replaces the active schema
- column positions can change between header blocks and are always resolved from the active header

Implications:

- `Trades` rows use the most recent preceding `Trades,Header`
- `Interest` rows use the most recent preceding `Interest,Header`
- `Financial Instrument Information` rows use the most recent preceding matching header
- section schemas are never mixed
- `Data` before matching `Header` fails loudly

## Tax Modes

`--tax-exempt-mode listed_symbol`

- EU-listed symbol -> Приложение 13
- non-EU-listed symbol -> Приложение 5
- execution exchange is informational only (warnings for non-regulated/unknown)

`--tax-exempt-mode execution_exchange`

- non-EU-listed symbol -> Приложение 5
- EU-listed + EU-regulated execution -> Приложение 13
- EU-listed + non-regulated/unknown execution -> `REVIEW_REQUIRED` bucket (excluded from appendix totals)

## Review Workflow (Post-Review Processing)

If the active `Trades,Header,...` block contains a `Review Status` column, the analyzer uses it for closing `Trade` rows:

- empty value: row is treated as not reviewed yet; default mode logic applies
- `TAXABLE`: row is forced to `APPENDIX_5`
- `NON-TAXABLE`: row is forced to `APPENDIX_13`
- any other value: row is flagged as unknown review status, reported in outputs, and kept with manual-check warning

Notes:

- `Review Status` override is mode-independent and has priority over `--tax-exempt-mode`
- this means `TAXABLE` / `NON-TAXABLE` overrides both `listed_symbol` and `execution_exchange` logic
- this is section-local and header-scoped, same as all other `Trades` fields
- it does not change row-ordering or ClosedLot attachment logic
- it is intended to let you re-run the analyzer after manual review and route rows deterministically

## Exchange Rules

Normalization:

- uppercase
- trim spaces
- preserve dots

Aliases:

- `ISE -> ENEXT.IR`
- `BME -> SIBE`
- `BM -> SIBE`
- `EUIBSI* -> EUIBSI` (for example `EUIBSILP -> EUIBSI`)

Classification:

- `EU_REGULATED`
- `EU_NON_REGULATED`
- `UNKNOWN`

## Algorithm (Step-by-Step)

1. Parse full CSV as multi-section rows using `csv.reader`.
2. Parse `Financial Instrument Information`:
- use active header mapping
- keep only `Stocks` and `Treasury Bills`
- build `Symbol -> Listing Exch` mapping
- support comma-separated symbol aliases, for example `4GLD, 4GLDd`
- conflicting symbol mapping with different EU/non-EU classification -> fail
- Treasury Bills symbol matching:
- first try exact `Trades.Symbol == Financial Instrument Information.Symbol`
- if not found, extract 9-char uppercase alphanumeric identifier from `Trades.Symbol`
- if exactly one identifier exists, use it for mapping
- if multiple or none are found, mark row `REVIEW_REQUIRED` (no guessing)
3. Parse `Trades`:
- process only `Trades,Data` rows
- closing trade = `DataDiscriminator=Trade` and `Code` contains token `C`
- attach immediate following `ClosedLot` rows only
- fail if closing trade has no attached `ClosedLot` rows
4. Asset category handling:
- `Stocks`, `Treasury Bills`: processed
- `Forex`: ignored for appendix totals, explicitly marked in outputs
- any other category: fail
5. Tax-year filter:
- trade inclusion uses Trade `Date/Time` year
- `ClosedLot` rows may be from prior years
6. FX conversion:
- Trade `Proceeds` -> EUR at trade date
- Trade `Comm/Fee` -> EUR at trade date
- `ClosedLot` `Basis` -> EUR at closed-lot date
7. Closing-trade basis:
- `Trade Basis (EUR) = -sum(ClosedLot Basis (EUR))`
8. Closing-trade realized P/L:
- `pnl_eur = proceeds_eur + basis_eur + comm_fee_eur`
9. Appendix classification:
- by selected `tax-exempt-mode`
 - optional `Review Status` override (`TAXABLE` / `NON-TAXABLE`) applied after default classification
10. Sale/Purchase price totals per closing trade:
- `cash_leg = proceeds_eur + comm_fee_eur`
- if `cash_leg >= 0`: `sale_price += abs(cash_leg)`, `purchase += abs(basis_eur)`
- if `cash_leg < 0`: `sale_price += abs(basis_eur)`, `purchase += abs(cash_leg)`

## Interest Processing (Appendix 6 / 9)

Parsed section:

- `Interest,Header,...`
- `Interest,Data,...`

Required active-header columns:

- `Currency`
- `Date`
- `Description`
- `Amount`

Total rows are skipped when `Currency` starts with:

- `Total`
- `Total in EUR`
- `Total Interest in EUR`

Interest type extraction:

- strip leading currency token in `Description`
- strip trailing `for <...>`
- classify normalized type

Supported types:

- `Credit Interest` -> `TAXABLE` (Appendix 6 code 603)
- `IBKR Managed Securities (SYEP) Interest` -> `TAXABLE` (Appendix 6 code 603)
- `Debit Interest` -> `NON-TAXABLE`
- `Borrow Fees` -> `NON-TAXABLE`
- unknown type -> `UNKNOWN` (no EUR conversion for now)

Interest review override:

- if the active `Interest` header has `Review Status`, that value is treated as human review input
- `Review Status=TAXABLE` forces `Status=TAXABLE`
- `Review Status=NON-TAXABLE` forces `Status=NON-TAXABLE`
- `Review Status=UNKNOWN` or `Review Status=REVIEW-REQUIRED` forces `Status=UNKNOWN`
- empty `Review Status` keeps automatic classification
- analyzer does not auto-fill `Review Status` for interest rows

EUR conversion rules:

- taxable interest rows only
- conversion date = `Interest.Date`
- conversion currency = `Interest.Currency`
- amount source = `Interest.Amount`

Appendix 6:

- `Обща сума на доходите с код 603` = taxable interest EUR total
- includes `Credit Interest` + `IBKR Managed Securities (SYEP) Interest`

Appendix 9 source for paid foreign tax:

- section `Mark-to-Market Performance Summary`
- row with `Asset Category = Withholding on Interest Received`
- use `abs(Mark-to-Market P/L Total)`
- if this row is missing while credit interest exists, analyzer keeps `Платен данък в чужбина = 0` and marks manual review

Appendix 9 calculations:

- country fixed: `Ирландия`
- income code: `603`
- gross income / tax base: credit-interest EUR total only
- allowable credit: `APPENDIX_9_ALLOWABLE_CREDIT_RATE * credit_interest_total_eur`
- default code constant is `APPENDIX_9_ALLOWABLE_CREDIT_RATE = 0.10` (change in code if regulation changes)
- recognized credit: `min(allowable_credit, paid_tax_abroad)`

## How To Read The Modified CSV

The analyzer modifies only `Trades` and `Interest` rows. All other sections are preserved exactly.

### Added Trades Columns

- `Fx Rate`
- `Comm/Fee (EUR)`
- `Proceeds (EUR)`
- `Basis (EUR)`
- `Sale Price (EUR)`
- `Purchase Price (EUR)`
- `Realized P/L (EUR)`
- `Realized P/L Wins (EUR)`
- `Realized P/L Losses (EUR)`
- `Normalized Symbol`
- `Listing Exchange`
- `Symbol Listed On EU Regulated Market`
- `Execution Exchange Classification`
- `Tax Exempt Mode`
- `Appendix Target`
- `Tax Treatment Reason`
- `Review Required`
- `Review Notes`

### Added Interest Columns

- `Amount (EUR)`
- `Status`

### Row-Type Semantics

- Closing `Trade` rows:
- all EUR calculation columns are populated
- `Sale Price (EUR)` / `Purchase Price (EUR)` are populated
- Open-entry `Trade` rows:
- `Realized P/L (EUR)` is `0`
- `Sale Price (EUR)` / `Purchase Price (EUR)` remain empty
- `ClosedLot` rows:
- populate `Fx Rate` and `Basis (EUR)`
- `Sale Price (EUR)` / `Purchase Price (EUR)` remain empty
- `SubTotal` / `Total` rows:
- EUR columns are populated from analyzer aggregates
- when both native and derived EUR aggregate rows exist, only native-currency rows are used for aggregate reconciliation
- if EUR-native trades exist, matching EUR aggregate rows are populated/checked
- `Interest` data rows:
- non-total rows receive `Amount (EUR)` and `Status`
- analyzer fills `Status` only (`TAXABLE`, `NON-TAXABLE`, `UNKNOWN`)
- unknown interest types are flagged as `UNKNOWN` and included in global manual-check state
- total rows in Interest section are not processed as income rows

## Declaration TXT Output

The declaration file includes:

- Приложение 5 totals
- Приложение 13 totals
- Приложение 6 (Част I) interest total for code 603
- Приложение 9 (Част II) credit-interest and foreign-tax credit data
- optional `РЪЧНА ПРОВЕРКА` section (execution mode with review rows)
- sanity-check section (`PASS`/`FAIL`, counts, artifact paths)
- mandatory Forex warning section
- evidence section (mode, counts, exchanges, warnings)
- review diagnostics (`review overrides`, `unknown Review Status` counts/values)

`нетен резултат (EUR)` is reported as `печалба - загуба`.

## Sanity Check Gate

After the modified CSV is produced, the analyzer runs sanity checks in verification mode (`FX=1`) and writes artifacts under:

- `output/ibkr/activity_statement/_sanity_debug/...`

Artifacts:

- `ibkr_activity_modified_fx1_debug.csv`
- `sanity_report.json`

The debug CSV preserves row order and adds `DEBUG_SANITY_*` columns so failing rows are easy to inspect.

If sanity checks fail:

- run exits non-zero
- declaration TXT includes failure summary and artifact paths
- diagnostics are written to `sanity_report.json`

## Forex Behavior

Forex trades are excluded from Appendix 5/13 totals in this phase.

- no hard failure on Forex rows
- explicit ignored classification in output
- declaration TXT always contains `ВНИМАНИЕ: FOREX ОПЕРАЦИИ`
- current behavior also marks manual check as required when Forex rows are present

## Errors (Fail Loudly)

- missing required sections (`Financial Instrument Information`, `Trades`)
- missing required section columns
- malformed trade/closed-lot dates
- malformed interest dates
- invalid decimal values
- FX conversion failure
- unsupported asset category
- closing trade without `ClosedLot` rows
- conflicting symbol mapping with different EU classification

## Output Paths

Default output directory:

- `output/ibkr/activity_statement/`

Main files:

- `ibkr_activity_modified_<tax_year>.csv`
- `ibkr_activity_declaration_<tax_year>.txt`

With alias (`--report-alias account2`):

- `ibkr_activity_account2_modified_<tax_year>.csv`
- `ibkr_activity_account2_declaration_<tax_year>.txt`

Sanity artifacts:

- `output/ibkr/activity_statement/_sanity_debug/ibkr_activity[_<alias>]_<tax_year>/ibkr_activity_modified_fx1_debug.csv`
- `output/ibkr/activity_statement/_sanity_debug/ibkr_activity[_<alias>]_<tax_year>/sanity_report.json`

Alias normalization:

- trim spaces
- spaces -> `_`
- keep only `[A-Za-z0-9._-]`

## Future Extension

Planned later phases can add:

- broader asset support
- richer review workflows
- Appendix 8 and additional declaration structures
