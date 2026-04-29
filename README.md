# Tax Reporting (Bulgaria / НАП)

Python-based CLI for generating Bulgarian annual tax reporting outputs from real-world investment data (IBKR, crypto exchanges, P2P platforms).

The goal of this project is simple:

> Process complex investment activity and generate declaration-ready results in minutes instead of days of manual work.

This repository provides a transparent, extensible engine that handles real-world edge cases across multiple platforms.

It is especially useful if:

- you invest across multiple brokers and platforms
- you want to avoid manual Excel workflows
- you need full visibility into how results are calculated

The project is evolving based on real usage and feedback, with the aim to cover the majority of practical investment scenarios over time.

## Who is this for

- Individual investors managing their own tax reporting
- Users with activity across multiple platforms (IBKR, crypto, P2P, etc.)
- Accounting professionals exploring automation or evaluating tooling for client workflows
- Developers or advanced users who want full control and transparency

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

## Commercial Usage

This project is free for personal use.

Companies (e.g. accounting firms) may evaluate the tool internally.

Using this tool to provide paid services (e.g. tax reporting for clients) requires a commercial license.

See [COMMERCIAL_USAGE.md](./COMMERCIAL_USAGE.md) for full details.

## Contact

For commercial usage or collaboration:

- Email: todor.projects@gmail.com
- LinkedIn: https://www.linkedin.com/in/todorppetrov/

Please include a short description of your use case.

## Installation & Usage

Install `uv` once:

**macOS:**
```bash
brew install uv
```

**Windows (PowerShell):**
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

**Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Option 1 - Try instantly (no install)

```bash
uvx tax-reporting --help
```

`uvx` runs a published Python CLI in an ephemeral, cached environment without installing it globally.

Example single analyzer run:

```bash
uvx tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol
```

Example aggregate run:

```bash
uvx tax-reporting \
  --input-dir path/to/reports \
  --tax-year 2025 \
  --output-dir output
```

### Option 2 - Install globally

```bash
uv tool install tax-reporting
tax-reporting --help
```

`uv tool install` installs a persistent command-line tool in uv's managed tool environment.

After installing, run:

```bash
tax-reporting coinbase \
  --input "path/to/Coinbase Report.csv" \
  --tax-year 2025
```

### Command Choice

- `uvx tax-reporting` -> run without installing
- `uv tool install tax-reporting` -> install once
- `tax-reporting ...` -> run the installed CLI

Important: `uvx tax-reporting` and `uv tool install tax-reporting` work after the package is published to PyPI or another configured package index. Before publishing, use the development workflow below or install from Git.

## Development

Setup:

```bash
git clone <repository-url>
cd <repository-directory>
uv sync
```

Run:

```bash
uv run tax-reporting --help
```

Development workflow:

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol

uv run pytest
uv run ruff check .

uv add <package>
uv sync
```

What the uv commands mean:

- `uv run` = execute a command inside the project environment
- `uv add` = add a dependency to `pyproject.toml`
- `uv sync` = create/update the environment from `pyproject.toml` and `uv.lock`

No need for:

- `pyenv`
- `virtualenv`
- `pip install`
- `PYTHONPATH` hacks

## Publishing / Release Process

Maintainers publish the package so users can run:

```bash
uvx tax-reporting
uv tool install tax-reporting
```

Required `pyproject.toml` metadata:

```toml
[project]
name = "tax-reporting"
version = "..."

[project.scripts]
tax-reporting = "report_analyzer.cli:main"
```

Build:

```bash
uv build
```

Publish:

```bash
uv publish
```

Recommended secure publishing:

- Use PyPI Trusted Publishing via GitHub Actions.
- Avoid long-lived PyPI API tokens.

After publishing:

```bash
uvx tax-reporting --help
uv tool install tax-reporting
tax-reporting --help
```

Important: these commands only work once the package is available on PyPI or another configured index.

Private usage before publishing:

```bash
uvx git+ssh://git@github.com/<owner>/<repo>.git
uv tool install git+ssh://git@github.com/<owner>/<repo>.git
```

## Unified CLI Reference

Single analyzer mode:

```bash
uv run tax-reporting <alias> \
  --input <file> \
  --tax-year 2025 \
  --output-dir output/<alias>
