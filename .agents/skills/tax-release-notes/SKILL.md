---
name: tax-release-notes
description: Generate professional GitHub release notes Markdown for the tax-reporting project from git commit history. Writes release notes to docs/public-releases or docs/private-releases based on release type.
---

# Tax Release Notes Skill

When invoked, generate release-ready GitHub-flavored Markdown for the tax-reporting project and write it to the appropriate release notes file.

The generated release notes should be suitable for:

gh release create "$TAG" --title "$TAG" --notes-file <release-notes-file>

## Default task

Generate release notes from git commit messages between the previous release tag and the target release tag/version.

Use commit messages and commit bodies as the primary source. Do not analyze full code diffs unless the git history is clearly insufficient or the user explicitly asks for deeper analysis.

By default, write the generated release notes to the computed release notes file.

If the user asks for `preview`, do not write the file; print the generated Markdown instead.

## Invocation model

Separate the history source from the output repository.

Definitions:

- Output repo: the current repository where Codex is running and where the release notes file is written by default.
- History source: the repository or remote whose git tags and commit history are used to build the release notes.
- Release type: public or private/internal, used to choose the release notes directory.

Rules:

1. `$tax-release-notes`
   - History source: current/output repo.
   - Output repo: current repo.
   - Release type: public if the current repo directory name is `tax-reporting-public`; otherwise private/internal.

2. `$tax-release-notes public`
   - History source: public repository history.
   - Output repo: current repo.
   - Release type: public.
   - Output file: `<current repo>/docs/public-releases/v<version>.md`.

3. `$tax-release-notes <repo>`
   - History source: `<repo>`.
   - Output repo: current repo.
   - Release type: public if the history source repo directory name is `tax-reporting-public`; otherwise private/internal.
   - Write the release notes under the current/output repo unless the user explicitly asks to write into the target repo.

Do not silently write release notes into another checkout just because that checkout was used as the history source.

## Public history source

For `$tax-release-notes public`, use the public repository as the history source:

https://github.com/todor/tax-reporting-public

Prefer the public remote history over relying on a local checkout being up to date.

A local `../tax-reporting-public` checkout may be used as a cache or convenience only if it is valid and refreshed/fetched first.

Do not require the local public checkout to be checked out on a branch.

Do not require the local public checkout worktree to contain `pyproject.toml`.

If the public remote can provide tags and commit history, use it even if the local public checkout is empty, unborn, stale, detached, or missing files.

If a local git object database is needed to inspect remote history, Codex may:

- use an existing local public checkout after fetching tags from origin;
- use `git ls-remote --tags https://github.com/todor/tax-reporting-public`;
- create or use a temporary local clone/cache only if needed.

## Release type and release notes file

Determine the release type from the invocation/history source:

1. If invoked as `$tax-release-notes public`, this is a public release.
2. If the selected history source repository directory name is `tax-reporting-public`, this is a public release.
3. Otherwise, this is a private/internal release.

Do not mention private repository details in public release notes.

The release notes file path is written under the output repo:

- public release: `docs/public-releases/v<version>.md`
- private/internal release: `docs/private-releases/v<version>.md`

Create parent directories when needed.

If the file already exists, replace it with the newly generated release notes after verifying the history source, output repo, release type, version, tag, previous tag, range, and output path.

## Target tag/version

Use the project release tag convention: `v<version>`.

Determine the target tag in this order:

1. If the user provides a tag like `v0.1.0`, use it as-is.
2. If the user provides a version like `0.1.0`, use `v0.1.0`.
3. If no tag/version is provided, read `project.version` from `pyproject.toml` in the output repo and use `v<project.version>`.
4. If `pyproject.toml` is not available in the output repo, fall back to reading `project.version` from the history source repo if available.

For `$tax-release-notes public` invoked from the private/source repo, intentionally read the current version from the output repo `pyproject.toml`. The public release notes are prepared in the source repo and then synced to the public repo.

The current version shown in the final response should be the provided version if supplied; otherwise, use the version read from `pyproject.toml`.

## Previous tag

Find existing release tags in the history source using the `v*` convention.

Select the previous release tag as the most recent existing `v*` tag before the target release.

Prefer release/tag chronology over version-string sorting:

1. If GitHub Releases are available, prefer the latest GitHub Release tag before the target release.
2. Otherwise, use annotated tagger date or tag creation date when available.
3. Otherwise, use the commit date of the tagged commit.
4. Use semantic version ordering only as a fallback or tie-breaker.

Do not pick a prerelease/dev tag merely because version sorting places it after a stable tag.

Example:

- Existing tags: `v0.1.0.dev1`, `v0.1.0`
- `v0.1.0` was created/released after `v0.1.0.dev1`
- Target tag: `v0.2.0`
- Previous tag must be `v0.1.0`

If the target tag already exists, choose the most recent existing `v*` tag before that target tag.

If the target tag does not exist yet, choose the most recent existing `v*` release tag before the target release.

If there is no previous tag, generate notes from the initial commit to the target revision and clearly say this appears to be the first release.

## Git history source

Use commit messages between `<previous_tag>..HEAD`, unless the target tag already exists, in which case use `<previous_tag>..<target_tag>`.

For public remote history, `HEAD` may be the remote default branch head, for example `origin/main`.

For `$tax-release-notes public` with target `v0.2.0` and previous tag `v0.1.0`, the range should be equivalent to:

