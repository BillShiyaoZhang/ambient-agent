# Ambient App Manifest: Phase 1 Design

## Status

Discussion proposal. The design is intentionally limited to a storage contract,
compatibility behavior, validation boundary, and tests. It does not change
runtime behavior by itself.

## Summary

Phase 1 proposes evolving the existing per-App `metadata.json` into a small,
versioned Manifest while preserving the current App directory layout and
public behavior.

The Manifest would make App identity and purpose explicit, but it would not
yet implement recommendation, permission enforcement, rollback, or UI changes.

## Existing behavior that constrains the design

The current implementation has several behaviors that Phase 1 must respect:

- `AppManager.create_or_update_app()` writes `metadata.json` and the three App
  source files.
- `AppManager.list_apps()` returns metadata objects used by the App Store and
  intent router.
- `AppManager.get_app_files()` returns `id`, `title`, HTML, CSS, and JavaScript
  to the API, agent harness, schema verification, and context injection paths.
- If an App directory contains `index.html` but no `metadata.json`,
  `_ensure_metadata()` derives a title and creates the missing metadata file.
  This repair-on-read behavior is covered by an existing test.
- Existing creation paths provide `id`, `title`, and source code. They do not
  currently provide a reliable description or list of intents.
- The frontend expects at least `id`, `title`, `created_at`, and `updated_at`
  from `GET /api/apps`.

These constraints favor additive changes and compatibility defaults over a
mandatory migration.

## Goals

1. Define a small and explicitly versioned App Manifest contract.
2. Preserve loading, listing, routing input, and App Store display for legacy
   Apps.
3. Centralize Manifest normalization and validation near `AppManager`.
4. Define observable behavior for malformed and unsupported Manifests.
5. Establish tests before any future component begins to rely on new fields.
6. Avoid expanding Phase 1 into unrelated product or security features.

## Non-goals

Phase 1 will not:

- implement Conversation-to-Skill or proactive App recommendations;
- change the router to select Apps by Manifest intents;
- make capability declarations or grant runtime permissions;
- implement candidate versions, atomic activation, rollback, or App data
  migration;
- modify graph schema registration or approval behavior;
- implement a Privacy Data Map or Personal UI preferences;
- change the App Store UI;
- introduce a general plugin or package format;
- require a bulk rewrite of existing workspaces;
- treat Manifest contents as trusted code or authorization.

## Proposed schema

### Version 1 object

```json
{
  "manifest_version": 1,
  "id": "morning-planner",
  "title": "Morning Planner",
  "description": "Helps the user organize a morning plan.",
  "app_version": "0.1.0",
  "intents": [
    "plan my morning",
    "organize today's priorities"
  ],
  "created_at": "2026-07-12T00:00:00+00:00",
  "updated_at": "2026-07-12T00:00:00+00:00"
}
```

### Field semantics

| Field | Required on normalized read | Owner | Meaning |
|---|---:|---|---|
| `manifest_version` | Yes | Platform contract | Integer version of the Manifest schema. Phase 1 supports only `1`. |
| `id` | Yes | App identity, validated by platform | Stable App ID. It must match the App directory name. |
| `title` | Yes | App declaration | User-facing App title. |
| `description` | Yes, may be empty | App declaration | Concise description of the App's purpose. |
| `app_version` | Yes | App declaration | Version of the generated App, independent of `manifest_version`. |
| `intents` | Yes, may be empty | App declaration | Short natural-language examples that may later help discover the App. |
| `created_at` | Yes | Platform | Original creation timestamp, preserved across updates. |
| `updated_at` | Yes | Platform | Timestamp of the latest explicit App write. |

`description` and `intents` are allowed to be empty in Phase 1 because existing
creation paths cannot yet populate them reliably. Establishing the contract
does not require inventing low-quality content.

### Intentionally absent fields

`capabilities` is not included in Version 1. Its vocabulary and enforcement
semantics should be designed together with the permission model. A declaration
must not be confused with an authorization grant.

The Manifest also does not embed graph schemas, source file contents, model
prompts, revision history, or user data.

## Compatibility and migration behavior

### Legacy metadata with valid identity

For a valid legacy object such as:

```json
{
  "id": "legacy-widget",
  "title": "Legacy Widget",
  "created_at": "2026-07-01T00:00:00+00:00",
  "updated_at": "2026-07-01T00:00:00+00:00"
}
```

