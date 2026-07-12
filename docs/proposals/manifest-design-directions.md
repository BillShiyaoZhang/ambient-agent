# Ambient App Manifest: Design Directions

## Status

Accepted design direction, updated after the maintainer discussion in
[Issue #2](https://github.com/BillShiyaoZhang/ambient-agent/issues/2).
This document records the boundary for the focused Phase 1 implementation; it
does not itself change runtime behavior.

## Context

The Phase 1 target persists each generated App in a directory such as:

```text
workspace/apps/<app-id>/
├── manifest.json
├── index.html
├── style.css
└── controller.js
```

The current implementation still uses `metadata.json` for identity, display
title, and platform-maintained timestamps. Phase 1 replaces that declaration
path with `manifest.json`. Existing metadata may be consumed only by a
one-time repair or migration step and is removed after a successful migration;
it is not retained as a fallback or synchronized second source of truth.

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
Describes identity, purpose, contract version, discovery hints,
and references to registered graph schemas

index.html / style.css / controller.js
Implements how the App behaves

User data / Graph Database
Stores the data the App works with
```

Keeping these layers separate prevents descriptive declarations from becoming
an accidental authorization, graph mutation instruction, or duplicate schema
registry.

## Design principles

1. **Use a clear contract boundary.** Store App declarations in
   `manifest.json`, rather than gradually turning platform metadata into a
   mixed-purpose file.
2. **Use migration, not permanent compatibility.** A legacy App may be repaired
   or migrated once, but normal reads and writes use only `manifest.json`.
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

During early development, an implementation may temporarily retain
`metadata.json` while the migration is being built. The final PR must remove
it from normal reads and writes. Legacy data is converted to a validated
Manifest, verified on disk, and then deleted.

## Recommendation

Use **Direction C: a standalone `manifest.json`**.

This follows the maintainer's preference and is also the clearer long-term
boundary. Phase 1 should introduce the standalone contract, validation, and a
deliberate one-time migration or repair path. It must not preserve
`metadata.json` as a fallback or second source of truth.

The migration rule should be explicit:

- `manifest.json` is authoritative when present and valid;
- legacy `metadata.json` may be read only to construct a migration candidate;
- the candidate must be validated, written safely, and revalidated before the
  legacy file is deleted;
- after migration, normal reads and writes use only `manifest.json`;
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

## Capability and schema boundary

Phase 1 uses `description`, `intents`, and `schema_refs` together as the
smallest useful capability context. It does not add a general `capabilities`
field because the project has not yet defined a consumer, vocabulary,
validation model, or permission semantics for one.

`schema_refs` contains only IDs of schemas registered in the central
`graph_schemas` registry:

```json
{
  "schema_refs": [
    "Task",
    "Event"
  ]
}
```

The registry remains authoritative for schema names, descriptions, properties,
registration, approval, and runtime validation. A schema reference does not
register a schema, copy its definition, grant read or write permission, prove
runtime behavior, or authorize graph mutation.

This association can be exposed through normalized App records for the router
to use as context. Phase 1 does not modify router scoring, RouterContext
construction, schema registration, or graph mutation flows.

## Fields and mechanisms deliberately deferred

### Formal capabilities and permissions

A formal capability taxonomy waits until the runtime model defines its
consumer, vocabulary, validation, approval, and enforcement semantics. No
Manifest declaration is authorization.

### Revision history and rollback

An App version can identify a declaration, but it does not imply that the
platform already supports snapshots, atomic activation, rollback, or data
migration. Those mechanisms require a separate design.

### Automatic recommendations

Intent hints may later help discovery. Phase 1 should not change routing,
proactively recommend Apps, or automatically create or modify them.

## Phase 1 implementation decisions

- `AppManager` is the integration boundary for producing, preserving, reading,
  listing, repairing, and migrating App declarations.
- A valid Manifest read is read-only. If a Manifest is absent, `list_apps()`
  or `get_app_files()` may trigger the same per-App, serialized, idempotent,
  on-demand migration or repair path before continuing; normal reads do not
  preserve a metadata-only state.
- An existing invalid Manifest is reported and isolated, never overwritten or
  hidden by legacy metadata.
- The final implementation removes `metadata.json` from normal operation.
- Source-only updates preserve `app_version` and other declarations unless the
  caller explicitly supplies replacements; Phase 1 does not guess or
  automatically increment versions.
- Existing REST timestamp fields are platform record projections rather than
  Manifest declarations. Phase 1 must preserve their public shape and define
  their post-migration source from repository evidence before implementation.
  File modification times must not be treated as equivalent to logical
  creation and update timestamps without an explicit compatibility decision.
- Manifest replacement must validate before writing, use a temporary file and
  atomic same-directory replace, and never report a required partial write as
  success. This is failure handling, not candidate activation or rollback.
- User confirmation policy, candidate activation, and recovery remain separate
  UX and lifecycle work.