```

Aggregate mode (auto-detect + run all + aggregate declaration summary):

```bash
uv run tax-reporting \
  --input-dir <folder> \
  --tax-year 2025 \
  --output-dir output
```

Display currency examples:

```bash
uv run tax-reporting coinbase \
  --input "path/to/Coinbase Report.csv" \
  --tax-year 2025 \
  --display-currency EUR

uv run tax-reporting \
  --input-dir path/to/reports \
  --tax-year 2025 \
  --output-dir output \
  --display-currency BGN
```

Auto-detection notes:

- file stem is tokenized by non-alphanumeric separators and lower-cased
- analyzer detection rules are token-set based (case-insensitive)
- example: `Binance_REPORT...PNL.csv` matches Binance futures detection tokens
- if multiple files match the same analyzer alias, all are processed and accumulated in aggregate mode

Aggregate filename conventions (so files auto-detect without `--analyzer-input`):

- `ibkr` (`.csv`): filename contains `ibkr`, or both `interactive` and `brokers`
- `binance_futures` (`.csv`): filename contains:
  - `binance` + `report` + `pnl`, or
  - `binance` + `futures`
- `coinbase` (`.csv`): filename contains `coinbase`
- `kraken` (`.csv`): filename contains `kraken`
- `finexify` (`.csv`): filename contains `finexify`
- `afranga` (`.pdf`): filename contains `afranga`
- `estateguru` (`.pdf`): filename contains `estateguru`
- `lendermarket` (`.pdf`): filename contains `lendermarket`
- `iuvo` (`.pdf`): filename contains `iuvo`
- `robocash` (`.pdf`): filename contains `robocash`
- `bondora_go_grow` (`.pdf`): filename contains:
  - `bondora`, or
  - `go` + `grow`, or
  - `go` + `and` + `grow`

Practical naming examples:

- `IBKR Activity Statement 2025.csv`
- `Binance Report PnL.csv`
- `Coinbase Report - since inception.csv`
- `Kraken Report - since inception.csv`
- `Finexify report 2025.csv`
- `Afranga report.pdf`
- `Estateguru report.pdf`
- `Lendermarket-v2-Report 2025.pdf`
- `Iuvo report.pdf`
- `Robocash report.pdf`
- `Go & Grow report.pdf`

Important detection constraints:

- extension must match analyzer expectations (`.csv` or `.pdf` above)
- multiple files per analyzer are supported and are processed cumulatively in aggregate mode
- if naming is non-standard, use `--analyzer-input alias=path` overrides

Aggregate mode global options:

- `--input-dir`
- `--include-pattern` (optional glob)
- `--analyzer-input alias=path` (repeatable override, including repeated same alias for multiple files)
- `--tax-year`
- `--output-dir`
- `--cache-dir` (shared FX cache override for all analyzers that use FX services)
- `--display-currency {EUR,BGN}` (TXT rendering only; calculations stay in EUR; BGN uses BNB FX at `YYYY-12-31`)
- `--log-level`
- `--clean-output`

`--include-pattern` uses standard glob matching (via `fnmatch`):

- `*.csv` -> only CSV files
- `*report*.pdf` -> PDFs containing `report`
- to match literal `[` and `]` in filenames, escape them in glob form:
  - `*[[]tax-analyzer[]]*`
  - example:
    - `--include-pattern "*[[]tax-analyzer[]]*"`

Group/analyzer override options:

- `--p2p-secondary-market-mode`
- `--afranga-secondary-market-mode`
- `--estateguru-secondary-market-mode`
- `--lendermarket-secondary-market-mode`
- `--iuvo-secondary-market-mode`
- `--robocash-secondary-market-mode`
- `--bondora-go-grow-secondary-market-mode`
- `--ibkr-tax-exempt-mode`
- `--ibkr-eu-regulated-exchange` (repeatable and supports comma-separated values)
- `--ibkr-closed-world`
- `--ibkr-report-alias`
- `--coinbase-opening-state-json`
- `--kraken-opening-state-json`
- `--finexify-opening-state-json`

Naming rule:

- single-analyzer mode uses base flags (for example `--opening-state-json`)
- aggregate mode auto-prefixes analyzer-scoped flags with alias (for example `--coinbase-opening-state-json`)

Display currency rule (all analyzers, single + aggregate):

- default is `EUR`
- `--display-currency BGN` converts declaration-facing TXT monetary values from EUR to BGN
- conversion is rendering-only (no calculation/aggregation changes)
- conversion uses `services.bnb_fx` on `31 Dec` of the selected tax year
- technical metadata for this conversion is shown under `Technical Details`

Aggregate output:

- per-analyzer subfolders under `<output-dir>/<alias>/...`
- `aggregated_tax_report_<tax_year>.txt` at `<output-dir>/`
- aggregate TXT starts with a top status banner (`OK` / `WARNING` / `NEEDS_REVIEW` / `ERROR`)
- output file paths inside aggregate TXT are rendered as URL-encoded `file://` links for clickability in terminals/editors that support file URIs

