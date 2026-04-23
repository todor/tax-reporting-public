# Lendermarket P2P Analyzer

Entry point:

- `integrations.p2p.lendermarket.report_analyzer`

## Overview

Parses Lendermarket tax statement PDF and maps aggregate received-income fields to Appendix 6.

Default mode:

- `appendix_6` (supported)

Reserved mode:

- `appendix_5` (not supported yet; explicit error)

## Input

- machine-generated Lendermarket tax statement PDF for `01.01 - 31.12`

Expected key labels:

- `Payments Received`
- `- Principal Amount`
- `- Interest`
- `- Late Payment Fees`
- `- Pending Payment interest`
- `- Campaign rewards and bonuses`

## Tax mapping

- `code 603 = Interest + Late Payment Fees`
- `code 606 = Campaign rewards and bonuses` (non-negative contribution)

Excluded from Appendix 6 totals:

- `Principal Amount`
- `Pending Payment interest` (default excluded unless explicit taxable-received interpretation is added later)
- account value / available funds / deposits / withdrawals / invested funds / paid fees

## Validations and warnings

Hard fail:

- missing report marker or period
- missing required taxable fields
- malformed amounts

Warnings:

- non-zero `Pending Payment interest` is parsed but excluded from Appendix 6 taxable totals

## Output

- `<input_stem>_declaration.txt` in `output/p2p/lendermarket` by default

## CLI

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.lendermarket.report_analyzer \
  --input "path/to/Lendermarket report.pdf" \
  --tax-year 2025
```
