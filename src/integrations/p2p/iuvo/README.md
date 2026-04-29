# Iuvo P2P Analyzer

Entry point (user-facing):

- `PYTHONPATH=src pyenv exec python -m report_analyzer iuvo ...`


## Overview

Parses Iuvo Profit Statement PDF (income + expenses sections) and maps aggregate values to Appendix 6.

Default mode:

- `appendix_6` (supported)

Reserved mode:

- `appendix_5` (not supported yet; explicit error)

## Input

Required report from Iuvo:

- Open `https://iuvo-group.com/en/account-statement`
- Choose `Profit Statement`
- Use `Change Period` and select `1 January - 31 December` for the selected tax year
- Click `Filter`
- Download the generated machine-readable PDF

Expected key fields:

Income section:

- `Interest income`
- `Late fees`
- `Secondary market gains`
- `Campaign rewards`
- `Interest income iuvoSAVE`

Expenses section:

- `Secondary market fees`
- `Secondary market losses`
- `Early withdraw fees iuvoSAVE`

## Extraction logic

- Parse top-level category totals only.
- Ignore grouped originator/country sub-breakdowns in calculations.
- For categories with sublines, use the category block total (last amount in block).

## Tax mapping

- `code 603 = Interest income + Late fees + Interest income iuvoSAVE`
- `code 606 = positive(Campaign rewards) + positive(secondary_market_aggregate)`

Where:

- `secondary_market_aggregate = gains + losses + fees`
- report signs are respected
- if `losses`/`fees` appear positive in the PDF variant, they are normalized to negative with warning

Appendix 6 behavior:

- if `secondary_market_aggregate > 0`, include it in `code 606`
- if `<= 0`, omit and emit warning

Excluded from Appendix 6 totals:

- `Early withdraw fees iuvoSAVE`

## Validations and warnings

Hard fail:

- missing report marker/period
- missing required top-level categories
- missing or malformed numeric values

Warnings:

- secondary aggregate omitted when `<= 0`
- sign normalization for fees/losses when needed
- early withdraw fees are informational only

## Output

- `<input_stem>_declaration.txt` in `output/p2p/iuvo` by default

## CLI

```bash
PYTHONPATH=src pyenv exec python -m report_analyzer iuvo \
  --input "path/to/Iuvo report.pdf" \
  --tax-year 2025
```