the reader returns a normalized in-memory Version 1 representation using
defaults:

```json
{
  "manifest_version": 1,
  "id": "legacy-widget",
  "title": "Legacy Widget",
  "description": "",
  "app_version": "0.1.0",
  "intents": [],
  "created_at": "2026-07-01T00:00:00+00:00",
  "updated_at": "2026-07-01T00:00:00+00:00"
}
```

Reading this otherwise valid legacy file should not rewrite it merely to add
defaults. This avoids modifying a workspace during listing and keeps migration
observable.

### Missing metadata

Current behavior creates metadata when an App has `index.html` but no
`metadata.json`. Phase 1 should preserve that compatibility behavior unless a
separate decision intentionally changes it.

The repaired file should use the Version 1 shape. The title may continue to be
derived from the HTML `<title>` element, falling back to a humanized App ID.

This is different from silently upgrading an existing valid legacy file:
repairing a missing required file restores an already established invariant,
while adding optional fields to a valid file is a migration.

### Explicit create or update

An explicit App write produces Version 1 metadata.

When updating an existing App:

- preserve a valid existing `created_at`;
- replace `updated_at` with the current UTC time;
- preserve existing descriptive fields if the caller does not supply
  replacements;
- never discard future or unknown fields by accident without an explicit
  policy.

The last point affects the implementation shape. Reconstructing the entire
object from only `id` and `title`, as the current writer does, would erase
Manifest declarations on every source-code update. Phase 1 must define a
merge/normalization path before new fields become meaningful.

### No bulk migration

Application startup and `list_apps()` should not scan and rewrite every valid
legacy file. Workspaces can be upgraded incrementally through explicit App
writes or a future dedicated migration command if one becomes necessary.

## Validation rules

### Structural validation

- The file must contain valid JSON.
- The root value must be an object.
- `manifest_version` must be an integer when present.
- `id`, `title`, `description`, and `app_version` must be strings after
  normalization.
- `intents` must be a list of non-empty strings after normalization.
- timestamps must be strings in the existing ISO 8601 format.

### Identity validation

The normalized `id` must equal the App directory name.

The directory is the storage lookup boundary and API path component. Allowing a
different ID inside the file would make listing, loading, updating, and
deleting disagree about the identity of the same App.

Phase 1 should reject the mismatched Manifest for normal use rather than trust
either value silently. A future repair tool may offer an explicit resolution.

### Version validation

- Missing `manifest_version` means legacy metadata and is normalized as
  Version 1.
- `manifest_version: 1` is supported.
- Unsupported versions must not be interpreted using Version 1 rules.

Forward compatibility should favor a clear "unsupported Manifest version"
diagnostic over silently accepting semantics the current runtime does not
understand.

### Unknown fields

The reader should tolerate unknown fields so that a newer writer does not make
an App unreadable solely because it added non-conflicting information.

The writer should preserve unknown fields when updating existing metadata,
unless a field conflicts with a platform-owned value. This reduces destructive
downgrades. Version compatibility is still controlled by
`manifest_version`; accepting unknown fields does not mean accepting an
unsupported schema version.

## Error behavior

The current implementation often converts metadata failures into a missing App
without exposing why. Once Manifest data influences system decisions, invalid
and absent state should be distinguishable.

Phase 1 should define a small diagnostic boundary, for example:

- missing metadata that can be repaired: repair and continue;
- malformed JSON or invalid field types: exclude the App from normal results
  and emit a diagnostic;
- ID mismatch: exclude and emit a diagnostic;
- unsupported version: exclude and emit a diagnostic;
- missing source entry point (`index.html`): treat the directory as incomplete.

The first implementation does not need a new UI or persistent error database.
A structured exception internally plus standard logging may be sufficient.
The important requirement is that tests can observe and distinguish the
failure classes and that one invalid App does not prevent other Apps from being
listed.

## Expected API compatibility

`GET /api/apps` may return additional fields, but it must continue to provide:

```text
id
title
created_at
updated_at
```

`GET /api/apps/{app_id}` should retain its current source payload:

```text
id
title
html
css
js
```

Phase 1 does not require exposing the complete Manifest through this source
endpoint. If future clients need it, that API shape should be discussed
separately rather than added incidentally.

