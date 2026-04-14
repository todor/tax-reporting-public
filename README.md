# Tax Reporting (Bulgaria / НАП)

Python project for Bulgarian annual tax reporting workflows.

The repository now includes:

- FX services (`bnb_fx`, `crypto_fx`)
- Binance analyzers
- IBKR activity statement analyzer (trades + interest + dividends)

Some areas are still intentionally phased and evolving (for example broader asset coverage and additional appendices).

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
- `src/integrations/`: integration packages (`binance`, `ibkr`)
- `src/services/bnb_fx/`: BNB CSV client + quarter cache + CLI
- `src/services/crypto_fx/`: crypto-to-EUR layer (pair resolution + Binance hourly pricing + CLI)
- `tests/test_imports.py`: import smoke tests
- `tests/services/bnb_fx/`: BNB FX tests
- `tests/services/crypto_fx/`: crypto FX tests
- `tests/integrations/binance/`: Binance analyzer tests
- `tests/integrations/ibkr/`: IBKR analyzer tests
- `output/`: output directory kept in git via `.gitkeep`
  Default analyzer outputs are written under this repo folder (for example `output/binance/futures/`).

## Integration Docs

- Binance integrations: [src/integrations/binance/README.md](src/integrations/binance/README.md)
- IBKR integrations: [src/integrations/ibkr/README.md](src/integrations/ibkr/README.md)

### Binance futures PnL cashflow analyzer

Pure realized-cashflow analyzer (no FIFO/carryover), based on Binance Futures PnL / Transaction History CSV:

```bash
PYTHONPATH=src pyenv exec python -m integrations.binance.futures_pnl_analyzer \
  --input path/to/binance_futures_pnl.csv \
  --tax-year 2025
```

### IBKR activity statement analyzer

```bash
PYTHONPATH=src pyenv exec python -m integrations.ibkr.activity_statement_analyzer \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol \
  --report-alias account1
```

IBKR appendix credit math note:

- Appendix 8 and Appendix 9 foreign-tax-credit fields are computed at country level from aggregated additive values (gross + paid foreign tax), then final `min(...)` logic is applied.
- IBKR also runs a minimal open-position reconciliation safety check (`Open Positions Summary` vs signed `Trades Order` quantities, by canonical instrument) and triggers manual review on mismatch/unmatched instruments.

## Crypto FX (`services.crypto_fx`)

`get_crypto_eur_rate(symbol_or_pair, timestamp, exchange, is_future=False)` resolves to a target symbol and returns EUR value for 1 unit of that symbol:

- Pair input: use QUOTE asset from exchange metadata (`binance` / `kraken`)
- Single symbol: use symbol itself
- Kraken symbols are normalized for Binance pricing (for example `XBT -> BTC`)
- `is_future=False`: pair detection uses spot metadata (`/api/v3/exchangeInfo` for Binance, `/0/public/AssetPairs` for Kraken)
- `is_future=True`: pair detection uses futures metadata (`/fapi/v1/exchangeInfo` for Binance, `/derivatives/api/v3/instruments` for Kraken)
- Fiat shortcuts:
  - `EUR` -> `1 EUR`
  - `USD` / `USDT` / `USDC` -> USD->EUR via `bnb_fx`
- Non-fiat symbols are priced via Binance hourly data on `<SYMBOL>USDT` (timestamp floored to hour), then converted USD->EUR via `bnb_fx`
- In futures mode, pricing tries Binance spot hourly close first, then falls back to Binance futures mark-price hourly candles (`/fapi/v1/premiumIndexKlines`)

CLI:

```bash
PYTHONPATH=src pyenv exec python -m services.crypto_fx.cli get-rate \
  --symbol-or-pair ALCHUSDT \
  --exchange binance \
  --is-future \
  --timestamp 2025-10-11T10:30:15Z
```
