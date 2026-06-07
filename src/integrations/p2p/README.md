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

## Secondary-Market Tax Interpretation

P2P secondary-market transactions can be viewed in two different ways. The tool currently uses `appendix_6` as its conservative default and intentionally does not implement `appendix_5` yet.

### Appendix 6 approach

Under the Appendix 6 approach, P2P loans / claims are treated as receivables, not as securities or standard financial instruments. They usually do not have an ISIN, and the platform secondary market is usually not a regulated-market execution venue. Economically, a secondary-market sale is closer to an assignment or transfer of a receivable than exchange trading of a listed financial instrument.

With this interpretation:

- interest and late-payment fees are treated as Appendix 6, code `603`
- bonuses and campaign rewards are treated as Appendix 6, code `606`
- positive secondary-market profit/loss can be treated as Appendix 6, code `606`, as other income, especially when platforms report only annual aggregate secondary-market gain/loss

This is the tool's current conservative and more defensible default. It works with the annual platform statements that current P2P analyzers parse and avoids reconstructing every loan-part acquisition, sale, partial sale, fee, discount, premium, repayment, and cross-year position.

### Appendix 5 approach

A secondary-market loan part can also be interpreted economically as a financial asset bought and sold for a price. In that view, there may be an acquisition price, sale price, and realized P/L, and the result could be argued under Appendix 5, code `508`, using the logic of transfer of financial assets.

This approach can be economically cleaner and may allow losses or costs to be netted. However, the legal basis is less certain because P2P claims are not explicitly the same as listed securities or standard financial instruments, and they usually lack ISIN / regulated-market execution evidence.

Appendix 5 is not supported now because it would require transaction-level reconstruction, not just an annual statement. The tool would need to link every secondary-market sale to its acquisition cost and handle partial sales, fees, discounts/premiums, repurchases, repayments before sale, and cross-year positions. For platforms like Iuvo this may be possible when Loan ID can trace the full history, but this is not guaranteed across all platforms.

Because the interpretation is less certain and the data requirements are higher, Appendix 5 should not be the default. It may be considered in the future as an explicit advanced mode, only for platforms that provide enough transaction-level data to support it safely.

### Appendix 6 reporting notes

- Interest and late-payment fees go to code `603`.
- Bonuses, campaign rewards, and positive secondary-market P/L go to code `606` under the current default approach.
- If a Bulgarian platform withholds tax, for example Afranga, the withheld final tax must be reported so there is no double taxation.
- For Bulgarian payers, the payer name and EIK may be needed in Appendix 6 Part I.
- For foreign / non-enterprise payers, use the appropriate Appendix 6 section for payers that are not enterprises or self-employed persons.

This documentation is not tax advice. Appendix 6 is the tool's conservative default, while Appendix 5 is an alternative interpretation that may be economically valid in some cases but is not currently supported. Consult a tax advisor for your specific situation.

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
- shared top-level warning/manual-review summary in the unified TXT envelope (when applicable)
- sibling diagnostics TXT file (English/mixed technical/audit details only)

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
