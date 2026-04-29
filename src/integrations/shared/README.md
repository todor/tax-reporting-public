# Shared Analyzer Layer

This package contains cross-integration shared orchestration pieces used by the user-facing unified CLI (`python -m report_analyzer`).

## Modules

- `contracts.py`
  - analyzer registration contract (`AnalyzerDefinition`)
  - shared structured result model (`TaxAnalysisResult`)
  - diagnostics and appendix records
- `registry.py`
  - dynamic analyzer discovery (`analyzer_definition.py` and `*_analyzer_definition.py` modules)
  - alias resolution
  - supports either a single `ANALYZER` or multiple `ANALYZERS` in one module
- `autodetect.py`
  - input-folder scanning and analyzer auto-detection
  - optional `--include-pattern` filter uses strict glob (`fnmatch`) semantics
  - literal `[` / `]` in filename patterns must use escaped glob forms (`[[]` / `[]]`)
  - explicit `--analyzer-input alias=path` override parsing (repeatable, including multiple files per alias)
  - when multiple files are mapped to the same alias, all files are processed (no single-file restriction per analyzer)
- `result_builders.py`
  - adapters from existing analyzer-native summaries/results into `TaxAnalysisResult`
- `aggregation.py`
  - declaration aggregation from structured appendix records
  - rendering of `aggregated_tax_report_<year>.txt`
  - delegates appendix Bulgarian declaration sections to shared canonical renderers in `integrations.shared.rendering`
- `rendering/common.py` and `rendering/display_currency.py`
  - shared TXT document helpers for Technical Details, manual-review banners, and display-only currency context

## Unified CLI Behavior (Shared Layer)

Single-analyzer mode:

- `python -m report_analyzer <alias> --input <file> --tax-year <year> [options]`

Aggregate mode:

- `python -m report_analyzer --input-dir <dir> --tax-year <year> [options]`
- auto-detects files by alias token rules and extension
- supports repeated `--analyzer-input alias=path` overrides
- supports multi-file-per-alias execution and accumulation
- supports `--display-currency {EUR,BGN}` for TXT rendering only

## Aggregate TXT Output Contract

`aggregation.py` renders one unified file:

- `<output-dir>/aggregated_tax_report_<year>.txt`

Top-level output behavior:

- top status banner: `OK` / `WARNING` / `NEEDS_REVIEW` / `ERROR`
- per-analyzer status summary
- aggregated appendix totals from structured records (not text parsing)
- output paths rendered as URL-encoded `file://` URIs for clickable local navigation in supported tools
- manual-review rows are excluded from declaration totals but reflected in status/diagnostic sections
- when `--display-currency BGN` is used, declaration-facing monetary lines are rendered in BGN using `bnb_fx` at tax-year end; technical FX metadata is shown under `Technical Details`

## Design Notes

- Aggregation is based on structured appendix records, never by parsing analyzer text outputs.
- Analyzer business logic stays in integration modules; this shared layer only orchestrates and aggregates.
- Manual-review diagnostics are surfaced in aggregate status and summary, while declaration totals come only from structured, non-review appendix records emitted by analyzers.
- Appendix-facing declaration formatting is centralized in `integrations.shared.rendering` and reused by both individual analyzer outputs and aggregated output to avoid drift.
