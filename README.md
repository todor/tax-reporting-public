# Tax Reporting (Bulgaria / НАП)

Minimal Python foundation for annual Bulgarian tax reporting workflows.

This repository currently contains only the initial project scaffold. No tax logic, calculations, exchange-rate handling, or provider-specific processing is implemented yet.

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
PYTHONPATH=src pyenv exec python -m tax_reporting.main list-integrations
PYTHONPATH=src pyenv exec python -m tax_reporting.main run --integration binance --year 2025 --input data/input.csv --output output
```

## Current Structure

- `src/tax_reporting/main.py`: single CLI entry point
- `src/tax_reporting/config.py`: central project paths
- `src/tax_reporting/logging_config.py`: minimal logging setup
- `src/tax_reporting/integrations/`: integration packages (currently `binance` placeholder)
- `src/tax_reporting/services/`: empty placeholder for future shared services
- `tests/test_imports.py`: minimal import smoke tests
- `output/`: output directory kept in git via `.gitkeep`
