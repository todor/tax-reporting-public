# IBKR Activity Statement Analyzer

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

## Scope

Processed sections:

- `Financial Instrument Information`
- `Trades`
- `Interest`
- `Dividends`
- `Withholding Tax`
- `Mark-to-Market Performance Summary` (interest withholding source for Appendix 9)

FX source:

- existing `services.bnb_fx` (`get_exchange_rate`)

Outputs:

- modified CSV (multi-section preserved; only selected sections are extended)
- declaration text file (Bulgarian)
- sanity debug artifacts (`_sanity_debug`)
- stdout diagnostics

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
- `Dividends` rows use the most recent preceding `Dividends,Header`
- `Withholding Tax` rows use the most recent preceding `Withholding Tax,Header`
- `Financial Instrument Information` rows use the most recent preceding matching header
- section schemas are never mixed
- `Data` before matching `Header` fails loudly

## Open Position Reconciliation Safety Check

The analyzer runs a minimal consistency check to flag suspicious open positions for manual review.

What is compared:

- `Open Positions` rows with `DataDiscriminator=Summary` (summary quantity)
- `Trades` rows with `DataDiscriminator=Order` (signed quantity from CSV, no sign transformation)

Quantity parsing in this check is normalized for IBKR formatting:

- comma thousands separators are accepted (for example `1,001`)
- empty quantity is treated as `0`

Both sides are normalized to the canonical instrument using the same Financial Instrument Information mapping logic (including symbol aliases such as `4GLD` / `4GLDd`).

Manual review is triggered when:

- open-position summary row cannot be matched to canonical instrument (`OPEN_POSITION_UNMATCHED_INSTRUMENT`)
- trades order row cannot be matched to canonical instrument (`TRADE_UNMATCHED_INSTRUMENT`)
- summed open quantity differs from summed signed order quantity beyond epsilon (`OPEN_POSITION_TRADE_QTY_MISMATCH`)

Scope/limits:

- this is a minimal safety net only
- no lot matching
- no timestamp matching
- no transfer/corporate-action parsing in this check

## Tax Modes (Trades)

`--tax-exempt-mode listed_symbol`

- EU-listed symbol -> Приложение 13
- non-EU-listed symbol -> Приложение 5
- execution exchange is informational only (warnings for non-regulated/unknown)

`--tax-exempt-mode execution_exchange`

- non-EU-listed symbol -> Приложение 5
- EU-listed + EU-regulated execution -> Приложение 13
- EU-listed + non-regulated/unknown execution -> `REVIEW_REQUIRED` bucket (excluded from appendix totals)

## Review Workflow

If the active section header contains a `Review Status` column, the analyzer uses it as human review input.

### Trades

- empty: default mode logic applies
- `TAXABLE`: force `APPENDIX_5`
- `NON-TAXABLE`: force `APPENDIX_13`
- other value: warning + review required

### Interest

- analyzer fills only `Status` in the output
- if input has `Review Status`:
  - `TAXABLE` -> force taxable
  - `NON-TAXABLE` -> force non-taxable
  - `UNKNOWN` / `REVIEW-REQUIRED` -> force unknown/manual review
- empty -> keep automatic classification
- any other value -> warning + manual review required

### Dividends

- `Review Status` is a human-input override field; analyzer does not auto-fill it
- analyzer auto-fills `Status` (`TAXABLE` / `UNKNOWN`)
- if input has `Review Status`, it is honored:
  - `TAXABLE` -> keep/include as taxable
  - `NON-TAXABLE` -> exclude from declaration totals
- empty -> keep automatic classification
- any other value -> warning + manual review required
- for taxable rows, manual `Country` / `Amount (EUR)` values are used if present
- if manual values are empty, analyzer uses auto-derived values when possible
- known taxable descriptions: `Cash Dividend`, `Lieu Received`, `Credit Interest`
- unknown descriptions -> `Status=UNKNOWN` and manual review required

### Withholding Tax

- supports human `Review Status` override using the same rules
- `Review Status` is human-input; analyzer does not auto-fill it
- analyzer auto-fills `Status` (`TAXABLE` / `UNKNOWN`)
- for taxable rows, manual `Country` / `Amount (EUR)` are used if present
- if manual values are empty, analyzer auto-fills when it can
- expected values are `TAXABLE` / `NON-TAXABLE` (or empty)
- any other value is treated as invalid and triggers warning + manual review
- recognized routing:
  - dividend withholding rows (`Cash Dividend`) -> `Appendix 8`
  - credit-interest withholding rows -> `Appendix 9` (`Country=Ireland`, `ISIN` empty)
- credit-interest rows in `Withholding Tax` are enriched/informational for review workflow
- Appendix 9 paid-tax source of truth remains `Mark-to-Market Performance Summary` (`Withholding on Interest Received`)

Unknown or unresolved rows contribute to the global manual-check state.

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

## Trades Algorithm (Summary)

