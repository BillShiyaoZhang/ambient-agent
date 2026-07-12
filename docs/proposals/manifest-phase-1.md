# Ambient App Manifest: Phase 1 Design

## Status

Discussion proposal. The design is limited to a standalone storage contract,
compatibility behavior, validation boundaries, and tests. It does not change
runtime behavior or implement the proposed UX by itself.

## Summary

Phase 1 proposes adding a versioned `manifest.json` for each generated App:

```text
workspace/apps/<app-id>/
├── manifest.json
├── index.html
├── style.css
└── controller.js
```

`manifest.json` makes identity and purpose explicit for the platform. Relevant
fields can later support user-readable change explanations, but Phase 1 does
not add frontend UI, semantic routing, permissions, activation, or rollback.

Legacy `metadata.json` may remain readable during development and migration.
It is not part of the intended final layout.

## Assumptions

- `manifest.json` is the authoritative declaration when present and valid.
- The App directory name remains the storage identity boundary.
- Existing public APIs must retain the fields used by the current frontend.
- Compatibility must not turn malformed or unsupported Manifests into
  apparently valid legacy Apps.
- The first implementation should modify only the Manifest/AppManager path and
  its tests.

## Goals

1. Define a small, explicitly versioned standalone Manifest contract.
2. Preserve loading and listing of legacy Apps during a bounded compatibility
   period.
3. Centralize normalization and validation near `AppManager`.
4. Define observable behavior for malformed, mismatched, and unsupported
   Manifests.
5. Prevent source updates from accidentally erasing declarations.
6. Establish tests before any router or UX depends on new fields.

## Non-goals

Phase 1 will not:

- change router selection using Manifest intents;
- implement capability declarations or grant runtime permissions;
- implement candidate staging, atomic activation, rollback, or recovery;
- modify graph schema registration or approval;
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

Platform-maintained timestamps do not need to be part of the App declaration.
If the current API still requires `created_at` and `updated_at`, the
compatibility implementation may derive or retain that state separately while
the migration boundary is established. The implementation PR should document
the selected storage source rather than quietly adding those fields back to
the Manifest.

`description` and `intents` may be empty because current creation paths cannot
yet populate them reliably. The contract should not invent low-quality
content merely to satisfy a non-empty constraint.

### Intentionally absent fields

Version 1 does not include capabilities, permissions, graph schemas, source
contents, model prompts, revision history, or user data.

## Read precedence and compatibility

### Valid standalone Manifest

When `manifest.json` exists, the reader validates and uses it. A malformed,
identity-mismatched, or unsupported Manifest is an error; it must not silently
fall back to `metadata.json`, because doing so would hide corruption or an
unsupported contract.

### Legacy App with only `metadata.json`

During the compatibility period, a legacy App may be normalized in memory:

```json
{
  "manifest_version": 1,
  "id": "legacy-widget",
  "title": "Legacy Widget",
  "description": "",
  "app_version": "0.1.0",
  "intents": []
}
```

Reading a valid legacy App should not necessarily rewrite it. Migration can
occur on an explicit App update or through a future dedicated command.

### App with neither declaration file

The current implementation repairs Apps that have `index.html` but no
`metadata.json`. Phase 1 should preserve equivalent compatibility: derive a
title, create a valid Version 1 Manifest, and continue.

This is repair of a missing invariant, not silent replacement of an invalid
Manifest.

### Explicit create or update

An explicit App write should produce a complete Version 1 `manifest.json`.
When updating:

- preserve existing declarations unless replacements are supplied;
- update `app_version` according to an explicit policy rather than guessing;
- preserve tolerated unknown fields when safe;
- write the Manifest and source through a failure-aware sequence;
- do not require a permanent mirrored `metadata.json`.

Phase 1 does not promise atomic multi-file activation. The implementation must
nevertheless avoid claiming success after a partial write and should use the
safest existing filesystem write pattern available in the repository.

## Validation rules

### Structural validation

- The file must contain valid JSON.
- The root value must be an object.
- `manifest_version` must be an integer.
- `id`, `title`, `description`, and `app_version` must be strings.
- `intents` must be a list of non-empty strings.
- Reasonable size limits should prevent an accidental or malicious Manifest
  from becoming unbounded input.

### Identity validation

The Manifest `id` must equal the App directory name. The directory is the
storage lookup boundary and API path component; silently accepting two
identities would make listing, loading, updating, and deletion disagree.

### Version validation

- `manifest_version: 1` is supported.
- Unsupported versions are diagnosed and excluded from normal use.
- A missing version is valid only on the legacy `metadata.json` compatibility
  path, not in a new `manifest.json`.

### Unknown fields

Readers may tolerate unknown fields within a supported contract version.
Writers should preserve them when doing so cannot conflict with
platform-validated identity. Tolerating fields is not the same as accepting an
unsupported `manifest_version`.

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

If accepted, the implementation PR should:

1. Add a focused Manifest representation and validator, either close to
   `AppManager` or in `backend/app_manifest.py`.
2. Route App declaration reads and writes through that boundary.
3. Add a temporary, explicit legacy `metadata.json` adapter.
4. Preserve current public method signatures unless tests require a change.
5. Extend `tests/backend/test_app_manager.py`.
6. Update UML documentation only if public core classes or signatures change.

No router, frontend, permission, graph database, or agent-harness behavior
should change in the same PR.

## Test-first acceptance cases

1. **New App write:** creates a valid standalone Version 1 Manifest.
2. **Standalone read:** a valid Manifest loads without consulting legacy
   metadata.
3. **Legacy read:** metadata-only App loads through compatibility without an
   incidental rewrite.
4. **Missing declaration repair:** App with `index.html` and neither file gets
   a valid Manifest.
5. **Update preservation:** a source update retains declarations and tolerated
   unknown fields.
6. **Manifest precedence:** an invalid Manifest does not silently fall back to
   valid-looking legacy metadata.
7. **Malformed JSON isolation:** one malformed App does not prevent other Apps
   from being listed.
8. **Wrong root type:** an array or scalar is rejected.
9. **ID mismatch:** Manifest and directory identity cannot diverge silently.
10. **Unsupported version:** a future version is not interpreted as Version 1.
11. **Intent validation:** invalid entries do not enter normalized discovery
    data.
12. **Oversized input:** an unreasonable Manifest is rejected predictably.
13. **Partial write failure:** failure is reported and is not presented as a
    completed update.
14. **API compatibility:** current frontend fields remain available.

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
  -> normalize through explicit legacy adapter
  -> do not rewrite during a valid read
```

### Missing declaration

```text
AppManager.list_apps() or get_app_files()
  -> both declaration files absent
  -> verify index.html exists
  -> derive identity and title
  -> write Version 1 manifest.json
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
  -> load current validated declaration or legacy normalization
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

### Two files become permanent

Mitigation: make `metadata.json` an explicitly temporary adapter, define
Manifest precedence, and avoid mirrored writes as the final design.

### Source and Manifest diverge

Mitigation: validate both through one AppManager update boundary, report
partial failures, and design candidate activation separately before promising
atomic updates.

### Declarations are mistaken for proof

Mitigation: document that the Manifest describes declared identity and
purpose. It does not verify code behavior or grant permissions.

### Compatibility hides corruption

Mitigation: fall back only when `manifest.json` is absent, never when it is
present but invalid.

### Schema becomes a catch-all

Mitigation: require a clear owner, consumer, validation rule, and security
meaning for every future field.

## Acceptance decision requested

Is this a suitable boundary for a future implementation PR: standalone
`manifest.json`, temporary legacy compatibility, validation and tests only,
with the UX and activation lifecycle kept as separate follow-up work?