- `v0.1.0..HEAD`, or
- `v0.1.0..origin/main`

Do not use an older prerelease/dev tag such as `v0.1.0.dev1` when `v0.1.0` is the most recent release tag before the target.

Prefer full commit bodies, not only one-line subjects, because this project’s commit messages often contain release-note-ready summaries and validation sections.

Useful commands:

- `git -C <repo> fetch --tags origin`
- `git -C <repo> log --reverse --format='%H%n%B%n---END-COMMIT---' <range>`
- `git -C <repo> tag --list 'v*'`
- `git -C <repo> for-each-ref refs/tags --format='%(refname:short) %(creatordate:iso8601) %(objectname)'`
- `git ls-remote --tags https://github.com/todor/tax-reporting-public`

## Release notes file content

The generated release notes file should be standalone Markdown containing only the release body.

Start the file directly with the generated release note sections, such as `## Highlights`.

Do not include a top-level `# <target tag>` or release-title heading. GitHub Releases already show the release title supplied by `gh release create --title`, so adding the tag as an H1 in the notes body creates a duplicate heading.

Do not include the Codex metadata summary inside the release notes file.

Do not include private repository paths, local filesystem paths, private sync details, or other private workflow details in public release notes.

## Length and detail

Release notes should be as short as the change allows while still being professional, clear, and useful.

Do not pad the notes to reach a target length.

Small-release rule:

If the release is a small/focused change, especially a formatting, wording, report-guidance, diagnostics-message, path-rendering, documentation, or validation-only change, generate `## Highlights` only.

For such releases:

- use 1–3 bullets total;
- do not add extra sections;
- do not repeat the same change in paragraph form after the bullets;
- do not add a validation paragraph unless validation is the main release value;
- include “No tax calculation/classification changes” only as one short bullet when relevant.

A small release note is allowed to be 40–120 words. Do not expand it.

For normal multi-change releases, target roughly 250–700 words.

For unusually large releases, allow up to about 1,200 words.

Do not try to include every commit or every implementation detail.

Prefer:

- concise grouped bullets
- clear user-facing impact
- enough tax/reporting detail to build trust
- explicit mention when tax calculations or classifications are unchanged

Use dynamic sections only for medium or large releases with multiple distinct themes. Do not create a second section for a small release when `## Highlights` already covers the user-visible change.

Avoid:

- expanding small formatting/report-output changes into full multi-section notes
- long nested subsections
- exhaustive per-feature audit details
- listing every validation suite
- internal-only tooling details unless relevant to users or release maintainers
- repeating the same change in multiple sections

If the generated notes feel larger than the actual release, compress them before writing or previewing the final output.

## Output rules

Generate release notes from the commit messages only.

Do not invent changes.

Do not mechanically list commits one by one.

Merge related changes across commits into user-facing bullets.

Use dynamic sections based on the actual changes. Do not force a fixed schema. For small/focused releases, the complete release note should usually be only `## Highlights` with 1–3 bullets.

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

Summarize validation briefly, and omit it entirely for very small release notes unless it adds useful confidence for users or maintainers.

Do not list every historical targeted test run.

For larger releases, prefer one concise bullet such as:

- Release validation includes full pytest/ruff runs, targeted analyzer tests, normalized output comparisons, and packaged CLI checks.

For small formatting/report-output releases, do not add a separate validation section. Mention validation only if it is directly relevant to the user-visible change, for example path normalization in generated output.

Include exact commands only when there are very few or when the user explicitly asks.

Do not claim validation passed unless the commit messages or user input say it passed.

## Internal changes

Include internal tooling only if it affects release reliability, installation, packaging, public distribution, or maintainer workflow.

Omit private sync implementation details from public release notes unless the user explicitly asks.

## Invocation examples

- `$tax-release-notes`
  Generates notes from the current repo history and writes to the current repo.

- `$tax-release-notes public`
  Generates notes from the public repository history and writes `docs/public-releases/v<version>.md` into the current repo.

- `$tax-release-notes public tag v0.2.0`
  Uses the public repository history and the explicit target tag `v0.2.0`.

- `$tax-release-notes <repo>`
  Uses `<repo>` as the history source and writes the release notes into the current repo.

- `$tax-release-notes preview`
  Prints the Markdown instead of writing the file.

- `$tax-release-notes public preview`
  Uses the public repository history and prints the Markdown instead of writing the file.

## Final response format

After writing the file, respond with a concise metadata summary only:

History source: <public remote URL, current repo, or explicit repo path>
Output repo: <current repo path where the file was written>
Release type: <public or private>
Current version: <version from pyproject.toml or provided version>
Target tag: <target tag>
Previous tag: <previous tag or none>
Range: <git range used>
Release notes file: <path written>

Then add:

Release notes written successfully.

For `$tax-release-notes public`, the expected metadata shape is:

History source: https://github.com/todor/tax-reporting-public
Output repo: /path/to/tax-reporting
Release type: public
Current version: 0.2.0
Target tag: v0.2.0
Previous tag: v0.1.0
Range: v0.1.0..origin/main
Release notes file: /path/to/tax-reporting/docs/public-releases/v0.2.0.md

Do not print the full release notes Markdown in the Codex response unless the user explicitly asks for preview mode.

In preview mode, print the same metadata summary and then output the release notes Markdown in one fenced markdown block without writing a file.