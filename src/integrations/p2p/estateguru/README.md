# Estateguru P2P Analyzer

Entry point:

- `integrations.p2p.estateguru.report_analyzer`

## Overview

Parses Estateguru Income Statement PDF and maps aggregate totals to Appendix 6.

Default mode:

- `appendix_6` (supported)

Reserved mode:

- `appendix_5` (not supported yet; explicit error)

## Input

- machine-generated Estateguru Income Statement PDF for `01.01 - 31.12`

Expected key labels:

- `Selected period ...`
- totals row containing:
- `Interest`
- `Bonus (Borrower)`
- `Penalty`
- `Indemnity`
- `Bonus (EG)`
- `Secondary market profit/loss`
- `Sale fee`
- `AUM fee`
- `Total`

## Extraction logic

- Detect report year from `Selected period`.
- Parse the single totals row numeric values.
- Keep all parsed totals in informative rows for auditability.

Note on compact table extraction:

- PDF extraction can include extra unlabeled numeric columns.
- Parser keeps first required columns and warns for any extra totals-row metrics.

## Tax mapping

- `code 603 = Interest + Penalty + Indemnity`
- `code 606 = positive(Bonus (Borrower)) + positive(Bonus (EG)) + positive(Secondary market profit/loss)`

Secondary market in `appendix_6` mode:

- if `> 0`: included in `code 606`
- if `<= 0`: omitted and warning emitted

Ignored for Appendix 6 totals:

- `Sale fee`
- `AUM fee`
- `Total` (kept only for audit/info)

## Validations and warnings

Hard fail:

- missing period marker
- missing/ambiguous totals row
- malformed totals row numbers

Warnings:

- secondary-market aggregate omitted when `<= 0`
- non-zero sale/AUM fees are intentionally not mapped to Appendix 6 totals
- extra unlabeled totals-row metrics detected

## Output

- `<input_stem>_declaration.txt` in `output/p2p/estateguru` by default
- shared Appendix 6 layout via `integrations.p2p.shared.appendix6_renderer`

## CLI

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.estateguru.report_analyzer \
  --input "path/to/Estateguru report.pdf" \
  --tax-year 2025
```
