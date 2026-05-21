# IBKR Activity Statement Analyzer

This module analyzes Interactive Brokers Activity Statement CSV files and prepares tax-oriented outputs for Bulgarian annual reporting in EUR.

Module:

- user-facing CLI: `uv run tax-reporting ibkr ...`

Internal IBKR module structure:

- `activity_statement_analyzer.py`: internal wrapper/orchestration flow
- `sections/`: business/source processing modules
  - `sections/trades.py`: Trades processing + Trade SubTotal/Total EUR aggregate population
  - `sections/interest.py`: Interest processing + Appendix 6/9 components
  - `sections/dividends.py`: Dividends processing + Appendix 8/6 components
  - `sections/tax_withholding.py`: Withholding Tax processing + Appendix 8/9 components
  - `sections/open_positions.py`: Open Positions processing + Part I aggregation + reconciliation checks
  - `sections/instruments.py`: Financial Instrument Information parsing/mapping + exchange/symbol resolution
  - `sections/income.py`: shared income parsing/classification helpers (interest/dividends)
  - `sections/sanity.py`: sanity gate + debug artifact generation
- `appendices/`: declaration-specific shaping/output
  - `appendices/aggregations.py`: Appendix 8/9 aggregation math + debug report payload generation
  - `appendices/csv_output.py`: multi-section CSV enrichment assembly + section width validation
  - `appendices/declaration_text.py`: declaration text assembly/output formatting
- `constants.py`: IBKR-specific constants, market/country maps, shared labels
- `models.py`: typed dataclasses/results/errors
- `shared.py`: truly shared infrastructure (header scoping, decimal/date parsing, FX conversion, row patching)

Conventions:

- preserve behavior and output compatibility first
- keep extraction pragmatic (no framework/pipeline abstractions)
- keep source/business parsing in `sections/*`
- keep declaration shaping in `appendices/*`
- keep only genuinely shared cross-cutting helpers in `shared.py`

## Quick Start

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol
```

Optional multi-account alias:

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement_account2.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol \
  --report-alias account2
```

Closed-world venue classification example (adds regulated overrides for this run):

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode execution_exchange \
  --eu-regulated-exchange TGATE \
  --eu-regulated-exchange "enext.fr,nyse"
```

Closed-world without adding extra regulated venues:

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode execution_exchange \
  --closed-world
```

## Input Report

Required report from Interactive Brokers:

- Custom Account Activity Statement
- Export format: CSV (machine input)
- Recommended companion export: PDF with the same settings, only for human review/audit

Export settings:

- `Default Sections`: `All`
- `Section Configurations`: set all flags to `No`

Important notes:

- The analyzer is designed to work from the IBKR Activity Statement CSV.
- Do not use the Dividend Report as analyzer input; dividends are read from the Activity Statement sections.
- Use one full report for the selected tax year. The `Statement -> Period` row must cover exactly January 1 through December 31 of `--tax-year`, for example `January 1, 2025 - December 31, 2025`.
- The account base currency must be EUR. The analyzer validates the `Account Information,Data,Base Currency,EUR` row before tax calculations start.
- Wrong or missing statement periods fail by default because tax reporting and SPB-8 can be incorrect.
- `--skip-period-validation` is for development/testing with partial reports only; do not use it for real tax reporting.

## CLI Options

- `--input`: IBKR Activity Statement CSV (required)
- `--tax-year`: target tax year, for example `2025` (required)
- `--tax-exempt-mode`: `listed_symbol` or `execution_exchange` (default: `listed_symbol`)
- `--appendix8-dividend-list-mode`: `company` (default) or `country`
- `--eu-regulated-exchange`: additional EU-regulated exchange code override; can be passed multiple times or comma-separated
- `--closed-world`: force closed-world exchange classification even without `--eu-regulated-exchange`
- `--report-alias`: optional alias added in output filenames
- `--skip-period-validation`: skip strict full-year Statement Period validation; development/testing only
- `--no-net-cfd-financing`: do not net CFD financing into Appendix 5; positive amounts go to Appendix 6 code 606 and negative amounts are skipped
- `--no-net-pil`: do not net negative Payment in Lieu adjustments into Appendix 5
- `--output-dir`: optional output root (default `output/ibkr/activity_statement`)
- `--cache-dir`: optional `bnb_fx` cache override
- `--display-currency {EUR,BGN}`: optional TXT rendering currency (calculation currency remains EUR)
- `--log-level`: logging level (default `INFO`)

## Scope

Processed sections:

- `Financial Instrument Information`
- `Trades`
- `Interest`
- `Dividends`
- `Withholding Tax`
- `Open Positions`
- `Mark-to-Market Performance Summary` (interest withholding source for Appendix 9)

