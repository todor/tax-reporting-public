---
name: tax-commit-message
description: Generate a concise, release-note-ready commit message for the current tax-reporting local changes. Use when the user invokes `$tax-commit-message` or asks for a commit message, local changes summary, or git commit text.
---

# Tax Commit Message Skill

When invoked, summarize the current local changes compared to the relevant remote/base branch as a commit message.

Inspect the local git diff against the upstream branch when available. If no upstream branch exists, use the most appropriate remote/base branch.

Output only the commit message.

Keep the style similar to existing project commit summaries:
- short specific title
- concise bullets
- optional `Validation:` section

Rules:
- Base the message only on actual local changes.
- Keep it concise, but useful for future release notes.
- Mention affected analyzers/platforms when relevant, such as IBKR, Kraken, Binance, Coinbase, Crypto.com, Finexify, Karol, P2P, or SPB-8.
- Make tax/reporting impact explicit when present, such as appendix placement, taxable/non-taxable classification, trade counts, FX/cost basis, withholding tax, dividends, interest, CFDs, futures, options, forex, crypto, P2P, diagnostics, warnings, or report output.
- Do not hide tax-impacting behavior under vague wording like “fix logic” or “improve reports”.
- Include validation only when known.
- Do not add explanations outside the commit message.
