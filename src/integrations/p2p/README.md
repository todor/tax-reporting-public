# P2P Integrations

P2P integrations are built around a shared Appendix 6 flow:

1. platform-specific input parsing
2. normalization into shared `P2PAppendix6Result`
3. shared Appendix 6 text rendering

Current integrations:

- `afranga` (PDF statement, payer-level appendix parsing)
- `estateguru` (PDF income statement, aggregate Appendix 6 mapping)
- `lendermarket` (PDF tax statement, aggregate Appendix 6 mapping)
- `iuvo` (PDF profit statement, aggregate Appendix 6 mapping)
- `robocash` (PDF tax report, aggregate Appendix 6 mapping)
- `bondora_go_grow` (PDF tax report, aggregate Appendix 6 mapping)

## Shared foundation

Shared components live in `integrations.p2p.shared`:

- normalized Appendix 6 result model
- common renderer for deterministic `.txt` output
- small runtime helpers (mode validation and output naming)
- shared text/money parsing helpers

User-facing execution:

- run P2P analyzers through `uv run tax-reporting <alias> ...`

Display currency for TXT output:

- all P2P analyzers support `--display-currency {EUR,BGN}` through `tax-reporting`
- default is `EUR`
- `BGN` affects only declaration-facing TXT money rendering; calculations remain in EUR
- conversion uses `services.bnb_fx` at tax-year end (`YYYY-12-31`)

Shared PDF extraction utility:

- `services.pdf_reader` (machine-generated text PDFs only, no OCR)

Secondary-market handling modes:

- `appendix_6` (default, supported)
- `appendix_5` (reserved for future, not supported yet)

If `appendix_5` is requested, analyzers fail explicitly with a "not supported yet" error.

## Common P2P Tax Direction

- P2P analyzers target `Приложение 6` by default.
- `code 603`: interest-like income (interest + late interest/penalty-like interest where applicable).
- `code 606`: bonuses and Appendix-6-classified non-interest add-ons.
- Part III reports withholding tax when available in source data.

Current provider-specific 603 nuance:

- Lendermarket includes `Pending Payment interest` in `code 603` (together with `Interest` and `Late Payment Fees`).

## Input and output contract

Input format is provider-specific (machine-generated PDFs), but all providers must produce the same normalized result and final declaration shape.

## Current Output Contract

All P2P integrations should produce:

- declaration text file (`*_declaration.txt`)
- deterministic section ordering:
- `Приложение 6 / Част I`
- `Част II`
- `Част III`
- `Информативни`
- `Бележки по обработката` (when applicable)
- `!!! НЕОБХОДИМА РЪЧНА ПРОВЕРКА !!!` (when applicable)
- `Technical Details` (English technical/audit details only)

And should expose in normalized result:

- Part I payer rows
- aggregate 603 and 606 rows
- Part II taxable totals
- Part III withheld tax
- ordered informative rows
- warnings

## Validation policy

- Unsupported secondary-market mode fails loudly.
- Unparseable or ambiguous provider fields should fail loudly.
- Non-critical provider anomalies can be emitted as warnings for manual review.

## Provider docs

- Shared P2P modules: [src/integrations/p2p/shared/README.md](shared/README.md)
- Afranga integration: [src/integrations/p2p/afranga/README.md](afranga/README.md)
- Estateguru integration: [src/integrations/p2p/estateguru/README.md](estateguru/README.md)
- Lendermarket integration: [src/integrations/p2p/lendermarket/README.md](lendermarket/README.md)
- Iuvo integration: [src/integrations/p2p/iuvo/README.md](iuvo/README.md)
- Robocash integration: [src/integrations/p2p/robocash/README.md](robocash/README.md)
- Bondora Go & Grow integration: [src/integrations/p2p/bondora_go_grow/README.md](bondora_go_grow/README.md)