FX source:

- existing `services.bnb_fx` (`get_exchange_rate`)

Outputs:

- modified CSV (multi-section preserved; only selected sections are extended)
- declaration text file (Bulgarian)
- sanity debug artifacts (`_sanity_debug`)

## СПБ-8

IBKR SPB-8 data is automatic where safe. The SPB-8 input CSV can be used to override or complete missing IBKR security quantities.

- Securities are derived from Open Positions and Financial Instrument Information.
- Beginning security quantities use Trades and supported Transfers for instruments still present in Open Positions.
- Supported transfer asset categories are `Stocks` and `Treasury Bills`.
- Transfers do not affect tax PnL or Appendix 5 because tax reporting uses IBKR Closed Lots.
- Unsupported transfer rows produce warnings and are skipped for SPB-8.
- Corporate actions such as stock splits, reverse splits, spin-offs, acquisitions, and mergers are detected but not handled yet.
- If Corporate Actions are present, type `04` start quantities are left empty unless supplied through `--spb8-input-file`.
- Review Corporate Actions manually because they may affect both SPB-8 and taxes.
- Unknown IBKR Activity Statement sections produce one consolidated Bulgarian warning for manual review.
- Cash is derived from Cash Report, not Net Asset Value.
- Cash uses `Starting Cash` as beginning balance and `Ending Cash` as ending balance.
- The `Currency` column is preserved as the original currency; values are not converted to EUR/BGN for SPB-8.
- The `Total` column is used as the amount.
- `Base Currency Summary` rows are ignored.
- Multiple cash currencies produce multiple SPB-8 cash lines.
- stdout status + output paths (`SUCCESS` / `MANUAL CHECK REQUIRED` / `ERROR`)

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
- `Mark-to-Market Performance Summary` rows (`Prior Quantity`) when available

Quantity parsing in this check is normalized for IBKR formatting:

- comma thousands separators are accepted (for example `1,001`)
- empty quantity is treated as `0`

Expected end quantity per instrument is computed as:

- `expected_open_qty = prior_qty_from_mtm + sum(trades_order_quantity_in_period)`

where `prior_qty_from_mtm` defaults to `0` if MTM prior quantity is not available.

All sides are normalized to the canonical instrument using the same Financial Instrument Information mapping logic (including symbol aliases such as `4GLD` / `4GLDd`).

Manual review is triggered when:

- open-position summary row cannot be matched to canonical instrument (`OPEN_POSITION_UNMATCHED_INSTRUMENT`)
- trades order row cannot be matched to canonical instrument (`TRADE_UNMATCHED_INSTRUMENT`)
- summed open quantity differs from summed signed order quantity beyond epsilon (`OPEN_POSITION_TRADE_QTY_MISMATCH`)

Scope/limits:

- this is a minimal safety net only
- no lot matching
- no timestamp matching
- no transfer/corporate-action parsing in this check; Transfers are used only for SPB-8 beginning quantity reconstruction

## Tax Modes (Trades)

`--tax-exempt-mode listed_symbol`

- EU-listed symbol -> Приложение 13
- non-EU-listed symbol -> Приложение 5
- execution exchange is informational only
- no per-row informational warnings are emitted for execution exchange in this mode
- a single global note is printed in diagnostics `Audit Data`: `In listed_symbol mode, execution exchange does not participate in classification and is informational only.`
- in open-world classification mode, unmapped listing venues still trigger manual review
- in closed-world classification mode, unmapped listing venues are treated as non-EU/non-regulated

`--tax-exempt-mode execution_exchange`

- two-stage decision:
  1. listing exchange is classified first
  2. if listing is `NON_EU` or `EU_NON_REGULATED` -> Приложение 5
  3. if listing is `EU_REGULATED` or `UNMAPPED` -> execution exchange is classified
- execution-stage result:
  - execution `EU_REGULATED` -> Приложение 13
  - execution `NON_EU` / `EU_NON_REGULATED` -> Приложение 5
  - execution `UNMAPPED` -> `REVIEW_REQUIRED`
  - execution invalid/unreadable -> `REVIEW_REQUIRED`

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

Classification classes:

- `EU_REGULATED`
- `EU_NON_REGULATED`
- `NON_EU`
- `UNMAPPED`
- `INVALID`

Built-in venue knowledge:

- EU regulated markets: explicit curated set in code
- EU non-regulated venues: explicit curated set (for example dark/MTF/SI style venues)
- known non-EU markets: explicit baseline set (includes major venues like `NYSE`, `NASDAQ`, `LSE`, `SWX`, etc.)
- placeholders/junk values (for example empty, `N/A`, `NULL`) are treated as `INVALID`
- Treasury Bills special case: when IBKR listing venue is empty for `Treasury Bills`, the analyzer treats the instrument as non-EU listed (not invalid listing exchange)

Classification mode:

- `OPEN_WORLD MODE`: active when no `--eu-regulated-exchange` is provided and `--closed-world` is not set
  - built-in knowledge is treated as partial
  - `UNMAPPED` stays review-worthy and is never silently trusted
- `CLOSED_WORLD MODE`: active when at least one `--eu-regulated-exchange` is provided or `--closed-world` is set
  - effective EU-regulated universe = built-in EU regulated + CLI-provided overrides
  - readable normalized venues do not remain `UNMAPPED`; they are forced to non-regulated classification unless explicitly regulated
  - `INVALID` is still review-worthy

CLI override behavior:

- CLI-provided regulated codes are normalized the same way as report codes
- overrides are case-insensitive after normalization and deduplicated
- override precedence is intentional:
  - CLI override > built-in EU non-regulated / built-in known non-EU
- example:
  - if built-in class for `TGATE` is EU non-regulated, `--eu-regulated-exchange TGATE` makes effective class EU-regulated for this run

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

## CFD and Payment in Lieu Handling

CFD trades are treated as derivative financial instruments, not as real shares/ETFs:

- realized CFD results are declared in `Приложение 5`, table 2, code `508`
- CFD positions are not declared in `Приложение 8`
- CFD positions are excluded from `СПБ-8`
- `Financial Instrument Information` rows for CFDs may use a different header and do not contain a reliable `Security ID` / ISIN
- the analyzer does not infer an underlying ISIN from the CFD symbol

IBKR may report CFD financing in the `Fees` section, for example `Long CFD Interest`, `Short CFD Interest`, or `CFD Financing`.

- By default, CFD financing / CFD interest is treated as part of CFD trading economics and is included in `Приложение 5`, code `508`.
- A positive amount increases the sale/proceeds side.
- A negative amount increases the acquisition/cost side by absolute value.
- With `--no-net-cfd-financing`, netting is disabled: positive amounts are declared in `Приложение 6`, code `606`, and negative amounts are not included automatically.
- Generic debit interest, margin interest, borrow fees, market data, subscription, ADR, snapshot, and wire fees are not treated as CFD financing unless the description explicitly contains CFD interest/financing wording.

IBKR may report `Payment in Lieu of Dividend (Ordinary Dividend)` in the `Dividends` section.

- PIL is not treated as a real dividend and is not declared in `Приложение 8`.
- PIL is not assumed to be CFD-related; it may come from short stock positions, CFD exposure, stock lending, rehypothecation, or other synthetic/substitute mechanisms.
- By default, negative PIL is treated as a cost adjustment for short/synthetic exposure and is included in `Приложение 5`, code `508`.
- With `--no-net-pil`, negative PIL is not included automatically.
- Positive PIL is always declared in `Приложение 6`, code `606`, because the exact source cannot be determined reliably from the IBKR Activity Statement.

CFD financing and PIL adjustments are treated according to their economic relationship with CFD/short/synthetic exposure. Because there is no explicit public guidance for synthetic broker cashflow adjustments, the tool applies a practical defensible approach and provides conservative modes through the CLI flags above.

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

Appendix 8 and Appendix 9 use different aggregation levels in this analyzer:

- Appendix 8 (dividends, code `8141`): final credit math is computed per company first.
- Appendix 9 (interest): final credit math remains country-level.

Why this matters:

- `min()` is non-linear, so applying it at the wrong grouping level changes declaration values.

Appendix 8 example (same country, two companies):

- Company A: gross `100`, foreign tax `15` -> recognized credit `min(15, 5)=5`
- Company B: gross `100`, foreign tax `0` -> recognized credit `min(0, 5)=0`
- correct total credit from company rows = `5`
- wrong country-level recomputation would be `min(15, 10)=10` (not used by analyzer output)

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

Internal calculations keep full precision (`Decimal`). Rounding is applied only in final rendered output.

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

### Company identity for Appendix 8

For Appendix 8 dividend rows, company/payer identity is resolved from:

1. symbol parsed from dividend description
2. symbol matching via existing Financial Instrument Information mapping logic
3. `Financial Instrument Information -> Description` as payer name