1. Parse instrument listings (`Stocks`, `Treasury Bills`) from `Financial Instrument Information`.
2. Process `Trades,Data` rows only.
3. Closing trade = `DataDiscriminator=Trade` and `Code` contains token `C`.
4. Attach immediate following `ClosedLot` rows.
5. Asset handling:
- `Stocks`, `Treasury Bills`: processed
- `Forex`: ignored for Appendix 5/13 totals, explicitly surfaced in output
- other category: fail
6. EUR conversion:
- Trade `Proceeds` and `Comm/Fee` at trade date
- ClosedLot `Basis` at closed-lot date
7. Basis/PnL:
- `Trade Basis (EUR) = -sum(ClosedLot Basis (EUR))`
- `Realized P/L (EUR) = Proceeds (EUR) + Basis (EUR) + Comm/Fee (EUR)`
8. Sale/Purchase price legs (closing rows):
- `cash_leg = proceeds_eur + comm_fee_eur`
- if `cash_leg >= 0`: `sale_price += abs(cash_leg)`, `purchase += abs(basis_eur)`
- else: `sale_price += abs(basis_eur)`, `purchase += abs(cash_leg)`

## Interest Processing (Appendix 6 / 9)

Source section:

- `Interest,Header/...`
- `Interest,Data/...`

Required columns:

- `Currency`, `Date`, `Description`, `Amount`

Totals rows are skipped if `Currency` starts with `Total`.

Type extraction from `Description`:

- `Credit Interest` -> taxable
- `IBKR Managed Securities (SYEP) Interest` -> taxable
- `Debit Interest` -> non-taxable
- `Borrow Fees` -> non-taxable
- unknown -> review required

Appendix 6 (code 603):

- includes taxable contributors
- may include `Other taxable (Review override)` when human review marks otherwise-unknown interest as `TAXABLE`
- output separates contributor subtotals and final declaration total

Appendix 9 (interest only):

- country-level gross is built from taxable `Credit Interest` rows (currently mapped to Ireland by default)
- paid foreign tax source: `Mark-to-Market Performance Summary` row where `Asset Category = Withholding on Interest Received`
- paid foreign tax = `abs(Mark-to-Market P/L Total)`
- allowable credit = `APPENDIX_9_ALLOWABLE_CREDIT_RATE * credit_interest_total_eur`
- recognized credit = `min(allowable_credit, paid_tax_abroad)`

`APPENDIX_9_ALLOWABLE_CREDIT_RATE` is a code constant (default `0.10`).

### Foreign Tax Credit Aggregation (Appendix 8 / 9)

Final credit fields are computed at country level from additive totals.

Additive values (safe to sum):

- gross income in EUR
- paid foreign tax in EUR

Final/non-additive values (must be computed after aggregation):

- allowable credit
- recognized credit
- Bulgarian tax (Appendix 8)
- tax due (Appendix 8)

Why this matters:

- `min()` is non-linear, so `sum(min(...))` is generally wrong.

Appendix 9 example:

- same country
- row A: gross `100`, foreign tax `15`
- row B: gross `100`, foreign tax `0`
- wrong row-wise (`10%` example): `min(15, 10) + min(0, 10) = 10`
- correct aggregated:
  - total gross `200`
  - total foreign tax `15`
  - allowable `20`
  - recognized `15`

Appendix 8 example:

- same country totals: gross `200`, foreign tax `15`
- Bulgarian tax = `DIVIDEND_TAX_RATE * gross` (default `5%`) => `10`
- recognized credit = `min(15, 10) = 10`
- tax due = `10 - 10 = 0`

Internal calculations keep full precision (`Decimal`). Rounding is applied only in final rendered output.

Future multi-analyzer note:

- the same rule still applies after cross-analyzer aggregation is introduced
- do not sum already-finalized recognized credits from separate analyzers for the same country
- merge additive country totals first, then apply final `min(...)` logic once

## Dividends Processing (Appendix 8 + Appendix 6 Lieu)

### Dividends section

Source:

- `Dividends,Header/...`
- `Dividends,Data/...`

Required columns:

- `Currency`, `Date`, `Description`, `Amount`

Classification by `Description`:

- contains `Cash Dividend` -> Appendix 8
- contains `Lieu Received` -> Appendix 6 (code 603 contributor)
- otherwise -> unknown/review required

Auto `Status` in this section:

- `Cash Dividend`, `Lieu Received`, `Credit Interest` -> `TAXABLE`
- anything else -> `UNKNOWN`

### ISIN and country derivation

For recognized dividend rows, ISIN is extracted from description, e.g. `TPR(US8760301072) ...`.

- country key = first 2 chars of ISIN
- country names come from an in-code full ISO alpha-2 mapping (`249` codes, English + Bulgarian)
- missing/invalid ISIN or unknown country code -> review required

### Dividend withholding section

Source:

- `Withholding Tax,Header/...`
- `Withholding Tax,Data/...`

Auto-routing to Appendix 8 withholding uses rows with `Description` containing `Cash Dividend`.

Ignored from dividend-withholding aggregation:

