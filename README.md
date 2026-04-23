# Tax Reporting (Bulgaria / НАП)

Python project for Bulgarian annual tax reporting workflows.

The repository now includes:

- FX services (`bnb_fx`, `crypto_fx`)
- Binance analyzers
- Coinbase report analyzer (spot transactions mapped to shared crypto IR engine)
- Kraken report analyzer (spot ledger mapped to shared crypto IR engine)
- Finexify fund analyzer (fund events mapped to shared fund IR engine)
- P2P analyzers:
- Afranga (payer-level Appendix 6 extraction + withheld-tax carry-through)
- Estateguru (aggregate Appendix 6 mapping)
- Lendermarket (aggregate Appendix 6 mapping)
- Iuvo (aggregate Appendix 6 mapping)
- Robocash (aggregate Appendix 6 mapping)
- Bondora Go & Grow (aggregate Appendix 6 mapping)
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

Current external dependencies in `requirements.txt`:

- `requests` (FX services HTTP clients)
- `pypdf` (machine-generated PDF text extraction for P2P analyzers)
- `pytest` (test runner)

Dependency policy:

- `requirements.txt` is the single install list for both runtime and repository tests.
- third-party imports currently used by the codebase are limited to the three packages above.

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

Notes:

- `python -m main list-integrations` lists available integrations.
- `python -m main run ...` is a lightweight router that points to the dedicated analyzer CLIs below.

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
- `src/integrations/`: integration packages (`crypto`, `fund`, `p2p`, `ibkr`)
- `src/integrations/crypto/shared/`: shared crypto IR models, generic analyzer, shared outputs/runtime helpers
- `src/integrations/crypto/coinbase/`: Coinbase parser, mapper, and orchestrator
- `src/integrations/crypto/kraken/`: Kraken parser, mapper, and orchestrator
- `src/integrations/crypto/binance/`: Binance crypto analyzers
- `src/integrations/fund/shared/`: shared fund IR models, generic analyzer, and outputs/state helpers
- `src/integrations/fund/finexify/`: Finexify parser, mapper, and orchestrator
- `src/integrations/p2p/shared/`: shared P2P Appendix 6 result model and renderer
- `src/integrations/p2p/afranga/`: Afranga PDF parser and orchestrator
- `src/integrations/p2p/estateguru/`: Estateguru PDF parser and orchestrator
- `src/integrations/p2p/lendermarket/`: Lendermarket PDF parser and orchestrator
- `src/integrations/p2p/iuvo/`: Iuvo PDF parser and orchestrator
- `src/integrations/p2p/robocash/`: Robocash PDF parser and orchestrator
- `src/integrations/p2p/bondora_go_grow/`: Bondora Go & Grow PDF parser and orchestrator
- `src/integrations/ibkr/activity_statement_analyzer.py`: IBKR analyzer facade/orchestrator
- `src/integrations/ibkr/sections/`: IBKR business/source processing modules (`trades`, `interest`, `dividends`, `tax_withholding`, `open_positions`, `instruments`, etc.)
- `src/integrations/ibkr/appendices/`: IBKR declaration shaping/output modules
- `src/integrations/ibkr/constants.py`: IBKR domain constants and country maps
- `src/integrations/ibkr/models.py`: IBKR typed models/errors/result structures
- `src/integrations/ibkr/shared.py`: shared IBKR parsing/matching/conversion helpers
- `src/services/bnb_fx/`: BNB CSV client + quarter cache + CLI
- `src/services/crypto_fx/`: crypto-to-EUR layer (pair resolution + Binance hourly pricing + CLI)
- `src/services/pdf_reader.py`: shared machine-generated PDF text extraction utility
- `tests/test_imports.py`: import smoke tests
- `tests/services/bnb_fx/`: BNB FX tests
- `tests/services/crypto_fx/`: crypto FX tests
- `tests/integrations/crypto/binance/`: Binance analyzer tests
- `tests/integrations/crypto/`: shared crypto IR/analyzer tests
- `tests/integrations/crypto/coinbase/`: Coinbase analyzer tests
- `tests/integrations/crypto/kraken/`: Kraken analyzer tests
- `tests/integrations/fund/`: shared and Finexify fund analyzer tests
- `tests/integrations/p2p/`: shared + platform-specific P2P analyzer tests
- `tests/integrations/ibkr/`: IBKR tests (organized by `sections/` and `appendices/`)
- `output/`: output directory kept in git via `.gitkeep`
  Default analyzer outputs are written under this repo folder (for example `output/binance/futures/`).

## Code Structure And Conventions

