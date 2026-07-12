# Ambient App Manifest: Design Directions

## Status

Discussion proposal, updated after the initial maintainer feedback in
[Issue #2](https://github.com/BillShiyaoZhang/ambient-agent/issues/2).
It documents the intended boundary for a future implementation and does not
change runtime behavior.

## Context

Ambient Agent currently persists each generated App in a directory such as:

```text
workspace/apps/<app-id>/
├── metadata.json
├── index.html
├── style.css
└── controller.js
```

The existing `metadata.json` contains stable identity, display title, and
platform-maintained timestamps. That is sufficient for storage and the current
App Store, but it does not provide a versioned declaration of what an App is
for.

The platform can infer purpose from the App ID, title, source, or conversation
context. Those signals remain useful, but they are not one uniform contract
that can be validated, compared between revisions, or translated into a
consistent user-facing explanation.

The proposed `manifest.json` fills that narrow gap:

> `manifest.json` is a machine-readable contract between each generated App
> and the Ambient Agent platform.

It describes what the App declares itself to be. It does not prove what the
source code does, grant permissions, or replace runtime enforcement.

## Three separate layers

```text
manifest.json
Describes identity, purpose, contract version, and discovery hints

index.html / style.css / controller.js
Implements how the App behaves

User data / Graph Database
Stores the data the App works with
```

Keeping these layers separate prevents descriptive declarations from becoming
an accidental authorization or data schema mechanism.

## Design principles

1. **Use a clear contract boundary.** Store App declarations in
   `manifest.json`, rather than gradually turning platform metadata into a
   mixed-purpose file.
2. **Preserve existing Apps during development.** Legacy Apps should remain
   loadable while compatibility is needed, even though dual files are not the
   final architecture.
3. **Separate declaration from enforcement.** Manifest fields never grant
   runtime permission by themselves.
4. **Version the contract explicitly.** The runtime must reject unsupported
   contract versions rather than guess their meaning.
5. **Keep Phase 1 small.** Add only fields with clear semantics, owners, and
   validation rules.
6. **Make invalid state observable and isolated.** One malformed App must not
   make the rest of the workspace unusable.
7. **Design for people as well as machines.** Users should not read raw JSON;
   the platform should translate relevant declarations into concise UI.

## Considered storage directions

### A. Add descriptive fields to `metadata.json`

This is the smallest implementation, but it combines App declarations with
platform-maintained storage state and leaves the contract boundary implicit.

### B. Turn `metadata.json` into a versioned Manifest

This adds a schema version without introducing another file. It was the
initial recommendation because it reused the existing read path with the
smallest implementation change.

Its main drawback is conceptual: a file named `metadata.json` would become the
long-term public contract while continuing to carry platform-owned fields.

### C. Use a standalone `manifest.json`

The intended final layout is:

```text
workspace/apps/<app-id>/
├── manifest.json
├── index.html
├── style.css
└── controller.js
```

This gives the declaration an explicit name and avoids retaining two
overlapping sources of identity in the final architecture.

During development, the implementation may temporarily read or retain
`metadata.json` to keep legacy Apps usable. That is a migration aid, not a
permanent dual-file design.

## Recommendation

Use **Direction C: a standalone `manifest.json`**.

This follows the maintainer's preference and is also the clearer long-term
boundary. Phase 1 should introduce the standalone contract, validation, and a
deliberate compatibility path. It should not preserve `metadata.json` as an
indefinite second source of truth.

The migration rule should be explicit:

- `manifest.json` is authoritative when present and valid;
- legacy `metadata.json` may be read only by a compatibility path;
- explicit migration or App update can produce `manifest.json`;
- final writes should not require both files to remain synchronized;
- unsupported or corrupted Manifests must not silently fall back in a way that
  hides the error.

## User-facing meaning

The Manifest is machine-readable, but its value is not limited to internal
execution. When a user asks to modify an App, the Agent and platform can use a
validated Manifest to present:

- the App's current declared purpose;
- the proposed purpose or version change;
- a concise explanation of the user-visible impact;
- whether the candidate Manifest passed contract validation;
- a confirmation step when a meaningful change warrants one.

The user should see a readable change summary, not `manifest.json`.

Not every update should interrupt the user. A future UX policy may distinguish
meaningful purpose or behavior changes from low-risk visual corrections. The
Manifest supplies structured input to that decision; it does not, by itself,
fully determine risk.

See [Manifest UX Workflow](manifest-ux-workflow.md) for the proposed UX,
responsibility, and lifecycle boundaries.

## Fields deliberately deferred

### Capabilities and permissions

Capability declarations should wait until the runtime permission model defines
their vocabulary, validation, approval, and enforcement semantics. A declared
capability must never be treated as authorization.

### Data schemas and migrations

The project already has a graph schema approval path. The Manifest should not
duplicate graph schemas or silently introduce an App data migration protocol.

### Revision history and rollback

An App version can identify a declaration, but it does not imply that the
platform already supports snapshots, atomic activation, rollback, or data
migration. Those mechanisms require a separate design.

### Automatic recommendations

Intent hints may later help discovery. Phase 1 should not change routing,
proactively recommend Apps, or automatically create or modify them.

## Remaining design questions

1. Which existing write path should first become responsible for producing
   complete Manifest descriptions and intent hints?
2. Should legacy Apps be migrated only on explicit update, or should there be
   a separate migration command?
3. Which user-visible changes should require confirmation once the UX is
   implemented?
4. When should the temporary `metadata.json` compatibility path be removed?