If mapping cannot be resolved confidently, the analyzer keeps processing with a deterministic fallback payer label and marks the run for manual review.

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
- broker-sign amounts are netted for Appendix 8: negative `Withholding Tax` means tax withheld, positive means returned/reversed tax
- final creditable foreign tax is clamped at zero, so positive-only reversals do not create negative credit
- manual `Amount (EUR)` overrides are treated as already-normalized tax-paid values
- credit-interest withholding rows are also enriched (`Appendix 9`, `Country=Ireland`, empty `ISIN`)

Auto `Status` in this section:

- `Cash Dividend`, `Lieu Received`, `Credit Interest` -> `TAXABLE`
- anything else -> `UNKNOWN` (manual review required)

### Appendix 8 math (company-first)

For each company (payer):

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
- country-mode output (see below) is presentation-only aggregation over already computed company rows
- country mode does not recompute `min(...)` at country level

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

Tax-credit related Appendix 8 columns are currently filled from the formulas above (company-level computation):

- `Платен данък в чужбина`
- `Допустим размер на данъчния кредит`
- `Размер на признатия данъчен кредит`
- `Дължим данък, подлежащ на внасяне`

### Appendix 8 output modes

`--appendix8-dividend-list-mode company` (default):

- one Appendix 8 row per company
- payer name comes from Financial Instrument Description

`--appendix8-dividend-list-mode country`:

- starts from already computed company rows
- aggregates by `country + method_code`
- sums numeric columns only (`gross`, `foreign tax`, `allowable`, `recognized`, `tax due`)
- does not recompute credit formula at country level
- payer label is the generic text:
  - `Различни чуждестранни дружества (чрез Interactive Brokers)`

Why country mode groups by method code:

- to keep column 5 truthful when one country has both:
  - rows with foreign withholding (`method=1`)
  - rows without foreign withholding (`method=3`)
- these two buckets are emitted as separate country rows and are never collapsed together

Country-mode examples:

1. Same country, both companies with withholding `> 0`:
- both are method `1`
- country mode merges into one row for that country/method `1`

2. Same country, mixed withholding:
- company A withholding `> 0` -> method `1`
- company B withholding `= 0` -> method `3`
- country mode emits two rows for that country (`method 1` and `method 3`)

## Appendix 8 Part I (Open Positions)

Appendix 8 Part I is generated from `Open Positions` summary rows only:

- section: `Open Positions`
- row type: `Data`
- discriminator: `Summary`

Current scope:

- supported asset categories: `Stocks` and `Treasury Bills`
- all supported holdings are reported under `Акции`
- `Дялове` are not emitted in this version
- any other Open Positions asset category is flagged for manual review

Fail-loud rules for supported Open Positions Part I rows:

- symbol must map to Financial Instrument
- ISIN/country must be resolvable
- `Summary Quantity` must be numeric
- `Cost Basis` must be numeric
- `Currency` must be present/non-empty (for EUR conversion)

If any of the above fails, analyzer exits with error (no silent skip).

Country derivation:

- symbol is resolved via existing Financial Instrument mapping
- ISIN is read from `Financial Instrument Information`
- country is derived from ISIN prefix (first 2 chars)

Aggregation:

- one Part I row per country
- quantity (`Брой`) is summed per country
- cost basis in original currency is summed per country
- acquisition date is fixed to `31.12.<tax_year>` for all Part I rows

FX for Part I:

- cost basis EUR is calculated using FX on `31.12.<tax_year>`
- output column label is `В EUR` (intentional current convention)

Declaration note:

- declaration output includes:
  - `Забележка:`
  - `Данните в Приложение 8, Част I са декларативни.`
  - `Не се изисква прикачване на файл към декларацията.`
  - `Запазете отчети (напр. broker statements) за целите на евентуална проверка от НАП.`

## How To Read The Modified CSV

The analyzer preserves row order and extends only these sections:

- `Trades`
- `Interest`
- `Dividends`
- `Withholding Tax`
- `Open Positions`

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

### Added Open Positions Columns

- `Country`
- `Cost Basis (EUR)`

They are filled for summary rows when symbol/ISIN/currency data is available.

Re-run safety:

- if these derived columns already exist in input, analyzer does not add duplicate columns
- existing manual values are preserved and can be used for review overrides

## Tax Credit Debug Artifacts

Non-production diagnostics for Appendix 8/9 tax-credit math are written under:

- `output/ibkr/activity_statement/_tax_credit_debug/.../tax_credit_country_debug.json`

This report includes:

- Appendix 8 company rows (declaration math source)
- Appendix 8 output rows (after mode-specific presentation aggregation)
- Appendix 8 country debug diagnostics:
  - `recognized_credit_sum_company`
  - `recognized_credit_wrong_country_recomputed`
  - delta between correct company-sum result and wrong country-level recomputation
- Appendix 9 country diagnostics

