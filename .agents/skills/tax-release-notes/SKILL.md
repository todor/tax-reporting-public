---
name: tax-release-notes
description: Generate professional GitHub release notes Markdown for the tax-reporting project from git commit history. Supports current repo, explicit repo path, and public repo alias.
---

# Tax Release Notes Skill

When invoked, generate release-ready GitHub-flavored Markdown for the tax-reporting project.

The release notes should be suitable for:

gh release create "$TAG" --title "$TAG" --notes-file release-notes.md

## Default task

Generate release notes from git commit messages between the previous release tag and the target release tag/version.

Use commit messages and commit bodies as the primary source. Do not analyze full code diffs unless the git history is clearly insufficient or the user explicitly asks for deeper analysis.

## Release repository selection

The release repository is selected from the invocation.

Rules:

1. If invoked as `$tax-release-notes` with no repo argument, use the current repository.
2. If invoked as `$tax-release-notes <repo>`, use `<repo>` as the release repository path.
3. If invoked as `$tax-release-notes public`, treat `public` as an alias for the public release repository:
   - first try `../tax-reporting-public`
   - then try `./tax-reporting-public`
   - then try a sibling directory named `tax-reporting-public` near the current repo
4. If the selected repo path does not exist or is not a git repository, report the problem and ask for the correct path.

Do not silently switch from `tax-reporting` to `tax-reporting-public` unless the user explicitly used the `public` alias or provided that repo path.

## Target tag/version

Use the project release tag convention: v<version>.

Determine the target tag in this order:

1. If the user provides a tag like `v0.1.0`, use it as-is.
2. If the user provides a version like `0.1.0`, use `v0.1.0`.
3. If no tag/version is provided, read `project.version` from `pyproject.toml` in the release repo and use `v<project.version>`.

The current version shown in the final response should be the provided version if supplied, otherwise the version read from `pyproject.toml`.

## Previous tag

Find existing release tags in the release repo using the `v*` convention.

Use the latest existing `v*` tag before the target tag as the previous tag.

If the target tag does not exist yet, use the latest existing `v*` tag as the previous tag.

If there is no previous tag, generate notes from the initial commit to the target revision and clearly say this appears to be the first release.

## Git history source

Use commit messages between `<previous_tag>..HEAD`, unless the target tag already exists, in which case use `<previous_tag>..<target_tag>`.

Prefer full commit bodies, not only one-line subjects, because this project’s commit messages often contain release-note-ready summaries and validation sections.

Useful commands:

- git -C <release_repo> log --reverse --format='%H%n%B%n---END-COMMIT---' <range>
- git -C <release_repo> tag --list 'v*' --sort=-version:refname

## Length and detail

Target normal release notes length: 300–800 words.

For unusually large releases, allow up to about 1,200 words.

Do not try to include every commit or every implementation detail.

Prefer:

- 3–7 highlight bullets
- 2–6 dynamic sections after highlights
- concise grouped bullets
- clear user-facing impact
- enough tax/reporting detail to build trust

Avoid:

- long nested subsections
- exhaustive per-feature audit details
- listing every validation suite
- internal-only tooling details unless relevant to users or release maintainers
- repeating the same change in multiple sections

If the generated notes exceed roughly 1,200 words, compress them before final output.

## Output rules

Generate release notes from the commit messages only.

Do not invent changes.

Do not mechanically list commits one by one.

Merge related changes across commits into user-facing bullets.

Use dynamic sections based on the actual changes. Do not force a fixed schema.

Use sentence case for section headings, for example:

- `## Tax-impacting changes`
- `## Reports and diagnostics`
- `## CLI, packaging, and distribution`

Good possible sections include, but are not limited to:

- Highlights
- Tax-impacting changes
- Analyzer changes
- IBKR changes
- SPB-8 changes
- Reports and diagnostics
- Input and validation
- Documentation and examples
- Release validation
- CLI, packaging, and distribution
- Internal changes

Omit empty sections.

Create a specific section when a major feature or theme deserves it, for example:

- IBKR options support
- Futures handling
- CFD and PIL handling
- Appendix 5 trade counts
- Binary release validation

Do not include shell commands in the release notes unless they are validation commands from commit messages.

The generated release notes file should be standalone Markdown.

Start the file with:

# <target tag>

Then include the generated release note sections.

Do not include the Codex metadata summary inside the release notes file.

Do not include private repository paths, local filesystem paths, or private sync details in public release notes.

## Professional release style

The release notes should look credible and professional for a public GitHub release.

Keep them practical, concise, and user-facing.

Do not over-market the release.

Avoid vague sales language such as “powerful”, “seamless”, “revolutionary”, or “game-changing”.

Prefer specific value:

- what changed
- why it matters
- what users may need to review
- whether generated tax reports may change

Make important changes easy to scan.

Prefer strong, concrete bullets over long paragraphs.

It is acceptable to include implementation-adjacent details when they explain tax/reporting behavior.

If the release includes breaking changes, migration steps, or behavior changes that may alter generated tax reports, make them clearly visible.

## Tax-reporting priorities

Always make tax/reporting impact visible when present:

- appendix placement
- taxable/non-taxable classification
- trade counts
- sale/acquisition values
- FX/cost basis handling
- withholding tax
- dividends
- interest
- CFDs
- futures
- options
- forex
- crypto
- P2P
- SPB-8 inclusion/exclusion
- diagnostics, warnings, audit lines, or main report output

Do not hide tax-impacting behavior under vague headings like “Improvements”.

Formatting-only report changes should usually go under `Reports and diagnostics`, not `Tax-impacting changes`, unless they can change how users interpret or declare tax values.

## Validation handling

Summarize validation briefly.

Do not list every historical targeted test run.

Prefer one concise bullet such as:

- Release validation includes full pytest/ruff runs, targeted analyzer tests, normalized output comparisons, and packaged CLI checks.

Include exact commands only when there are very few or when the user explicitly asks.

Do not claim validation passed unless the commit messages or user input say it passed.

## Internal changes

Include internal tooling only if it affects release reliability, installation, packaging, public distribution, or maintainer workflow.

Omit private sync implementation details from public release notes unless the user explicitly asks.

## Final response format

By default, write the release notes to the computed release notes file.

After writing the file, respond with a concise metadata summary only:

Release repo: <path used>
Release type: <public or private>
Current version: <version from pyproject.toml or provided version>
Target tag: <target tag>
Previous tag: <previous tag or none>
Range: <git range used>
Release notes file: <path written>

Then add a short confirmation:

Release notes written successfully.

Do not print the full release notes Markdown in the Codex response unless the user explicitly asks for preview mode.

In preview mode, print the same metadata summary and then output the release notes Markdown in one fenced markdown block without writing a file.

## Release type and release notes file

Determine the release type from the selected release repository.

Rules:

1. If the selected repository directory name is `tax-reporting-public`, this is a public release.
2. Otherwise, this is a private/internal release.

Do not mention private repository details in public release notes.

Use the current version from `pyproject.toml` unless the user provides an explicit version or tag.

The release notes file path is:

- public release: `docs/public-releases/v<version>.md`
- private/internal release: `docs/private-releases/v<version>.md`

Create parent directories when needed.

By default, write the generated release notes to this file.

If the file already exists, replace it with the newly generated release notes after verifying the target repo, version, tag, previous tag, and range.

If the user asks for preview mode, do not write the file; print the release notes Markdown instead.