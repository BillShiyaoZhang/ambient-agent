# Codex file generation recovery acceptance (2026-10-02)

## Failure and repair

A weather Widget reported missing `controller.js` at `stage_code`. Its retained draft was empty;
the generator explicitly reported a missing `codex-code-mode-host`, preventing file tools from
starting in both turns. The managed installation contained `codex-cli 0.159.3` without its matching
companion. More code retries could not repair this environment failure.

Independent review also found the old permission parser incompatible with pinned
`codex-acp@2.1.1`: ordinary permission requests omit old `codex.params`, and standard update diffs
contain only hunks. The repair negotiates the exact version and `diffPatch` capability, checking
the associated tool, complete paths, prior file revision, and patch line coordinates while
retaining the artifact whitelist and single-use approval.

The installer completes and strictly validates the companion, supporting retries of partial CLI
installations. Launch reports missing components as `coding_agent_code_mode_unavailable`;
environment failures require installation repair first. Failed generation activities terminate
and precede diagnostic replies in committed event order. New generation clears stale repair
directives while preserving cross-Run verifier history.

## Actual runtime acceptance

The normal installation API repaired the actual Debian 12 aarch64 Docker backend:
`available=false, update_available=true` became `available=true, update_available=false`, retaining
version `0.159.3`. Authentication stayed CLI-owned; credential contents were neither read nor copied.

Actual `gpt-6-luna`, the production ACP adapter, and isolated container temporary staging verified:

| Operation | Result |
| --- | --- |
| Create a static test Widget | Real `controller.js` and Manifest V2 files; zero automatic repairs |
| Replace one controller text locally | Real file update; identical Manifest bytes; zero automatic repairs |
| Widget Runtime acceptance | Production runtime protocol returned a 640×480 first frame |
| Publication boundary | Both generations used `promote=false`; the test App was not published |

This verifies file tools and rendering, not completion of the weather App. Its failed draft and
approval remain retained: the weather design has no approved data capability. Live weather sources
still require design approval; test data or expanded Manifest grants cannot substitute for it.

## Regression boundaries

Regressions cover initial and partial installation, bad archives and size/link/download limits,
failed-install retries, real launch errors, old and new ACP shapes, creation and local updates,
CRLF and missing final newlines, multiple files, outside paths/moves/stale evidence, single use,
stopping consecutive empty artifacts, retained drafts, atomic event commits, and bilingual diagnostics.

The pinned bridge limits patch evidence to 1MiB; paths alone cannot approve missing update evidence.
Synchronous launch checks companion files, size, and execute permission. Functional `--help`
checks run during installation and status. Actual acceptance covers missing-component recovery;
it does not claim every same-size damaged executable is detected by synchronous checks.
