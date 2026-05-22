# AGENTS.md

## Purpose
This repo contains tax analyzers (IBKR, Binance, etc.) that produce Bulgarian tax declaration data.

---

## Core rules
- Prefer the simplest design that fits the analyzer’s real complexity.
- Small analyzers may stay single-file.
- Larger analyzers must be split into coherent modules.
- Do not create large monolithic files.

---

## Structure
- Organize code by business/source responsibilities (e.g. trades, dividends, interest).
- Appendix modules (appendix5, appendix6, appendix8, appendix9) are allowed only as final builders/assemblers.
- Do not move raw parsing logic into appendix modules.
- If logic depends on source format → keep it in business modules.
- If logic depends on tax declaration rules → keep it in appendix modules.

---

## Orchestration
- Each analyzer must have a single clear entrypoint.
- The entrypoint orchestrates:
  - source/business modules
  - appendix builders
- Keep orchestration simple and explicit (no pipelines/frameworks).

---

## Shared code
- Keep shared modules minimal.
- Do not create generic dumping-ground files (utils, constants, calc) without clear need.

---

## Tests
- Large analyzers must not have a single large test file.
- Organize tests by business responsibility.
- Do not reduce coverage or weaken assertions.

---

## Cross-analyzer rule
- Internal structure may vary by analyzer.
- Do not force the same structure on all analyzers.
- Consistency is required at the output boundary, not internally.

---

## Result contract
Each analyzer should return structured output containing:
- appendix data
- diagnostics for warnings / review-required items
- audit/debug data (if applicable)

Expected analyzer issues must be structured diagnostics, not free-form warning strings:
- add a stable diagnostic code
- pass structured params/context and raw evidence
- render Bulgarian user-facing text centrally from code + params
- render English technical diagnostics centrally from code + params/raw evidence

Do not add new expected issues to legacy `warnings: list[str]` or `manual_check_reasons` fields. Those are compatibility inputs only and should normalize to structured diagnostics in the shared result/reporting layer.

---

## Output boundary
- Main TXT reports are Bulgarian, taxpayer/accountant-facing, and actionable.
- Main TXT reports must include declaration data, assumptions, warnings, errors, manual-review items, and what-to-do-next guidance needed for common fixes.
- Diagnostics TXT reports are supplementary and may contain English/mixed technical details, raw parser errors, file paths, tracebacks, and audit/debug evidence.
- stdout is operational only: status, report paths, diagnostics path, and short summary counts.
- Do not copy raw English exception text into the Bulgarian main report.
- User-actionable expected failures should use shared diagnostic codes/params where practical.
- ERROR / NEEDS_REVIEW / WARNING diagnostics must be deduplicated and rendered near the top of the main report with the exact actionable reason.
- Notes/assumptions sections are only for non-actionable context and must be omitted when empty.
- Analyzer settings or interpretation notes that affect tax treatment, declaration filling, or auditability must be emitted as structured `MainReportNote` values so aggregate reports include them too. Use diagnostics for actionable problems and diagnostics TXT for raw technical evidence.
- Render normal filesystem paths in reports and diagnostics, not `file://` URLs.
- Technical Details belong in diagnostics, not embedded in the main report.
- Prefer shared report/rendering helpers over analyzer-specific formatting at the output boundary.
- Do not render analyzer-specific warning/manual-review sections such as `Бележки по обработката`, custom `Какво трябва да направите`, or duplicate manual-review banners inside analyzers.
- A main report must have exactly one status banner at the top.
- If a new analyzer warning is expected/user-actionable, add a diagnostic code and shared renderer/diagnostics mapping before using it in reports.
- `UNCLASSIFIED_*` diagnostics are defensive fallback only. Normal analyzer tests and golden examples should use known structured diagnostic codes.

---

## Refactoring
- Do not change behavior during refactoring.
- Refactor incrementally.
- Preserve outputs exactly.
