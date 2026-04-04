# Tax Reporting (Bulgaria / НАП)

Minimal Python foundation for annual Bulgarian tax reporting workflows.

This repository currently contains foundational project setup plus a BNB FX caching module.
Tax reporting business logic and calculations are still intentionally not implemented.

## Setup

Use the pyenv environment set in `.python-version` (`tax-reporting`).

If you need to create it:

```bash
pyenv install -s 3.13.0
pyenv virtualenv 3.13.0 tax-reporting
pyenv local tax-reporting
```

Install dependencies:

```bash
pyenv exec python -m pip install -r requirements.txt
```

## Run

Run tests:

```bash
pyenv exec pytest
```

Run the entry point:

```bash
PYTHONPATH=src pyenv exec python -m main list-integrations
PYTHONPATH=src pyenv exec python -m main run --integration binance --year 2025 --input data/input.csv --output output
```

## BNB FX (`services.bnb_fx`)

What you can do:

- Get a historical FX quote by symbol and date.
- Auto-fetch and cache the whole quarter on cache miss.
- Preload cache for any date period.
- Preload cache for full years.
- Use either default cache location (`~/.cache/tax_reporting/bnb_fx`) or a custom directory.
- Always receive quotes as **EUR for 1 symbol unit**.
- If a requested date has no published rate, automatically use the closest previous available date.

Rate semantics:

- `rate` returned by `get_exchange_rate()` is always for `1` unit of the requested symbol.
- Example for USD: `rate=0.85` means `1 USD = 0.85 EUR`.
- For `EUR`, returned rate is always `1`.

### From Python code

Get one rate (auto-fetch quarter if needed):

```python
from services.bnb_fx import get_exchange_rate

rate = get_exchange_rate("USD", "2024-10-15")
print(rate.symbol, rate.date, rate.rate, rate.base_currency)
# rate is always "EUR for 1 symbol unit"
```

Build cache for an arbitrary period:

```python
from services.bnb_fx import build_cache

result = build_cache(["USD", "EUR"], "2024-01-01", "2024-12-31")
print(result.fetched_count, result.skipped_count, result.failed_count)
```

Build cache for full years:

```python
from services.bnb_fx import build_cache_for_symbols_and_years

result = build_cache_for_symbols_and_years(["USD"], [2023, 2024, 2025])
print(result.fetched_count, result.rows_written)
```

Use a custom cache directory:

```bash
PYTHONPATH=src pyenv exec python - <<'PY'
from services.bnb_fx import get_exchange_rate

rate = get_exchange_rate("USD", "2024-10-15", cache_dir="output/fx-cache")
print(rate.rate, rate.base_currency)
PY
```

Query multiple dates with automatic fallback to previous available day:

```python
from services.bnb_fx import get_exchange_rate

for d in ["2025-10-11", "2025-10-12"]:
    fx = get_exchange_rate("USD", d)
    print(d, "->", fx.date.isoformat(), fx.rate)  # requested -> effective
```

### From CLI

Build cache for period:

```bash
PYTHONPATH=src pyenv exec python -m services.bnb_fx.cli period \
  --symbols USD,EUR \
  --start-date 2024-01-01 \
  --end-date 2024-12-31
```

Build cache for full years:

```bash
PYTHONPATH=src pyenv exec python -m services.bnb_fx.cli years \
  --symbols USD \
  --years 2023,2024,2025
```

Build cache into a custom folder:

```bash
PYTHONPATH=src pyenv exec python -m services.bnb_fx.cli period \
  --symbols USD \
  --start-date 2024-01-01 \
  --end-date 2024-03-31 \
  --cache-dir output/fx-cache
```

Get one rate:

```bash
PYTHONPATH=src pyenv exec python -m services.bnb_fx.cli get-rate \
  --symbol USD \
  --date 2024-10-15
```

Get multiple dates:

```bash
PYTHONPATH=src pyenv exec python -m services.bnb_fx.cli get-rate \
  --symbol USD \
  --dates 2025-10-11,2025-10-12
```

`get-rate` output columns:

- `requested_date`
- `effective_date` (may be earlier if no rate on requested date)
- `symbol`
- `eur_for_1_symbol`

## Current Structure

- `src/main.py`: single CLI entry point
- `src/config.py`: central project paths
- `src/logging_config.py`: minimal logging setup
- `src/integrations/`: integration packages (currently `binance` placeholder)
- `src/services/bnb_fx/`: BNB CSV client + quarter cache + CLI
- `tests/test_imports.py`: minimal import smoke tests
- `tests/services/bnb_fx/`: BNB FX tests
- `output/`: output directory kept in git via `.gitkeep`
