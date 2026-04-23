# Binance Integrations

This folder contains Binance-specific analyzers used for tax reporting.

## Futures PnL Cashflow Analyzer

Module:

- `integrations.crypto.binance.futures_pnl_analyzer`

Purpose:

- Process Binance Futures **PnL / Transaction History** CSV rows as realized cashflow only.
- No FIFO, no position tracking, no carryover.
- Use only operation rows:
- `Fee`
- `Funding Fee`
- `Realized Profit and Loss`

Input CSV header (required columns):

- `User ID`
- `Time`
- `Account`
- `Operation`
- `Coin`
- `Change`
- `Remark`

`Time` parsing accepts:

- ISO timestamp (with or without timezone; naive timestamps are treated as UTC)
- `YYYY-MM-DD HH:MM:SS`
- `YYYY/MM/DD HH:MM:SS`
- `YY-MM-DD HH:MM:SS`
- `YY/MM/DD HH:MM:SS`

Important currency rule:

- Relevant rows must have `Coin=BNFCR`.
- Analyzer treats `1 BNFCR = 1 USD`.
- Any relevant row with a different coin fails fast with row details.

EUR conversion:

- Per-row USD->EUR conversion uses existing `services.bnb_fx`.
- Conversion is applied on each row timestamp date.
- `services.bnb_fx` may use the closest previous business day if the exact date has no published BNB rate.

## Algorithm (Exact)

The analyzer preserves original input row order. It does not sort.

Step 1: Validate CSV schema

- Required columns must exist.
- Missing columns fail immediately.

Step 2: Select relevant rows

- Keep only rows where `Operation` is one of:
- `Fee`
- `Funding Fee`
- `Realized Profit and Loss`
- All other operations are ignored and counted in `ignored_rows`.

Step 3: Filter by tax year

- Parse `Time`.
- Keep relevant rows whose `Time.year == tax_year`.
- Relevant rows outside the selected year are ignored and counted in `ignored_rows`.

Step 4: Validate currency for each kept row

- `Coin` must be `BNFCR`.
- If not, fail fast with row number, `Time`, `Operation`, `Coin`, and `Change`.

Step 5: Convert each kept row

- `change_bnfcr = Decimal(Change)` with original sign preserved.
- `amount_usd = change_bnfcr` because `1 BNFCR = 1 USD`.
- `fx_usd_eur_rate = get_exchange_rate("USD", row_date).rate`
- `amount_eur = amount_usd * fx_usd_eur_rate`

Step 6: Classify by sign only

- If `amount_usd > 0`:
- profit row
- If `amount_usd < 0`:
- loss row
- If `amount_usd == 0`:
- neutral row

No special behavior by `Operation` beyond row selection.

Step 7: Aggregate totals

- `profit_usd = sum(amount_usd where amount_usd > 0)`
- `loss_usd = sum(abs(amount_usd) where amount_usd < 0)`
- `profit_eur = sum(amount_eur where amount_usd > 0)`
- `loss_eur = sum(abs(amount_eur) where amount_usd < 0)`
- `sale_price_usd = profit_usd`
- `purchase_price_usd = loss_usd`
- `sale_price_eur = profit_eur`
- `purchase_price_eur = loss_eur`
- `net_result_usd = profit_usd - loss_usd`
- `net_result_eur = profit_eur - loss_eur`

## Per-row Detailed CSV Logic

Output columns:

- `original_row_number`
- `user_id`
- `time`
- `account`
- `operation`
- `coin`
- `change_bnfcr`
- `amount_usd`
- `fx_usd_eur_rate`
- `amount_eur`
- `profit_usd`
- `loss_usd`
- `profit_eur`
- `loss_eur`
- `sale_price_usd`
- `purchase_price_usd`
- `sale_price_eur`
- `purchase_price_eur`
- `remark`

For `Change > 0`:

- `profit_usd = amount_usd`
- `loss_usd = 0`
- `profit_eur = amount_eur`
- `loss_eur = 0`
- `sale_price_usd = amount_usd`
- `purchase_price_usd = 0`
- `sale_price_eur = amount_eur`
- `purchase_price_eur = 0`

For `Change < 0`:

- `profit_usd = 0`
- `loss_usd = abs(amount_usd)`
- `profit_eur = 0`
- `loss_eur = abs(amount_eur)`
- `sale_price_usd = 0`
- `purchase_price_usd = abs(amount_usd)`
- `sale_price_eur = 0`
- `purchase_price_eur = abs(amount_eur)`

For `Change == 0`:

- all tax columns are `0`

## How To Reproduce Manually

You can reproduce the output in a spreadsheet exactly:

1. Filter rows where `Operation` is one of `Fee`, `Funding Fee`, `Realized Profit and Loss`.
2. Keep only rows from the selected tax year.
3. Validate `Coin=BNFCR` for every kept row.
4. Parse `Change` as signed decimal.
5. Set `amount_usd = Change`.
6. Get USD->EUR BNB rate for each row date.
7. Compute `amount_eur = amount_usd * fx_usd_eur_rate`.
8. For each row, split to profit/loss columns by sign of `amount_usd` only.
9. Sum row columns to produce summary totals.

CLI:

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.binance.futures_pnl_analyzer \
  --input path/to/binance_futures_pnl.csv \
  --tax-year 2025
```

CLI options:

- `--input` (required)
- `--tax-year` (required)
- `--output-dir` (optional, default `output/binance/futures`)
- `--cache-dir` (optional BNB FX cache override)
- `--log-level` (optional, default `INFO`)

Optional:

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.binance.futures_pnl_analyzer \
  --input path/to/binance_futures_pnl.csv \
  --tax-year 2025 \
  --output-dir output/binance/futures \
  --cache-dir ~/.cache/tax_reporting/bnb_fx
```

Outputs:

- `futures_pnl_detailed_<year>.csv`
- `futures_pnl_tax_<year>.txt`
- `futures_pnl_summary_<year>.json`

Default output location is under this repository:

- `tax-reporting/output/binance/futures/`

Stdout is intentionally concise and prints only:

- status: `SUCCESS` or `ERROR`
- output file paths

## Output Files

`futures_pnl_detailed_<year>.csv`

- Row-by-row processed dataset with FX and tax columns.

`futures_pnl_tax_<year>.txt`

- Human-readable filing helper.
- Structured as:
- `Приложение 5`
- `Таблица 2`
- `Информативни`
- `Таблица 2` rows:
- `данъчна година`
- `Продажна цена (EUR) - код 5082`
- `Цена на придобиване (EUR) - код 5082`
- `Печалба (EUR) - код 5082`
- `Загуба (EUR) - код 5082`
- `Информативни` includes:
- `Нетен резултат (EUR)`
- Includes USD totals and processing counters.

`futures_pnl_summary_<year>.json`

- Machine-readable totals with explicit currency suffixes:
- `sale_price_usd`
- `purchase_price_usd`
- `profit_usd`
- `loss_usd`
- `sale_price_eur`
- `purchase_price_eur`
- `profit_eur`
- `loss_eur`
- `net_result_usd`
- `net_result_eur`
- Plus `processed_rows` and `ignored_rows`.

## Validation and Failure Modes

The analyzer fails with clear error messages when:

- required CSV columns are missing
- `Change` is not a valid decimal
- relevant row has `Coin != BNFCR`
- `Time` is not parseable

Error messages include row number.