## BNB FX (`services.bnb_fx`)

What you can do:

- Use BNB XML export only (CSV is no longer supported).
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
uv run python - <<'PY'
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
uv run python -m services.bnb_fx.cli period \
  --symbols USD,EUR \
  --start-date 2024-01-01 \
  --end-date 2024-12-31
```

Build cache for full years:

```bash
uv run python -m services.bnb_fx.cli years \
  --symbols USD \
  --years 2023,2024,2025
```

Build cache into a custom folder:

```bash
uv run python -m services.bnb_fx.cli period \
  --symbols USD \
  --start-date 2024-01-01 \
  --end-date 2024-03-31 \
  --cache-dir output/fx-cache
```

Get one rate:

```bash
uv run python -m services.bnb_fx.cli get-rate \
  --symbol USD \
  --date 2024-10-15
```

Get multiple dates:

```bash
uv run python -m services.bnb_fx.cli get-rate \
  --symbol USD \
  --dates 2025-10-11,2025-10-12
```

`get-rate` output columns:

- `requested_date`
- `effective_date` (may be earlier if no rate on requested date)
- `symbol`
- `eur_for_1_symbol`

## Current Structure

- `src/report_analyzer/`: unified analyzer CLI package (single and aggregate modes)
- `src/main.py`: backwards-compatible wrapper delegating to unified CLI
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
- `src/integrations/shared/`: analyzer registration contracts, autodetect, and aggregate reporting
- `src/integrations/ibkr/activity_statement_analyzer.py`: IBKR analyzer facade/orchestrator
- `src/integrations/ibkr/sections/`: IBKR business/source processing modules (`trades`, `interest`, `dividends`, `tax_withholding`, `open_positions`, `instruments`, etc.)
- `src/integrations/ibkr/appendices/`: IBKR declaration shaping/output modules
- `src/integrations/ibkr/constants.py`: IBKR domain constants and country maps
- `src/integrations/ibkr/models.py`: IBKR typed models/errors/result structures
- `src/integrations/ibkr/shared.py`: shared IBKR parsing/matching/conversion helpers
- `src/services/bnb_fx/`: BNB XML client + quarter cache + CLI
- `src/services/crypto_fx/`: crypto-to-EUR layer (pair resolution + Binance hourly pricing + CLI)
- `src/services/pdf_reader.py`: shared machine-generated PDF text extraction utility
- `src/integrations/shared/rendering/`: canonical declaration-facing appendix renderers (`Приложение 5/6/8/9/13`) reused by both individual analyzers and aggregated output
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
- `tests/integrations/shared/`: unified CLI/shared registry and discovery tests
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
uv run tax-reporting binance_futures \
  --input path/to/binance_futures_pnl.csv \
  --tax-year 2025
```

### IBKR activity statement analyzer

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode listed_symbol \
  --report-alias account1
```

Optional venue override inputs (activates closed-world venue classification for this run):

```bash
uv run tax-reporting ibkr \
  --input path/to/ibkr_activity_statement.csv \
  --tax-year 2025 \
  --tax-exempt-mode execution_exchange \
  --eu-regulated-exchange TGATE \
  --eu-regulated-exchange "ENEXT.FR,NYSE"
```

Closed-world without adding extra regulated exchanges:

```bash
uv run tax-reporting ibkr \
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
- IBKR declaration output includes `Audit Data` with encountered venue categories and active classification mode.
- In `listed_symbol` mode, execution exchange is documented once as a global informational note (no per-row informational noise).