- Keep analyzer behavior stable first: refactors must preserve outputs, labels, calculations, and review semantics.
- Simpler analyzers may stay single-file; more complex analyzers can be split when it clearly improves readability and safety.
- For IBKR, keep the analyzer facade/orchestrator thin and explicit, and move cohesive parsing/calculation/output logic into IBKR-local modules.
- Put new source/business logic in the most relevant existing module (do not append to a giant function/file).
- Keep appendix builders focused on declaration shaping and final presentation; keep source parsing/matching logic in source-oriented modules.
- Reuse existing helpers when there is real duplication; avoid speculative abstractions or framework-like pipelines.
- Cross-analyzer consistency should come from a stable result/output contract, not from forcing identical internal folder layouts.

## Integration Docs

- Binance integrations: [src/integrations/crypto/binance/README.md](src/integrations/crypto/binance/README.md)
- Coinbase integrations: [src/integrations/crypto/coinbase/README.md](src/integrations/crypto/coinbase/README.md)
- Kraken integrations: [src/integrations/crypto/kraken/README.md](src/integrations/crypto/kraken/README.md)
- Shared crypto engine: [src/integrations/crypto/shared/README.md](src/integrations/crypto/shared/README.md)
- Fund integrations: [src/integrations/fund/README.md](src/integrations/fund/README.md)
- Shared fund engine: [src/integrations/fund/shared/README.md](src/integrations/fund/shared/README.md)
- Finexify fund analyzer: [src/integrations/fund/finexify/README.md](src/integrations/fund/finexify/README.md)
- P2P integrations: [src/integrations/p2p/README.md](src/integrations/p2p/README.md)
- Shared P2P engine: [src/integrations/p2p/shared/README.md](src/integrations/p2p/shared/README.md)
- Afranga P2P analyzer: [src/integrations/p2p/afranga/README.md](src/integrations/p2p/afranga/README.md)
- Estateguru P2P analyzer: [src/integrations/p2p/estateguru/README.md](src/integrations/p2p/estateguru/README.md)
- Lendermarket P2P analyzer: [src/integrations/p2p/lendermarket/README.md](src/integrations/p2p/lendermarket/README.md)
- Iuvo P2P analyzer: [src/integrations/p2p/iuvo/README.md](src/integrations/p2p/iuvo/README.md)
- Robocash P2P analyzer: [src/integrations/p2p/robocash/README.md](src/integrations/p2p/robocash/README.md)
- Bondora Go & Grow P2P analyzer: [src/integrations/p2p/bondora_go_grow/README.md](src/integrations/p2p/bondora_go_grow/README.md)
- IBKR integrations: [src/integrations/ibkr/README.md](src/integrations/ibkr/README.md)

### Binance futures PnL cashflow analyzer

Pure realized-cashflow analyzer (no FIFO/carryover), based on Binance Futures PnL / Transaction History CSV:

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.binance.futures_pnl_analyzer \
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

Optional venue override inputs (activates closed-world venue classification for this run):

```bash
PYTHONPATH=src pyenv exec python -m integrations.ibkr.activity_statement_analyzer \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode execution_exchange \
  --eu-regulated-exchange TGATE \
  --eu-regulated-exchange "ENEXT.FR,NYSE"
```

Closed-world without adding extra regulated exchanges:

```bash
PYTHONPATH=src pyenv exec python -m integrations.ibkr.activity_statement_analyzer \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode execution_exchange \
  --closed-world
```

IBKR appendix credit math note:

- Appendix 8 credit math is computed per company first (source-of-truth calculation), then optionally presented aggregated by country in country-list mode.
- Appendix 9 credit math remains country-level.
- IBKR also runs a minimal open-position reconciliation safety check (`Open Positions Summary` vs signed `Trades Order` quantities, by canonical instrument) and triggers manual review on mismatch/unmatched instruments.
- IBKR venue classification supports:
  - open-world mode (default): unmapped venues stay review-worthy
  - closed-world mode (activated by `--eu-regulated-exchange` or `--closed-world`): built-in EU regulated + CLI overrides become the effective regulated universe for this run
  - in closed-world mode, readable normalized venues are forced to non-regulated classification unless explicitly regulated (only invalid/garbled values remain review-worthy)
- IBKR declaration output includes `Одитни данни` with encountered venue categories and active classification mode.
- In `listed_symbol` mode, execution exchange is documented once as a global informational note (no per-row informational noise).

### Coinbase report analyzer

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025
```

### Finexify fund analyzer

```bash
PYTHONPATH=src pyenv exec python -m integrations.fund.finexify.report_analyzer \
  --input "path/to/finexify.csv" \
  --tax-year 2025
```

### Afranga P2P analyzer

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.afranga.report_analyzer \
  --input "path/to/afranga_statement.pdf" \
  --tax-year 2025
```

Notes:

- secondary-market mode defaults to `appendix_6`
- `appendix_5` mode is reserved for future analyzers and currently fails explicitly as not supported

### Additional P2P analyzers

Estateguru:

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.estateguru.report_analyzer \
  --input "path/to/Estateguru report.pdf" \
  --tax-year 2025