Use this only for verification. Declaration values come from the main analyzer outputs.

## Declaration TXT Output

The declaration text includes:

- Приложение 5 (trades)
- Приложение 13 (trades)
- Приложение 6 (interest + lieu contributors, code 603 total)
- Приложение 8:
  - Част I, Акции, ред 1.N (from Open Positions summary rows)
  - Част III, ред 1.N (`company` mode by default, optional `country` mode)
- Приложение 9 (interest-only withholding credit flow)
- optional manual-check block when review is required
- sanity-check details (`PASS`/`FAIL` + artifact paths) in diagnostics
- conditional Forex warning section (shown only when Forex rows require manual check)
- diagnostics evidence section (counts and technical details)
- diagnostics `Audit Data` section with encountered venue groups:
  - EU-регулирани пазари, открити в отчета
  - EU нерегулирани пазари, открити в отчета
  - Не-EU пазари, открити в отчета
  - Неразпознати пазари, открити в отчета
  - Невалидни/нечетими стойности за пазар, открити в отчета
  - active classification mode + CLI exchange overrides used in the run
  - in `listed_symbol` mode, diagnostics `Audit Data` contains a single global note that execution exchange is informational-only
  - venue scope is limited to in-tax-year closing `Trades` rows (Forex, non-closing rows, and Open Positions are excluded)
  - `listed_symbol` mode: only listing venues are included (execution venues are not used for routing)
  - `execution_exchange` mode: listing venues are always included; execution venues are included only for rows where listing is `EU_REGULATED` or `UNMAPPED`
  - execution-mode discovery exception: when listing is invalid/missing but execution is readable, execution is still surfaced in audit buckets for transparency (tax routing remains review-required)

`нетен резултат: <amount> EUR` is reported as `печалба - загуба`.

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
- explicit warnings in declaration output when Forex rows require manual check
- `Review Status=TAXABLE` and `Review Status=NON-TAXABLE` apply only to the current Forex Trade row
- `Review Status=NON-TAXABLE` on a Forex Trade row means the row is ignored without manual-check requirement
- `Review Status=TAXABLE` means the row requires manual review because taxable Forex is not supported in this version
- `Review Status=TAXABLE-FROM-HERE` applies `TAXABLE` to the current Forex Trade row and to subsequent Forex Trade rows with empty `Review Status`
- `Review Status=NON-TAXABLE-FROM-HERE` applies `NON-TAXABLE` to the current Forex Trade row and to subsequent Forex Trade rows with empty `Review Status`
- a later explicit `TAXABLE` or `NON-TAXABLE` overrides the inherited Forex status only for that row
- a later `TAXABLE-FROM-HERE` or `NON-TAXABLE-FROM-HERE` changes the inherited Forex status from that row onward
- inherited Forex status is Forex-only and does not affect stocks, CFDs, dividends, interest, withholding tax, or other sections
- if a Forex Trade row has empty `Review Status` before any `*-FROM-HERE` directive, manual check is required

Short CSV-style example:

```csv
Trades,Header,Asset Category,Currency,Symbol,Date/Time,Exchange,Code,Proceeds,DataDiscriminator,Basis,Review Status
Trades,Data,Forex,USD,EUR.USD,2025-03-01 10:00:00,IDEALPRO,C,10,Trade,,NON-TAXABLE-FROM-HERE
Trades,Data,Forex,USD,EUR.USD,2025-03-02 10:00:00,IDEALPRO,C,11,Trade,,
Trades,Data,Forex,USD,EUR.USD,2025-03-03 10:00:00,IDEALPRO,C,12,Trade,,TAXABLE
Trades,Data,Forex,USD,EUR.USD,2025-03-04 10:00:00,IDEALPRO,C,13,Trade,,
Trades,Data,Forex,USD,EUR.USD,2025-03-05 10:00:00,IDEALPRO,C,14,Trade,,TAXABLE-FROM-HERE
Trades,Data,Forex,USD,EUR.USD,2025-03-06 10:00:00,IDEALPRO,C,15,Trade,,
```

In this example, March 1, 2, and 4 are treated as `NON-TAXABLE`; March 3 is
explicitly `TAXABLE` for that row only; March 5 and 6 are `TAXABLE`.

## Errors (Fail Loudly)

- missing required sections or section headers
- missing required columns in active headers
- malformed date/decimal values
- FX conversion failures
- unsupported asset categories (outside allowed set + Forex special handling)
- closing trades without attached ClosedLot rows
- incomplete Activity Statements with closing trades but no `Trades/Data/ClosedLot` rows at all; export a full IBKR Activity Statement with `Trades -> Closed Lots / Lot Details`
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
