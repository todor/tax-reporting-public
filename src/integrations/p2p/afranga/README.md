# Afranga P2P Analyzer

Entry point:

- `integrations.p2p.afranga.report_analyzer`

## Overview

Afranga analyzer reads machine-generated PDF tax statements and produces Appendix 6 declaration text.

Current scope:

- Appendix 6 only
- secondary-market mode `appendix_6` only
- `appendix_5` mode is reserved and currently rejected with explicit error

## Architecture

Afranga uses shared P2P foundations:

- `services.pdf_reader`: PDF text extraction and normalization
- `integrations.p2p.shared.appendix6_models`: normalized Appendix 6 result model
- `integrations.p2p.shared.appendix6_renderer`: deterministic `.txt` output

Afranga-specific logic remains local:

- `afranga_parser.py`: summary and appendix parsing + domain validations
- `report_analyzer.py`: CLI orchestration + output writing

## Input

- Afranga tax statement PDF
- machine-generated text PDF (OCR is not used)

## Parsing approach

Parsing relies on content markers and regex patterns, not on fixed page numbers.

Summary fields are extracted from full-document text:

- `Reporting year`
- statement period (`for the period between ... till ...`)
- `Income from interest received`
- `Income from late interest received`
- `Bonuses`
- `Income/loss from secondary market discount/premium`

Appendix section is detected by `Appendix No. 1` marker and parsed sequentially.

Inside appendix:

- company headers are detected from `..., company number <n> registered in ...`
- detail rows parsed:
- `Income from interest EUR <gross> <wht%> <wht> <net>`
- `Income from late interest EUR <gross> <wht%> <wht> <net>`
- repeated headers/country lines/total lines are ignored as structural delimiters

## Mapping and calculations

For each company, one Part I row is emitted:

- `code=603`
- `amount=sum(gross interest + gross late interest)`

Computed from detail rows:

- `Net Sum from Appendix = sum(net)`
- `Total WHT from Appendix = sum(wht)`

Aggregates:

- `total_interest_received = interest_received + late_interest_received`
- `aggregate_code_603 = total_interest_received - net_sum_from_appendix`
- `aggregate_code_606 = bonuses + max(secondary_market_result, 0)` in `appendix_6` mode

Validation rule:

- if `aggregate_code_603 < 0`, analyzer fails with diagnostics that include:
- `Income from interest received`
- `Income from late interest received`
- `Total interest received`
- `Net Sum from Appendix`
- computed `aggregate_code_603`

Part II:

- `taxable_code_603 = sum(part1 code603 rows) + aggregate_code_603`
- `taxable_code_606 = aggregate_code_606`

Part III:

- `withheld_tax = total_wht_from_appendix`

## Validations

Hard-fail on:

- PDF read/extraction failure
- missing required summary fields
- ambiguous summary field matches
- malformed numeric values
- appendix detail row before any company context
- unsupported `secondary_market_mode`
- negative `aggregate_code_603` (with detailed diagnostic values)

Non-fatal warning:

- if appendix `Total ...` row exists and does not match parsed detail sums

## Output

- `<input_stem>_declaration.txt` in `output/p2p/afranga` by default

Output sections:

- `Приложение 6 / Част I`
- `Част II`
- `Част III`
- `Одитни данни`
- `Бележки по обработката` (when parser emits non-blocking explanatory notes)

Informative rows include reporting year, statement period, summary metrics, appendix net/WHT totals, and active secondary-market mode.

### Output naming

Output file stem is normalized for consistency with other analyzers:

- non-alphanumeric characters become `_`
- stem is lowercased
- example: `Afranga report.pdf` -> `afranga_report_declaration.txt`

## Example output shape

The rendered `.txt` follows this deterministic structure:

```text
Приложение 6
Част I
- Ред 1.1
  ЕИК: ...
  Наименование: ...
  Код: 603
  Размер на дохода: ...
- Обща сума на доходите с код 603: ...
- Обща сума на доходите с код 606: ...

Част II
- Облагаем доход по чл. 35, код 603: ...
- Облагаем доход по чл. 35, код 606: ...

Част III
- Удържан и/или внесен окончателен данък за доходи: ...

Одитни данни
- ...
```

## CLI

```bash
PYTHONPATH=src pyenv exec python -m integrations.p2p.afranga.report_analyzer \
  --input "path/to/afranga_statement.pdf" \
  --tax-year 2025
```

Options:

- `--input` (required)
- `--tax-year` (required)
- `--secondary-market-mode` (default `appendix_6`)
- `--output-dir` (optional, default `output/p2p/afranga`)
- `--log-level` (optional)
