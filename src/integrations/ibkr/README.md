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
- empty -> keep automatic classification
- any other value -> warning + manual review required

### Dividends

- `Review Status` is a human-input override field; analyzer does not auto-fill it
- analyzer auto-fills `Status` (`TAXABLE` / `NON-TAXABLE` / `UNKNOWN`)
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
- analyzer auto-fills `Status` (`TAXABLE` / `NON-TAXABLE` / `UNKNOWN`)
- for taxable rows, manual `Country` / `Amount (EUR)` are used if present
- if manual values are empty, analyzer auto-fills when it can
- expected values are `TAXABLE` / `NON-TAXABLE` (or empty)
- any other value is treated as invalid and triggers warning + manual review
- recognized routing:
  - dividend withholding rows (`Cash Dividend`) -> `Appendix 8`
  - credit-interest withholding rows -> `Appendix 9` (`Country=Ireland`, `ISIN` empty)
- if taxable and `Appendix 9` rows exist in `Withholding Tax`, they are used for Appendix 9 paid-tax amount
- otherwise Appendix 9 paid-tax falls back to `Mark-to-Market Performance Summary`

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

- withholding source: `Mark-to-Market Performance Summary` row where `Asset Category = Withholding on Interest Received`
- paid foreign tax = `abs(Mark-to-Market P/L Total)`
- allowable credit = `APPENDIX_9_ALLOWABLE_CREDIT_RATE * credit_interest_total_eur`
- recognized credit = `min(allowable_credit, paid_tax_abroad)`

`APPENDIX_9_ALLOWABLE_CREDIT_RATE` is a code constant (default `0.10`).

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
- Bulgarian dividend tax = `DIVIDEND_TAX_RATE * gross`
- allowable credit = `min(foreign_tax_paid, Bulgarian_tax)`
- recognized credit = allowable credit
- tax due = `Bulgarian_tax - recognized_credit`

`DIVIDEND_TAX_RATE` is a code constant (default `0.05`).

Important:

- dividend withholding is used in Appendix 8 only
- Appendix 9 remains interest-only

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