## Proposed implementation boundary

If this design is accepted, the implementation should remain small:

1. Add a typed internal representation and normalization/validation logic,
   either in `backend/app_manager.py` or a focused module such as
   `backend/app_manifest.py`.
2. Route metadata reads and writes in `AppManager` through that logic.
3. Preserve the existing public method signatures unless a concrete test
   demonstrates the need to change them.
4. Extend `tests/backend/test_app_manager.py` with the compatibility and error
   cases below.
5. Update `backend/UML.md` only if a new public core class or public service
   signature is introduced, following the repository's agent rules.

No router, frontend, permission, graph database, or agent-harness behavior
should change in the same PR.

## Test-first acceptance cases

The implementation PR should define these cases before production code:

1. **Legacy read:** old metadata loads with normalized defaults and is not
   rewritten.
2. **Missing metadata repair:** an App with `index.html` but no metadata gets a
   valid Version 1 file, preserving current behavior.
3. **New App write:** a newly created App receives a valid Version 1 file.
4. **Update preservation:** updating App source preserves `created_at`,
   descriptive fields, and tolerated unknown fields while changing
   `updated_at`.
5. **Malformed JSON isolation:** one malformed App does not prevent valid Apps
   from being listed.
6. **Wrong root type:** a JSON array or scalar is rejected as invalid metadata.
7. **ID mismatch:** metadata whose `id` differs from the directory is not
   silently accepted.
8. **Unsupported version:** a future `manifest_version` is not interpreted as
   Version 1.
9. **Intent normalization:** invalid intent entries do not enter normalized
   routing data.
10. **API compatibility:** list results retain the four fields used by the
    current frontend.

## Execution-path simulations

### Existing App is listed

```text
App Store or router
  -> AppManager.list_apps()
  -> read legacy metadata
  -> normalize defaults in memory
  -> return existing API fields plus optional Manifest fields
  -> do not rewrite the valid legacy file
```

### App has no metadata

```text
AppManager.list_apps() or get_app_files()
  -> metadata missing
  -> verify index.html exists
  -> derive title
  -> write Version 1 metadata
  -> continue using the repaired App
```

### Agent updates App source

```text
AgentOrchestrator
  -> AppManager.create_or_update_app(...)
  -> load and validate existing metadata
  -> merge platform-owned and preserved declaration fields
  -> write source and Version 1 metadata
  -> existing get_app_files() payload remains compatible
```

### One App has malformed metadata

```text
AppManager.list_apps()
  -> valid App A: normalize and include
  -> invalid App B: diagnose and exclude
  -> valid App C: normalize and include
  -> caller receives A and C rather than a failed whole-workspace scan
```

### Manifest declares future intents

```text
AppManager.list_apps()
  -> return normalized intent hints
  -> current router may receive additional keys
  -> no Phase 1 routing behavior is promised or changed
```

This last boundary is intentional: the contract can be established before a
separate routing design decides how to rank, trust, or generate intent hints.

## Risks and mitigations

### Risk: The Manifest becomes a catch-all configuration file

Mitigation: require each new field to define its owner, consumer, validation,
and security meaning. Defer fields without an active consumer or stable
semantics.

### Risk: Source updates erase declarations

Mitigation: merge with validated existing metadata and test preservation.

### Risk: Intent hints are treated as authoritative

Mitigation: document them as discovery hints only. They are not permissions,
proof of behavior, or a substitute for source/runtime checks.

### Risk: Compatibility behavior hides corruption

Mitigation: repair only the already supported missing-file case. Do not
silently replace malformed, mismatched, or unsupported Manifests.

### Risk: The schema is introduced without useful data

Mitigation: permit empty `description` and `intents` initially. A later
generation or editing workflow can populate them once its quality and
ownership rules are designed.

## Decisions requested

1. Should Phase 1 evolve `metadata.json` as proposed, rather than introduce a
   separate `manifest.json`?
2. Should `capabilities` remain deferred until its permission and enforcement
   semantics are defined?
3. Is preserving missing-metadata repair while avoiding implicit upgrades of
   valid legacy files the correct compatibility boundary?
4. Should the first implementation be limited to Manifest normalization,
   validation, `AppManager` integration, and tests?
