# P2P Integrations

P2P integrations are built around a shared Appendix 6 flow:

1. platform-specific input parsing
2. normalization into shared `P2PAppendix6Result`
3. shared Appendix 6 text rendering

Current integration:

- `afranga` (PDF statement)

## Shared foundation

Shared components live in `integrations.p2p.shared`:

- normalized Appendix 6 result model
- common renderer for deterministic `.txt` output
- small runtime helpers (mode validation, output naming, CLI summary lines)
- shared text/money parsing helpers

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

## Input and output contract

Input format is provider-specific (currently Afranga PDF), but all providers must produce the same normalized result and final declaration shape.

## Current Output Contract

All P2P integrations should produce:

- declaration text file (`*_declaration.txt`)
- deterministic section ordering:
- `Приложение 6 / Част I`
- `Част II`
- `Част III`
- `Информативни`

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