```

Lendermarket:

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.lendermarket.report_analyzer \
  --input "path/to/Lendermarket report.pdf" \
  --tax-year 2025
```

Iuvo:

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.iuvo.report_analyzer \
  --input "path/to/Iuvo report.pdf" \
  --tax-year 2025
```

Robocash:

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.robocash.report_analyzer \
  --input "path/to/Robocash report.pdf" \
  --tax-year 2025
```

Bondora Go & Grow:

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.bondora_go_grow.report_analyzer \
  --input "path/to/Go & Grow report.pdf" \
  --tax-year 2025
```

Optional:

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025 \
  --output-dir output/coinbase \
  --cache-dir ~/.cache/tax_reporting
```

Incremental mode (previous year state + current year operations only):

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.coinbase.report_analyzer \
  --input "path/to/Coinbase Report - 2025-only.csv" \
  --tax-year 2025 \
  --opening-state-json output/coinbase/coinbase_report_since_inception_state_end_2024.json \
  --output-dir output/coinbase \
  --cache-dir ~/.cache/tax_reporting
```

### Kraken report analyzer

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.kraken.report_analyzer \
  --input "path/to/kraken_ledger.csv" \
  --tax-year 2025
```

Incremental mode (previous year state + current year operations only):

```bash
PYTHONPATH=src pyenv exec python -m integrations.crypto.kraken.report_analyzer \
  --input "path/to/kraken_ledger_2026.csv" \
  --tax-year 2026 \
  --opening-state-json output/kraken/kraken_report_since_inception_state_end_2025.json \
  --output-dir output/kraken \
  --cache-dir ~/.cache/tax_reporting
```

Coinbase analyzer highlights:

- input supports Coinbase preamble + header row with or without leading `ID` column
- architecture is layered: Coinbase parser + Coinbase->IR mapper + shared generic crypto analyzer
- supports `Buy`, `Sell`, `Convert`, `Send`, `Receive`, `Deposit`, `Withdraw`, `Withdrawal`
- signed average-cost model per asset (`quantity` and `total_cost_eur` can be positive/negative/zero)
- realization is on closing legs only (supports partial closes and long<->short flips in a single trade)
- declaration totals include only realized closing-leg results in `--tax-year` (while basis uses full history)
- `Convert` is lowered to two IR legs with shared operation id: source `Sell` + target `Buy` (target can close an existing short)
- Coinbase statements value rule is enforced: `Total = Subtotal + Fees`; use `Total` for economic value, except Convert source uses `Subtotal`
- Coinbase transaction semantics are applied directly: `Deposit/Withdraw` as fiat movements, `Send/Receive` as crypto movements
- `Receive` can close an existing short before opening/adding long (using provided `Cost Basis (EUR)` basis)
- `Send` rows do not accumulate in Appendix 5 totals
- `Send` is validated only against existing long holdings in this analyzer version
- EUR conversion via existing `bnb_fx` and `crypto_fx`
- outputs:
- enriched IR CSV (`*_modified.csv`) with IR columns plus EUR/tax columns
- `Subtotal (EUR)` / `Total (EUR)` and position-after audit columns are intentionally omitted from output CSV
- IR numeric columns (`Quantity`, `Proceeds`, `Fee`, `Cost Basis`) keep Decimal precision from mapping/analysis (no forced 8-decimal quantization)
- tax columns (`Purchase/Sale/Profit/Net`) are filled only on closing legs with non-zero realized PnL
- declaration TXT (`Приложение 5 / Таблица 2`) with manual-check summary
- informational `manual check overrides` metric (count of non-empty `Review Status` rows)
- year-end state JSON (`*_state_end_<tax_year>.json`) for incremental runs
- no separate `*_ir.csv` is produced; `*_modified.csv` is the primary IR CSV

For full Coinbase rules and edge-case behavior, see:

- [src/integrations/crypto/coinbase/README.md](src/integrations/crypto/coinbase/README.md)

Kraken analyzer highlights:

- Kraken ledger rows are mapped to shared IR; accounting/PnL logic is fully in `integrations.crypto.shared`.
- multi-row operations are grouped by `refid` and lowered to IR rows with shared `Operation ID`.
- `spend+receive` pairs map to one IR `Buy`; `trade/tradespot` pairs map to IR `Sell` + `Buy`.
- receive-like crypto deposits support `Review Status` workflows (`CARRY_OVER_BASIS`, `RESET_BASIS_FROM_PRIOR_TAX_EVENT`, `NON-TAXABLE`).
- `NON-TAXABLE` receive-like rows are included as non-taxable inventory movement (affect holdings/state, no taxable PnL).
- output contract matches Coinbase:
- enriched IR CSV (`*_modified.csv`)
- declaration TXT
- year-end state JSON

For full Kraken rules and edge-case behavior, see:

- [src/integrations/crypto/kraken/README.md](src/integrations/crypto/kraken/README.md)

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