### Coinbase report analyzer

```bash
uv run tax-reporting coinbase \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025
```

### Finexify fund analyzer

```bash
uv run tax-reporting finexify \
  --input "path/to/finexify.csv" \
  --tax-year 2025
```

### Afranga P2P analyzer

```bash
uv run tax-reporting afranga \
  --input "path/to/afranga_statement.pdf" \
  --tax-year 2025
```

Notes:

- secondary-market mode defaults to `appendix_6`
- `appendix_5` mode is reserved for future analyzers and currently fails explicitly as not supported

### Additional P2P analyzers

Estateguru:

```bash
uv run tax-reporting estateguru \
  --input "path/to/Estateguru report.pdf" \
  --tax-year 2025
```

Lendermarket:

```bash
uv run tax-reporting lendermarket \
  --input "path/to/Lendermarket report.pdf" \
  --tax-year 2025
```

Iuvo:

```bash
uv run tax-reporting iuvo \
  --input "path/to/Iuvo report.pdf" \
  --tax-year 2025
```

Robocash:

```bash
uv run tax-reporting robocash \
  --input "path/to/Robocash report.pdf" \
  --tax-year 2025
```

Bondora Go & Grow:

```bash
uv run tax-reporting bondora_go_grow \
  --input "path/to/Go & Grow report.pdf" \
  --tax-year 2025
```

P2P tax-mapping quick reference:

- Estateguru: `code 603 = Interest + Penalty + Indemnity`; `code 606 = positive(Bonus (Borrower)) + positive(Bonus (EG)) + positive(Secondary market profit/loss)`
- Lendermarket: `code 603 = Interest + Late Payment Fees + Pending Payment interest`; `code 606 = Campaign rewards and bonuses` (non-negative only)
- Iuvo: `code 603 = Interest income + Late fees + Interest income iuvoSAVE`; `code 606 = positive(Campaign rewards) + positive(secondary-market aggregate)`
- Robocash: `code 603 = Earned interest`; `code 606 = positive(Earned income from bonuses)`
- Bondora Go & Grow: `code 603 = Interest Accrued`; `code 606 = positive(Bonus income received on Bondora account)`

Optional:

```bash
uv run tax-reporting coinbase \
  --input "path/to/Coinbase Report - since inception.csv" \
  --tax-year 2025 \
  --output-dir output/coinbase \
  --cache-dir ~/.cache/tax_reporting
```

Opening-state mode (recommended after first filing year):

```bash
uv run tax-reporting coinbase \
  --input "path/to/Coinbase Report - 2025-only.csv" \
  --tax-year 2025 \
  --opening-state-json output/coinbase/coinbase_report_since_inception_state_end_2024.json \
  --output-dir output/coinbase \
  --cache-dir ~/.cache/tax_reporting
```

Opening-state contract:

- for `--tax-year YYYY`, `state_tax_year_end` in `--opening-state-json` must be `< YYYY`
- with opening state, analyzer applies ledger/state math only for rows where:
- `state_tax_year_end < row.timestamp.year <= tax_year`
- rows `<= state_tax_year_end` and rows `> tax_year` are ignored for ledger/state
- declaration totals still include only `row.timestamp.year == tax_year`

### Kraken report analyzer

```bash
uv run tax-reporting kraken \
  --input "path/to/kraken_ledger.csv" \
  --tax-year 2025
```

Opening-state mode (recommended after first filing year):

```bash
uv run tax-reporting kraken \
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
- `Receive` can close an existing short before opening/adding long:
- `CARRY_OVER_BASIS` uses provided `Cost Basis (EUR)`
- `GIFT` forces zero basis
- `NON-TAXABLE` uses market EUR value at receive timestamp (no basis expected)
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
- receive-like crypto deposits support `Review Status` workflows (`CARRY_OVER_BASIS`, `GIFT`, `NON-TAXABLE`).
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
uv run python -m services.crypto_fx.cli get-rate \
  --symbol-or-pair ALCHUSDT \
  --exchange binance \
  --is-future \
  --timestamp 2025-10-11T10:30:15Z
```

## License

This project is licensed under **MIT + Commons Clause**.

- Free for personal use
- Commercial usage requires a separate agreement

See [LICENSE](./LICENSE) for details.