- non-dividend withholding rows (for example credit-interest withholding descriptions)
- aggregate rows where `Currency` starts with `Total`

Manual override note:

- when human review marks a withholding row as taxable and provides appendix/amount/country, those manual values are used for aggregation

For included rows:

- country is derived from ISIN (not suffix text)
- amount is converted to EUR
- declaration math uses absolute withheld amount
- credit-interest withholding rows are also enriched (`Appendix 9`, `Country=Ireland`, empty `ISIN`)

Auto `Status` in this section:

- `Cash Dividend`, `Lieu Received`, `Credit Interest` -> `TAXABLE`
- anything else -> `UNKNOWN` (manual review required)

### Appendix 8 math (by country)

For each country:

- gross dividend EUR = sum of Cash Dividend EUR
- foreign tax paid EUR = sum of absolute dividend-withholding EUR
- Bulgarian dividend tax = `DIVIDEND_TAX_RATE * gross` (computed after country aggregation)
- allowable credit = `min(foreign_tax_paid, Bulgarian_tax)` (computed after country aggregation)
- recognized credit = allowable credit
- tax due = `Bulgarian_tax - recognized_credit`

`DIVIDEND_TAX_RATE` is a code constant (default `0.05`).

Important:

- dividend withholding is used in Appendix 8 only
- Appendix 9 remains interest-only

### Appendix 8 Part III convention (code 8141, column 5)

Dividend rows in Appendix 8 are rendered under Part III, row `1.N`, with income code `8141`.

For column 5 (`Код за прилагане на метод за избягване на двойното данъчно облагане`), the analyzer uses an intentional practical filing convention:

- column 5 = `1` when foreign withholding tax amount is strictly greater than zero
- column 5 = `3` when foreign withholding tax amount is zero (or effectively missing/no withholding)

This is an explicit practical convention in the analyzer and is not a treaty lookup/detection mechanism.

Examples:

1. Dividend with foreign withholding `> 0`:
- foreign tax paid (EUR) = `7.00`
- column 5 = `1`

2. Dividend with foreign withholding `= 0`:
- foreign tax paid (EUR) = `0.00`
- column 5 = `3`
- full Bulgarian dividend tax (`DIVIDEND_TAX_RATE`, default `5%`) remains due locally

Tax-credit related Appendix 8 columns are currently filled from the formulas above (country-level aggregation):

- `Платен данък в чужбина`
- `Допустим размер на данъчния кредит`
- `Размер на признатия данъчен кредит`
- `Дължим данък, подлежащ на внасяне`

## How To Read The Modified CSV

The analyzer preserves row order and extends only these sections:

- `Trades`
- `Interest`
- `Dividends`
- `Withholding Tax`

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

### Added Dividends Columns

- `Country`
- `Amount (EUR)`
- `ISIN`
- `Appendix`
- `Status`
- `Review Status`

### Added Withholding Tax Columns

- `Country`
- `Amount (EUR)`
- `ISIN`
- `Appendix`
- `Status`
- `Review Status`

Re-run safety:

- if these derived columns already exist in input, analyzer does not add duplicate columns
- existing manual values are preserved and can be used for review overrides

## Tax Credit Debug Artifacts

Non-production diagnostics for country-level foreign-tax-credit math are written under:

- `output/ibkr/activity_statement/_tax_credit_debug/.../tax_credit_country_debug.json`

This report includes, per country:

- aggregated gross
- aggregated foreign tax paid
- correct aggregated credit values
- row-wise comparison values (`wrong_rowwise`)
- delta between correct and row-wise formulas

Use this only for verification. Declaration values come from the main analyzer outputs.

## Declaration TXT Output

The declaration text includes:

- Приложение 5 (trades)
- Приложение 13 (trades)
- Приложение 6 (interest + lieu contributors, code 603 total)
- Приложение 8 (Част III, ред 1.N; country-grouped dividends and withholding tax credit math)
- Приложение 9 (interest-only withholding credit flow)
- optional manual-check block when review is required
- sanity-check section (`PASS`/`FAIL` + artifact paths)
- mandatory Forex warning section
- evidence section (counts and diagnostics)

`нетен резултат (EUR)` is reported as `печалба - загуба`.

## Sanity Check Gate

After generating the modified CSV, the analyzer runs sanity checks in verification mode (`FX=1`) and writes artifacts under:

- `output/ibkr/activity_statement/_sanity_debug/...`

Artifacts:

- `ibkr_activity_modified_fx1_debug.csv`
- `sanity_report.json`

If sanity fails:

- run exits non-zero
- declaration TXT includes failure diagnostics and artifact paths

## Forex Behavior

Forex trades are excluded from Appendix 5/13 calculations in this implementation.

- no hard failure on Forex rows
- explicit warnings in declaration output
- manual review is required when Forex rows exist

## Errors (Fail Loudly)

- missing required sections or section headers
- missing required columns in active headers
- malformed date/decimal values
- FX conversion failures
- unsupported asset categories (outside allowed set + Forex special handling)
- closing trades without attached ClosedLot rows
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
