# Shared P2P Appendix 6 Foundation

Shared P2P modules are intentionally small:

- `appendix6_models.py`: normalized result model for P2P Appendix 6 analyzers
- `appendix6_renderer.py`: shared deterministic Appendix 6 text renderer
- `runtime.py`: shared mode validation, CLI summary lines, output path helper
- `text_money.py`: shared text-line normalization and Decimal parsing helpers

This layer is reused by current P2P integrations (Afranga, Estateguru, Lendermarket, Iuvo, Robocash, Bondora Go & Grow) without forcing a shared parser hierarchy.

## Result model

`P2PAppendix6Result` contains:

- Part I payer rows
- aggregate rows (`code 603`, `code 606`)
- Part II taxable totals
- Part III withheld tax
- ordered informative rows
- warnings (manual-check-required issues)
- informational messages (non-blocking explanatory notes)

The model is renderer-friendly and ready for future cross-analyzer aggregation.

### Core dataclasses

- `Appendix6Part1Row`:
- `payer_name`
- `payer_eik | None`
- `code`
- `amount`

- `InformativeRow`:
- `label`
- `value` (`Decimal | str`)

- `P2PAppendix6Result`:
- `platform`
- `tax_year | None`
- `part1_rows`
- `aggregate_code_603`
- `aggregate_code_606`
- `taxable_code_603`
- `taxable_code_606`
- `withheld_tax`
- `informative_rows`
- `warnings`
- `informational_messages`

## Rendering contract

`appendix6_renderer.py` is the single shared formatter for P2P Appendix 6 declaration output.
Integrations should not format declaration text ad-hoc.

Renderer guarantees:

- stable ordering of sections and rows
- deterministic decimal formatting for money values
- identical field labels across P2P providers
- manual-check block is shown only when warning-level issues exist
- tax-result clarifications are rendered in Bulgarian sections (`Информативни`, `Бележки по обработката`)
- extracted technical/audit context is rendered in English under `Technical Details` -> `Audit Data`

## Mode handling

Runtime helper enforces mode validation:

- `appendix_6`: supported
- `appendix_5`: explicit `not supported yet` error unless future analyzer opts in

This avoids silent behavioral drift before Appendix 5 mode is implemented.

CLI summary lines are intentionally minimal:

- `STATUS: SUCCESS` or `STATUS: MANUAL CHECK REQUIRED`
- declaration output path
- `STATUS: ERROR` is printed by integration entrypoints on failure
