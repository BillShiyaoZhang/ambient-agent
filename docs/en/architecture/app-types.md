# App types and feature declarations

An App provides data and processing tools to the Agent and an interactive interface to the user. Types provide stable discovery vocabulary; Apps of the same type may differ in implementation, interface, and feature coverage. The catalogue is extensible and Apps may declare multiple types. Types do not grant permissions or install private ontology schemas.

## Manifest contract

Manifest V2 accepts an optional `app_spec`. Existing Apps without it remain loadable and unclassified. Never infer types from a title, schema references, or capability grants. The independently versioned standard currently uses integer `spec_version: 1`.

```json
{
  "app_spec": {
    "spec_version": 1,
    "types": ["calendar", "tasks"],
    "features": [
      {"id": "calendar.events", "status": "implemented", "surfaces": ["data", "ui"]},
      {"id": "calendar.reminders", "status": "planned", "surfaces": []},
      {"id": "tasks.items", "status": "partial", "surfaces": ["ui"], "notes": "List interface only"}
    ]
  }
}
```

`types` is a nonempty ordered list of unique type IDs. Its first entry is the primary display type. Standard types are `calendar`, `tasks`, `notes`, `contacts`, `documents`, `messaging`, `finance`, `media`, `dashboard`, and `utility`. Custom types use `custom:<namespace>` and custom features use `custom:<namespace>.<feature>`, with lowercase kebab-case namespace and feature names; the matching custom type must be declared. Standard features must belong to a declared type. Unknown standard IDs, duplicates, unsupported versions, and unknown fields are rejected. Each specification is limited to 20 types, 100 features, 200 characters per ID, and 2000 characters per feature note.

Feature status is `implemented`, `partial`, or `planned`. Implemented and partial features require at least one actual surface: `data` for storage and data, `tools` for Agent-invokable processing, or `ui` for visual interaction. Planned features require empty surfaces. `notes` is optional. Undeclared standard features display as `not_declared`; an App need not implement every feature of a type.

Statuses and surfaces are author declarations. Validation checks structure and classification, not runtime correctness. Detail views label these as implementation declarations and do not display certification badges. Permissions still use `capabilities`, while data still uses canonical `schema_refs`. A `tools` declaration creates no callable tool or permission.

## Catalogue, metadata, and App Center

- `GET /api/app-types` returns `{spec_version: 1, types: [...]}`. Each type has `id`, bilingual `title` and `description`, and standard `features` with `id` and bilingual `title`. The backend owns the catalogue shared by the frontend and Coding Agent.
- App listing, file details, and App Center items include declared `app_spec`; unclassified Apps may omit it or return null. App Center state includes `app_type_catalog`.
- `PATCH /api/apps/{app_id}` can add or replace `app_spec`, applying the Manifest validation rules. Null clears classification. The operation changes Manifest metadata only. UI configuration edits types; API authors may submit complete feature declarations.
- App Center keeps its source filters and adds purpose type and unclassified filters. Search includes type and feature names. An App can be found through any declared type without altering persistent launcher layout.
- Details show types, declared feature status and surfaces, undeclared standard features, and custom features. Bound executable-capability UIs retain their `app_spec` in the corresponding App Center entry.

## Generation and distribution

The read-only `list_app_specs(app_id?)` tool returns identity, description, version, and complete declarations. The selector can be omitted for small catalogues; for large workspaces, use `list_available_apps` to obtain IDs and read each App with `app_id`, keeping individual reads within the tool output limit. An unknown App returns an empty list.

The Coding Agent receives the current catalogue in a dedicated prompt section and the Manifest template. A new App template remains unclassified until its implementation is known. The Agent selects types and feature declarations from actually delivered behavior. A grant alone never establishes an implemented feature. Modifications preserve existing declarations and update them to match the resulting implementation. `app_spec` may be edited while identity, schema references, and approved grants remain immutable.

Router context explicitly distinguishes implemented, partial, and planned feature declarations and their surfaces. It describes declarations as author supplied, never as verified behavior or callable tools. Legacy Apps remain visibly unclassified. Staging validation and promotion keep enforcing the existing Manifest and Runtime Contract boundaries.

Because `app_spec` travels with the Manifest, future store packages can use it for classification and comparison. This change delivers the standard, validation, generation guidance, and App Center discovery. App Center remains the installed workspace catalogue and Skill Market remains separate; online App publishing, installation by other users, and behavior certification are outside this change.

## Acceptance

Tests cover standard, custom, and multiple types; feature membership; status and surface constraints; invalid input; legacy round trips; atomic metadata updates; catalogue API and Agent tools; App Center filtering, search, and details; bound UI metadata; Coding Agent catalogue and declaration guidance; and router declaration wording. New behavior starts with failing tests, then passes focused regression checks, the existing suites, lint, build, and UML verification.
