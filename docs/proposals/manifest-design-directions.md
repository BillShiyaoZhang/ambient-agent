# Ambient App Manifest: Design Directions

## Status

Discussion proposal. This document does not define an accepted implementation
plan and does not change runtime behavior.

## Context

Ambient Agent currently persists each generated App in a directory such as:

```text
workspace/apps/<app-id>/
├── metadata.json
├── index.html
├── style.css
└── controller.js
```

`AppManager` creates and reads `metadata.json`, `list_apps()` supplies that
metadata to both the App Store and the intent-routing path, and
`get_app_files()` supplies the latest source to the runtime and conversation
context.

The existing metadata contract is intentionally small:

```json
{
  "id": "morning-planner",
  "title": "Morning Planner",
  "created_at": "2026-07-12T00:00:00+00:00",
  "updated_at": "2026-07-12T00:00:00+00:00"
}
```

This is sufficient for storage and display, but it does not provide a
versioned, machine-readable description of an App's purpose. A future router,
permission layer, or App evolution workflow should not need to infer that
purpose from an ID or repeatedly inspect the full source tree.

The goal of a Manifest should therefore be modest: establish one stable place
for App identity and descriptive declarations. It should not become a shortcut
for implementing several unrelated product features at once.

## Design principles

1. **Preserve existing Apps.** An App created before the Manifest work should
   continue to load, appear in the App Store, and be available to the router.
2. **Keep one source of truth where possible.** Two overlapping metadata files
   create synchronization rules that the current system does not yet need.
3. **Separate declaration from enforcement.** A declared capability must never
   grant runtime permission by itself.
4. **Version the contract, not every future idea.** The initial schema should
   contain only fields with a clear meaning and owner.
5. **Avoid hidden migration.** Compatibility repair may preserve current
   behavior, but reading an otherwise valid legacy file should not silently
   rewrite it merely to add optional fields.
6. **Make invalid state observable.** Since routing may eventually depend on
   the Manifest, parse and validation failures should have defined behavior
   rather than being indistinguishable from a valid empty declaration.

## Direction A: Extend metadata without a schema version

The smallest change is to add descriptive fields to the current file:

```json
{
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

### Advantages

- Minimal implementation and migration cost.
- Preserves the existing file layout and API response shape.
- Easy to consume from `AppManager.list_apps()`.

### Limitations

- There is no explicit way to distinguish old and new contract versions.
- Future readers cannot reliably decide which validation rules apply.
- Optional additions can gradually turn the file into an unbounded collection
  of unrelated settings.

## Direction B: Evolve `metadata.json` into a versioned Manifest

This direction retains the existing file and adds an explicit schema version:

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

Here, `manifest_version` versions the file contract while `app_version`
describes the generated App. They are independent.

### Advantages

- Introduces an explicit evolution boundary without adding a second file.
- Reuses the existing `AppManager`, API, App Store, and router data path.
- Supports backward-compatible reads of existing metadata.
- Keeps the first implementation focused and reversible.

### Limitations

- The name `metadata.json` is less explicit than `manifest.json`.
- Platform-maintained timestamps and App declarations remain in one object, so
  field ownership must be documented.
- The schema still needs discipline to avoid accumulating unrelated concerns.

## Direction C: Add a separate `manifest.json`

This direction separates declarative App information from platform-maintained
metadata:

```text
workspace/apps/<app-id>/
├── manifest.json
├── metadata.json
├── index.html
├── style.css
└── controller.js
```

For example, `manifest.json` could contain identity, purpose, App version, and
intent hints, while `metadata.json` retains timestamps and other platform
state.

### Advantages

- Provides the clearest conceptual boundary between declaration and runtime
  metadata.
- Makes `manifest.json` an obvious future integration point.
- Allows the two contracts to evolve independently.

### Limitations

- Creates two overlapping files immediately.
- Requires precedence and consistency rules for fields such as `id` and
  `title`.
- Expands missing-file, migration, write-order, and recovery behavior before
  the project has demonstrated a need for that complexity.
- Increases the Phase 1 implementation and test surface without changing the
  user-visible outcome.

## Recommendation

For Phase 1, use **Direction B**: incrementally evolve the existing
`metadata.json` into a small, versioned Manifest.

This recommendation is based on the current architecture rather than a claim
that one-file storage is universally preferable. `metadata.json` is already
the object returned by `list_apps()`, consumed by the App Store, and passed to
the intent router. Reusing that path provides a useful contract with the
smallest compatibility risk.

A separate `manifest.json` can still be introduced later if a concrete need
emerges—for example, if signed or developer-authored declarations must be
managed independently from platform-maintained state. Phase 1 should not pay
that coordination cost in advance.

## Fields deliberately deferred

### Capabilities and permissions

Capability declarations should be deferred until the runtime permission model
defines:

- the supported capability vocabulary;
- whether declarations are informational, required, or user-approved;
- which component validates and enforces them;
- how an App behaves when declarations and observed behavior disagree.

Adding an empty `capabilities` array now would appear extensible, but it would
freeze a field name before its security semantics are clear. More importantly,
a Manifest declaration must never be treated as authorization.

### Data schemas and migrations

The project already has a graph schema approval path. The Manifest should not
duplicate graph schema definitions or silently introduce an App data migration
protocol in Phase 1.

### App revision history and rollback

`app_version` can identify the App's declared version, but it does not imply
that the platform already supports snapshots, rollback, atomic activation, or
data migration. Those reliability mechanisms require a separate design.

### Conversation-to-Skill and automatic recommendations

Intent hints can provide future routing input. Phase 1 should not introduce
behavior that proactively creates, recommends, or modifies Apps.

## Questions for maintainers

1. Is evolving the current `metadata.json` preferable to introducing a second
   `manifest.json` in the first phase?
2. Should capabilities remain out of the schema until their enforcement model
   is designed?
3. Is it acceptable for Phase 1 to define intent hints without changing router
   behavior yet?
4. Are there use cases that already require platform metadata and App
   declarations to be stored independently?
