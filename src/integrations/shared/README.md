# Shared Analyzer Layer

This package contains cross-integration shared orchestration pieces used by the user-facing unified CLI (`uv run tax-reporting`).

## Modules

- `contracts.py`
  - analyzer registration contract (`AnalyzerDefinition`)
  - shared structured result model (`TaxAnalysisResult`)
  - diagnostics and appendix records
- `registry.py`
  - static built-in analyzer registration from `report_analyzer.registry.BUILTIN_ANALYZERS`
  - alias resolution
- `autodetect.py`
  - input-folder scanning and analyzer auto-detection
  - optional `--include-pattern` filter uses strict glob (`fnmatch`) semantics
  - literal `[` / `]` in filename patterns must use escaped glob forms (`[[]` / `[]]`)
  - explicit `--analyzer-input alias=path` override parsing (repeatable, including multiple files per alias)
  - when multiple files are mapped to the same alias, all files are processed (no single-file restriction per analyzer)
- `result_builders.py`
  - adapters from existing analyzer-native summaries/results into `TaxAnalysisResult`
  - normalizes legacy analyzer warning lists into structured diagnostics
- `aggregation.py`
  - declaration aggregation from structured appendix records
  - rendering of `aggregated_tax_report_<year>.txt`
  - delegates appendix Bulgarian declaration sections to shared canonical renderers in `integrations.shared.rendering`
- `rendering/common.py`, `rendering/display_currency.py`, and `reporting.py`
  - shared TXT document helpers for diagnostics splitting, standardized report envelopes, and display-only currency context

## Unified CLI Behavior (Shared Layer)

Single-analyzer mode:

- `uv run tax-reporting <alias> --input <file> --tax-year <year> [options]`

Aggregate mode:

- `uv run tax-reporting --input-dir <dir> --tax-year <year> [options]`
- auto-detects files by alias token rules and extension
- supports repeated `--analyzer-input alias=path` overrides
- supports multi-file-per-alias execution and accumulation
- supports `--display-currency {EUR,BGN}` for TXT rendering only

## Aggregate TXT Output Contract

The unified CLI writes:

- `<output-dir>/aggregated_tax_report_<year>.txt`
- `<output-dir>/aggregated_tax_report_<year>.diagnostics.txt`

Top-level output behavior:

- Bulgarian top status banner in the main report
- review summary and deduplicated actionable "what to do" guidance in the main report
- aggregated appendix totals from structured records (not text parsing)
- per-run analyzer status, normal filesystem paths, readable `Diagnostics` entries, display-currency metadata, and audit/debug details in diagnostics
- manual-review rows are excluded from declaration totals but reflected in status/diagnostic sections
- when `--display-currency BGN` is used, declaration-facing monetary lines are rendered in BGN using `bnb_fx` at tax-year end; technical FX metadata is shown in diagnostics

## Diagnostic Contract

Expected analyzer warnings/errors/manual-review items must be structured diagnostics:

- stable `code`
- `severity`
- analyzer alias
- structured `params`/raw evidence
- English technical message for diagnostics

Bulgarian user-facing text is rendered centrally by `reporting.py` from `code + params`.
Do not add new expected issues as free-form strings in analyzer text output or legacy
`warnings: list[str]` fields. Those fields are compatibility inputs only.

`UNCLASSIFIED_*` diagnostics are defensive fallback for unexpected legacy/free-form
messages. Normal analyzer tests and golden examples should use known structured codes.

## Design Notes

- Aggregation is based on structured appendix records, never by parsing analyzer text outputs.
- Analyzer business logic stays in integration modules; this shared layer only orchestrates and aggregates.
- Manual-review diagnostics are surfaced in aggregate status and the Bulgarian main report, while declaration totals come only from structured, non-review appendix records emitted by analyzers.
- Appendix-facing declaration formatting is centralized in `integrations.shared.rendering` and reused by both individual analyzer outputs and aggregated output to avoid drift.
