# Ambient App Manifest: Phase 1 Design

## Status

Accepted implementation design. The scope is limited to a standalone storage
contract, validation, one-time repair or migration, `AppManager` integration,
API compatibility, and tests. It does not implement router behavior, graph
mutation, or the proposed UX.

## Summary

Phase 1 adds a versioned `manifest.json` for each generated App:

```text
workspace/apps/<app-id>/
├── manifest.json
├── index.html
├── style.css
└── controller.js
```

`manifest.json` makes identity, purpose, discovery hints, and central graph
schema associations explicit for the platform. Those fields can supply compact
context to routing and later user-readable change explanations, but Phase 1
does not change router scoring, graph mutation, frontend UI, permissions,
activation, or rollback.

Legacy `metadata.json` may exist temporarily while the migration is developed.
In the final implementation it is consumed only by a one-time migration and
deleted after the resulting Manifest has been written and verified. It is not
part of normal runtime operation.

## Assumptions

- `manifest.json` is the authoritative declaration when present and valid.
- The App directory name remains the storage identity boundary.
- Existing public APIs must retain the fields used by the current frontend.
- A valid Manifest read does not mutate App files.
- Migration must not turn malformed or unsupported Manifests into apparently
  valid legacy Apps.
- The first implementation should modify only the Manifest/AppManager path and
  its tests.

## Goals

1. Define a small, explicitly versioned standalone Manifest contract.
2. Repair or migrate existing Apps to the standalone contract without
   retaining a metadata fallback.
3. Centralize normalization and validation near `AppManager`.
4. Define observable behavior for malformed, mismatched, and unsupported
   Manifests.
5. Prevent source updates from accidentally erasing declarations.
6. Establish tests before any router or UX depends on new fields.

## Non-goals

Phase 1 will not:

- change router selection using Manifest context;
- introduce a formal capability taxonomy or grant runtime permissions;
- implement candidate staging, atomic activation, rollback, or recovery;
- modify graph schema registration or approval;
- authorize or execute graph mutations from Manifest declarations;
- implement the user-facing change preview;
- add a general plugin or package format;
- require an eager bulk rewrite of all workspaces;
- treat declarations as proof of runtime behavior.

