# Bondora Go & Grow P2P Analyzer

Entry point (user-facing):

- `uv run tax-reporting bondora_go_grow ...`


## Overview

Parses Bondora Go & Grow Tax Report PDF and maps selected fields to Appendix 6.

Default mode:

- `appendix_6` (supported)

Reserved mode:

- `appendix_5` (not supported yet; explicit error)

## Input

Required report from Bondora Go & Grow:

- Open `https://app.goandgrow.eu/en/statements/index/`
- Click `Download`
- Set `Report Type = Tax Report`
- Select the tax year
- Download the generated machine-readable PDF

Expected key content:

Portfolio table row (`Go & Grow`) with sequence:

- `Capital invested`
- `Capital withdrawn`
- `Withdrawal fees`
- `Profit realized`
- `Interest Accrued`
- `Net profit`

Other income section:

- `Bonus income received on Bondora account*`

Parser accepts label quirk `Bonusincome` (without space).

## Tax mapping

- `code 603 = Interest Accrued`
- `code 606 = Bonus income received on Bondora account` (only when positive)

Excluded from Appendix 6 totals:

- `Capital invested`
- `Capital withdrawn`
- `Withdrawal fees`
- `Profit realized`
- `Net profit`

## Validations and warnings

Hard fail:

- missing report marker/period
- missing `Go & Grow` row
- missing or malformed row amounts
- missing bonus income field

Warnings:

- portfolio capital/profit/net fields are parsed but treated as informational only

## Output

- `<input_stem>_declaration.txt` in `output/p2p/bondora_go_grow` by default

## CLI

```bash
uv run tax-reporting bondora_go_grow \
  --input "path/to/Go & Grow report.pdf" \
  --tax-year 2025
```
