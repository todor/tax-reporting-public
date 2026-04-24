# Finexify Fund Analyzer

Entry point (user-facing):

- `PYTHONPATH=src pyenv exec python -m report_analyzer finexify ...`


## Architecture

Finexify follows parse/map/analyze/output layering:

- `finexify_parser.py`: raw CSV parsing and schema validation
- `finexify_to_ir.py`: Finexify rows -> shared fund IR rows
- `report_analyzer.py`: orchestration only (map -> generic analyze -> shared outputs)

All accounting/tax logic runs in `integrations.fund.shared`.

## Input CSV

Required columns:

- `Type`
- `Cryptocurrency`
- `Amount`
- `Date`
- `Source`

Other columns are ignored.

Supported `Type` values:

- `Deposit`
- `Balance`
- `Withdraw`

Unknown types are warned and excluded.
Empty rows are ignored.

Date formats:

- date-only: `YYYY-MM-DD`
- timestamp: ISO forms like `YYYY-MM-DDTHH:MM:SS.sssZ`

Invalid dates fail fast (for example `2025-21-01`).
Withdraw above available balance fails fast.

## Finexify Mapping Rules

- `Withdraw` -> IR `withdraw` (crypto, positive amount)
- `Balance` -> IR `profit` delta (not raw balance)

`Deposit` uses `Source`:

- if `Source=Investment` -> IR `deposit` (crypto, positive amount)
- otherwise -> IR `profit` with `Amount` equal to input `Amount` (signed)

Balance-to-profit delta formula:

- Before row: `current_total_native = D_native + P_native`
- For balance snapshot `B_native`: `profit_delta = B_native - current_total_native`
- Emit IR `profit` with `Amount=profit_delta`

## Processing Order

Order normalization before mapping:

- if source rows are already monotonic ascending by `Date`, keep original order
- if source rows are monotonic descending, reverse once
- if neither ascending nor descending, sort by date only (stable)

Important same-day rule:

- when at least one row has date-only format (e.g. `2025-12-01`), comparisons use date component only
- therefore `2025-12-01` and `2025-12-01T07:52:21.879Z` are treated as same-day ties and their original order is preserved

## Outputs

Default output directory:

- `output/finexify/`

Generated files:

- `<input_stem>_modified.csv` (enriched IR CSV)
- `<input_stem>_declaration.txt`
- `<input_stem>_state_end_<tax_year>.json`

Enriched CSV includes additional audit columns:

- `Amount (EUR)`
- `Balance`
- `Balance (EUR)`
- `Deposit to Date (EUR)`

Meaning:

- `Balance` / `Balance (EUR)` are post-row portfolio balance views.
- `Deposit to Date (EUR)` is cumulative gross deposits in EUR (historical per-deposit conversion), used for auditability of purchase-price math.

Declaration TXT contains:

- manual-check block
- `Приложение 5 / Таблица 2`
- informational counters (`processed_rows`, `ignored_rows`, warnings)

Only `withdraw` rows contribute to Appendix 5 totals.

## Carry-forward State

You can pass prior year-end state:

```bash
PYTHONPATH=src pyenv exec python -m report_analyzer finexify \
  --input "path/to/finexify_2026.csv" \
  --tax-year 2026 \
  --opening-state-json output/finexify/<state_end_2025>.json
```

## CLI

```bash
PYTHONPATH=src pyenv exec python -m report_analyzer finexify \
  --input "path/to/finexify.csv" \
  --tax-year 2025
```

Options:

- `--input` (required)
- `--tax-year` (required)
- `--opening-state-json` (optional)
- `--output-dir` (optional, default `output/finexify`)
- `--cache-dir` (optional FX cache override)
- `--log-level` (optional)

CLI stdout policy:

- `STATUS: SUCCESS` or `STATUS: MANUAL CHECK REQUIRED` on successful runs
- `STATUS: ERROR` on failure
- output file paths only (no duplicated diagnostic counters)