## Proposed Version 1 schema

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
  "schema_refs": [
    "Task",
    "Event"
  ]
}
```

### Field semantics

| Field | Required | Owner | Meaning |
|---|---:|---|---|
| `manifest_version` | Yes | Platform contract | Integer version of the Manifest schema. Phase 1 supports only `1`. |
| `id` | Yes | App declaration, validated by platform | Stable App ID; must match the directory name. |
| `title` | Yes | App declaration | User-facing App title. |
| `description` | Yes, may be empty | App declaration | Concise description of the App's purpose. |
| `app_version` | Yes | App declaration | Version of the generated App, independent of `manifest_version`. |
| `intents` | Yes, may be empty | App declaration | Natural-language discovery hints for possible future routing. |
| `schema_refs` | Yes, may be empty | App declaration, validated structurally by platform | IDs of related schemas in the central `graph_schemas` registry. |

Platform-maintained timestamps do not need to be part of the App declaration.
The current API still requires `created_at` and `updated_at`, so Phase 1 must
preserve their public shape while keeping them outside the Manifest contract.
The implementation must first audit every current reader and writer, then
choose a platform-owned source that does not recreate a second App declaration
or silently change the timestamps' logical meaning. Required-file modification
times are not assumed to be equivalent to logical creation and update times.

`description`, `intents`, and `schema_refs` may be empty because current
creation paths cannot yet populate them reliably. The contract should not
invent low-quality content or schema associations merely to satisfy a
non-empty constraint.

### Intentionally absent fields

Version 1 does not include a formal `capabilities` taxonomy, permissions, full
graph schema definitions, source contents, model prompts, revision history, or
user data. Its minimum capability context is the combination of
`description`, `intents`, and `schema_refs`.

## Read, repair, and migration behavior

### Valid standalone Manifest

When `manifest.json` exists, the reader validates and uses it. A malformed,
identity-mismatched, or unsupported Manifest is an error; it must not silently
fall back to `metadata.json`, because doing so would hide corruption or an
unsupported contract.

### Legacy App with only `metadata.json`

A one-time migration constructs a Version 1 candidate:

```json
{
  "manifest_version": 1,
  "id": "legacy-widget",
  "title": "Legacy Widget",
  "description": "",
  "app_version": "0.1.0",
  "intents": [],
  "schema_refs": []
}
```

The migration validates the candidate, writes `manifest.json` through a
failure-aware path, rereads and validates it, and deletes `metadata.json` last.
It must be idempotent. Once migrated, the App is read only from
`manifest.json`.

### App with neither declaration file

The current implementation repairs Apps that have `index.html` but no
declaration. Phase 1 preserves that useful behavior by deriving a title,
creating and validating a Version 1 Manifest, and continuing.

This is repair of a missing invariant, not silent replacement of an invalid
Manifest.

### Explicit create or update

An explicit App write should produce a complete Version 1 `manifest.json`.
When updating:

- preserve existing declarations unless replacements are supplied;
- preserve `app_version` unless the caller explicitly supplies a replacement;
- distinguish an omitted declaration field from an explicitly supplied empty
  value;
- write the Manifest and source through a failure-aware sequence;
- never write or preserve a mirrored `metadata.json`.

Phase 1 does not promise atomic multi-file activation. The implementation must
validate inputs before writing, replace `manifest.json` through an atomic
same-directory temporary file, report any required source or Manifest write
failure, and never claim that a partial write completed successfully. It does
not promise automatic restoration of every source file after an arbitrary
failure; candidate activation and rollback remain separate work.

## Validation rules

### Structural validation

- The file must contain valid JSON.
- The root value must be an object.
- `manifest_version` must be an integer.
- `id`, `title`, `description`, and `app_version` must be strings.
- `intents` must be a list of non-empty strings.
- `schema_refs` must be a list of unique, non-empty schema ID strings.
- Reasonable size limits should prevent an accidental or malicious Manifest
  from becoming unbounded input.

### Identity validation

The Manifest `id` must equal the App directory name. The directory is the
storage lookup boundary and API path component; silently accepting two
identities would make listing, loading, updating, and deletion disagree.

The same App ID validator is used by create, read, list, update, delete,
migration, and repair paths. Version 1 IDs are lowercase ASCII slugs matching
`^[a-z0-9]+(?:-[a-z0-9]+)*$`, are at most 64 characters, and must not be
Windows reserved device names. Resolved App paths must be direct children of
the configured Apps directory.

### Version validation

- `manifest_version: 1` is supported.
- Unsupported versions are diagnosed and excluded from normal use.
- A missing version is valid only when constructing a migration candidate from
  legacy metadata, not in a new `manifest.json`.

### Unknown fields

Version 1 rejects unknown fields. A strict contract prevents misspelled or
unreviewed declarations from silently entering router context. Future fields
require a new documented contract decision; unsupported versions remain
explicit errors.

### Schema reference validation

The Manifest layer validates the structure of `schema_refs`, including type,
empty values, duplicates, count, and length. The central graph schema registry
remains responsible for registration, approval, definitions, and runtime
validation. Phase 1 does not couple ordinary Manifest parsing to graph
mutation or schema registration.

All string lengths, list counts, and the total Manifest byte size use
centralized, documented limits with boundary tests. They are not redefined
independently inside `AppManager`.

## Error behavior

The first implementation should distinguish:

- missing declaration that can be repaired;
- malformed JSON;
- wrong root type or invalid field type;
- Manifest/directory ID mismatch;
- unsupported Manifest version;
- incomplete App with no `index.html`;
- write failure before the new declaration and source are complete.

A structured internal exception plus standard logging may be sufficient.
One invalid App must not prevent valid Apps from being listed.
Listing is deterministic by App ID. Per-App migration, repair, update, and
delete operations are serialized so concurrent operations cannot publish two
different migration results or delete migration input before verification.
The acceptance suite must exercise competing migration/update/delete attempts,
not only single-threaded success paths.

## API compatibility

`GET /api/apps` must continue to provide:

```text
id
title
created_at
updated_at
```

The first implementation may add normalized Manifest information, but it
should not change existing field meaning.

`created_at` and `updated_at` remain UTC ISO 8601 platform record fields rather
than Manifest declarations. Their post-migration source is an implementation
decision that must preserve the existing API contract and avoid a second App
declaration source; Phase 1 must resolve it from the current call sites before
code is merged.

`GET /api/apps/{app_id}` should retain:

```text
id
title
html
css
js
```

Exposing the complete raw Manifest is not required. A future UX should receive
a purpose-built summary rather than forcing clients or users to interpret raw
JSON.

## Proposed implementation boundary

The implementation PR should:

1. Add a focused Manifest representation and validator, either close to
   `AppManager` or in `backend/app_manifest.py`.
2. Route App declaration reads and writes through that boundary.
3. Add an idempotent, on-demand one-time migration or repair path that may be
   triggered by the first list/get access and removes `metadata.json` only
   after successful Manifest verification.
4. Preserve current public method signatures while allowing optional,
   explicitly supplied declaration replacements.
5. Add focused tests in `tests/backend/test_app_manifest.py` and extend
   `tests/backend/test_app_manager.py`.
6. Update UML documentation only if public core classes or signatures change.

No router, frontend, permission, graph database, or agent-harness behavior
should change in the same PR.

## Test-first acceptance cases

1. **New App write:** creates a valid standalone Version 1 Manifest.
2. **No metadata write:** new and updated Apps do not create
   `metadata.json`.
3. **Standalone read:** a valid Manifest loads without consulting legacy
   metadata.
4. **Legacy migration:** a metadata-only App is converted to a valid Manifest
   and metadata is deleted only after verification.
5. **Missing declaration repair:** App with `index.html` and neither file gets
   a valid Manifest.
6. **Update preservation:** a source update retains `description`,
   `app_version`, `intents`, and `schema_refs` unless explicitly replaced.
7. **Manifest precedence:** an invalid Manifest does not silently fall back to
   valid-looking legacy metadata.
8. **Malformed JSON isolation:** one malformed App does not prevent other Apps
   from being listed.
9. **Wrong root type:** an array or scalar is rejected.
10. **ID mismatch:** Manifest and directory identity cannot diverge silently.
11. **Unsupported version:** a future version is not interpreted as Version 1.
12. **Intent validation:** invalid entries do not enter normalized discovery
    data.
13. **Schema reference validation:** invalid, empty, or duplicate entries are
    rejected.
14. **Unknown fields:** undeclared Version 1 fields are rejected.
15. **Oversized input:** an unreasonable Manifest is rejected predictably.
16. **Path safety:** invalid IDs, traversal attempts, and non-child paths are
    rejected consistently.
17. **Partial write failure:** failure is reported and is not presented as a
    completed update.
18. **API compatibility:** current frontend fields remain available.

## Execution-path simulations

### Existing standalone App

```text
App Store or router
  -> AppManager.list_apps()
  -> read manifest.json
  -> validate version, identity, and fields
  -> map to the existing API shape
