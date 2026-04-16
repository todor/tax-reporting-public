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
- warnings / review-required items
- audit/debug data (if applicable)

---

## Refactoring
- Do not change behavior during refactoring.
- Refactor incrementally.
- Preserve outputs exactly.