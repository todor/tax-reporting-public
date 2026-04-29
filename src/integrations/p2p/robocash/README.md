# Robocash P2P Analyzer

Entry point (user-facing):

- `PYTHONPATH=src pyenv exec python -m report_analyzer robocash ...`


## Overview

Parses Robocash annual Tax Report PDF and maps aggregate income totals to Appendix 6.

Default mode:

- `appendix_6` (supported)

Reserved mode:

- `appendix_5` (not supported yet; explicit error)

## Input

Required report from Robocash:

- Open `https://robo.cash/cabinet/statement`
- Set `Type = Tax Report`
- Select period: `1 January - 31 December` for the selected tax year
- Click `Generate`
- Native format is DOCX; export/convert it to a machine-readable PDF before using it as analyzer input

Expected key labels:

- `Earned interest`
- `Earned income from bonuses`
- `Taxes withheld`

## Tax mapping

- `code 603 = Earned interest`
- `code 606 = Earned income from bonuses` (only when positive)

Taxes withheld handling:

- parsed and exposed in informative rows
- not mapped to structured tax-credit workflow in this analyzer due missing country/payer context
- when `Taxes withheld > 0`, warning is emitted

Excluded from Appendix 6 totals:

- invested/uninvested/account-value and cash-flow operational fields

## Validations

Hard fail:

- missing year marker
- missing required income labels
- malformed amounts

## Output

- `<input_stem>_declaration.txt` in `output/p2p/robocash` by default

## CLI

```bash
PYTHONPATH=src pyenv exec python -m report_analyzer robocash \
  --input "path/to/Robocash report.pdf" \
  --tax-year 2025
```