```

### Legacy App

```text
AppManager.list_apps()
  -> manifest.json absent
  -> metadata.json present
  -> acquire the App operation lock
  -> construct and validate Version 1 candidate
  -> atomically write and reread manifest.json
  -> delete metadata.json last
  -> continue from the validated Manifest
```

### Missing declaration

```text
AppManager.list_apps() or get_app_files()
  -> both declaration files absent
  -> verify index.html exists
  -> acquire the App operation lock
  -> derive identity and title
  -> atomically write Version 1 manifest.json
  -> continue
```

### Invalid Manifest beside legacy metadata

```text
AppManager.list_apps()
  -> manifest.json present
  -> validation fails
  -> diagnose and isolate the App
  -> do not hide the failure by reading metadata.json
```

### Explicit App update

```text
AgentOrchestrator
  -> AppManager.create_or_update_app(...)
  -> load current validated declaration or perform one-time migration
  -> merge supplied and preserved fields
  -> perform failure-aware writes
  -> return success only after required writes complete
```

### Future UX consumer

```text
validated current Manifest + validated candidate Manifest
  -> compare declared purpose and version
  -> Agent writes a concise explanation
  -> platform decides whether review is required
  -> user sees a readable summary, not raw JSON
```

The last path explains the intended design value but is not implemented in
Phase 1.

## Risks and mitigations

### Legacy migration loses its source

Mitigation: validate before writing, use a failure-aware Manifest write,
reread and validate the result, and delete `metadata.json` only after success.

### Source and Manifest diverge

Mitigation: validate both through one AppManager update boundary, report
partial failures, and design candidate activation separately before promising
atomic updates.

### Declarations are mistaken for proof

Mitigation: document that the Manifest describes declared identity and
purpose. It does not verify code behavior or grant permissions.

### Migration hides corruption

Mitigation: migrate only when `manifest.json` is absent, never when it is
present but invalid.

### Schema becomes a catch-all

Mitigation: require a clear owner, consumer, validation rule, and security
meaning for every future field.

## Accepted implementation boundary

Issue #2 confirms this Phase 1 boundary: standalone `manifest.json`, references
to registered central schemas, no permanent metadata fallback, validation and
tests, with router scoring, graph mutation, permissions, UX, activation, and
recovery kept as separate work.
