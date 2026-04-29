# Lendermarket P2P Analyzer

Entry point (user-facing):

- `uv run tax-reporting lendermarket ...`


## Overview

Parses Lendermarket tax statement PDF and maps aggregate received-income fields to Appendix 6.

Default mode:

- `appendix_6` (supported)

Reserved mode:

- `appendix_5` (not supported yet; explicit error)

## Input

Required report from Lendermarket:

- Open `https://app.lendermarket.com/tax-report`
- Select period: `1 January - 31 December` for the selected tax year
- Click `Download Tax Report`
- Use the generated machine-readable PDF

Expected key labels:

- `Payments Received`
- `- Principal Amount`
- `- Interest`
- `- Late Payment Fees`
- `- Pending Payment interest`
- `- Campaign rewards and bonuses`

## Tax mapping

- `code 603 = Interest + Late Payment Fees + Pending Payment interest`
- `code 606 = Campaign rewards and bonuses` (non-negative contribution)

Excluded from Appendix 6 totals:

- `Principal Amount`
- account value / available funds / deposits / withdrawals / invested funds / paid fees

## Validations and warnings

Hard fail:

- missing report marker or period
- missing required taxable fields
- malformed amounts

Warnings:

- negative `Campaign rewards and bonuses` is not included in `code 606`

## Output

- `<input_stem>_declaration.txt` in `output/p2p/lendermarket` by default

## CLI

```bash
uv run tax-reporting lendermarket \
  --input "path/to/Lendermarket report.pdf" \
  --tax-year 2025
```
