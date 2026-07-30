# Widgets and App Center

A “Widget” is a React UI rendered in the workspace. An “App” is a Widget with a persistent Manifest V2 and Controller. A “Capability” is an invokable backend action in App Center that may not have a UI. An “Instruction Skill” is `SKILL.md` guidance injected into an Agent turn on demand. Skills, Capabilities, and Apps can be composed but cannot replace one another. An App accesses host or external resources only through approved capability grants.

## 1. App artifacts

```text
workspace/apps/<app-id>/
├── manifest.json      # Required: identity, schema refs, exact capability grants
├── controller.js      # Required: default-exported React component
├── README.md          # Optional App documentation
└── data/              # Optional private runtime data constrained by file.* grants
```

Manifest V2 is the only loadable and publishable format:

```json
{
  "manifest_version": 2,
  "id": "task-board",
  "title": "Task Board",
  "description": "Manage tasks",
  "app_version": "1.0.0",
  "intents": ["manage tasks"],
  "schema_refs": ["Task"],
  "capabilities": [
    {"id": "graph.query", "scope": {"entities": ["Task"]}},
    {
      "id": "graph.mutate",
      "scope": {"entities": ["Task"], "operations": ["create", "update", "delete"]}
    }
  ]
}
```

`id` matches the directory name and uses lowercase kebab case. An App with no external access still writes `"capabilities": []`. V1, top-level `data_sources`, `index.html`, `style.css`, `layout.json`, and `index.jsx` are not new-version artifacts and are neither implicitly migrated nor loaded.

## 2. Controller and minimal SDK

`controller.js` default-exports a React component. After transpilation, the host constructs an `ambient` capability membrane only from approved Manifest grants.

```javascript
export default function TaskBoard({ ambient }) {
  const { useEffect, useState } = ambient.react;
  const { Card, Text } = ambient.components;
  const [tasks, setTasks] = useState([]);

  useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);
  return ambient.html`<${Card} title="Tasks"><${Text} text=${`${tasks.length} items`} /><//>`;
}
```

The example injects `ambient.graph.subscribe` only when a `graph.query` grant exists and includes `Task`. Without `network.request`, `ambient.net` does not exist. Without a `file.*` grant, `ambient.files` does not exist. The backend reloads the persistent Manifest and authorizes every request again.

## 3. Creation, modification, and publication

```mermaid
flowchart LR
    Request[User request] --> Run[internal_agent Run]
    Run --> Plan[Development plan approval]
    Plan --> Align[Schema + capability alignment]
    Align --> Approval[User approves exact proposal]
    Approval --> Contract[Immutable Runtime Contract]
    Contract --> Stage[Coding Agent staging]
    Stage --> Verify[Code use / Manifest / Schema checks]
    Verify --> Promote[Atomic promotion]
    Promote --> Store[App Center / workspace]
```

- A denied capability proposal prevents the Coding Agent from starting.
- For an existing App, current grants enter the proposal. Any expansion or replacement requires explicit approval.
- The Coding Agent receives only approved schemas, grants, the SDK subset, and allowed files.
- Controller capability IDs, source IDs, catalog IDs, and action IDs must be statically extractable string literals.
- Verification requires normalized staging Manifest grants to equal the Runtime Contract and code use to be a subset.
- The live directory remains unchanged until approval and verification complete. Recovery checks artifact hash, grants digest, and Run effect records before promotion.

See [Widget Capability Security](/en/architecture/capability-security.md) for the full contract.

## 4. App Center

`GET /api/app-store` combines only the `generated_app`, Instruction Skill, and executable-capability items installed in the current workspace and maintains their launcher layout. Installable but not yet installed Skills come from the separate `GET /api/skill-market`; installation, enable, update, and uninstall use the separate Skill API. A Market response is not the source of truth for App Center layout, and uninstalled items are never written into that layout.

A headless Capability can run directly as a structured action or explicitly start a Durable UI-generation Run. Its generated UI requests a `capability.invoke` grant restricted to the target `catalog_id + action_id`; it never binds a provider, MCP server, or tool name directly, and still passes through the existing approval, staging, verification, and atomic-publication path. An Instruction Skill instead uses `launch_mode = "details"`: App Center provides details, enable/disable, and uninstall, while a relevant Agent turn loads its guidance on demand. Being headless does not make it a system-level Tool, and this version offers no Run or UI-generation entry point for a pure Instruction Skill.

Items are `ready`, `needs_ui`, `generating`, or `unavailable`. Layout uses revision-based optimistic concurrency. A conflict returns `409`, after which the client reloads before submitting again.

A generated App icon opens its management menu through right-click or a hold of about 500 ms. Movement beyond the gesture tolerance, pointer release, or cancellation must cancel the hold; a successful hold must not subsequently launch the App. The menu provides details, property configuration, rename, and uninstall actions, while the keyboard context-menu path remains available.

`PATCH /api/apps/{app_id}` updates only user-manageable Manifest presentation properties: `title`, `description`, `app_version`, and `intents`. It is a partial update; unknown fields, empty updates, and values that violate Manifest V2 return `422`, while a missing App returns `404`. Rename changes `title` only. The stable App ID, directory, grants, schema references, and Controller remain unchanged. After a successful update, App Center and any open window refresh to the latest properties.

## 5. Data and capability boundaries

- The Graph stores only user context. App caches, cursors, UI state, and raw provider payloads live under `data/`.
- `graph.query` and `graph.mutate` are separate grants further constrained by entity, operation, and edge type.
- A `network.request` grant declares the public HTTPS origin, paths, methods, and response limit. The Controller never supplies a full URL.
- `file.*` accesses only `app://data/`; it cannot read the Manifest, Controller, sessions, Graph, or credentials.
- `capability.invoke` calls only exact approved App Center actions. Direct `ambient.mcp` has been removed from the Widget SDK.
- A grant only allows the App to request an operation. Run interactions, adapter spawn permission, input/output schemas, idempotency, and recovery policy remain in force.
- Skill installation records live in workspace SQLite, while the validated `SKILL.md` and Market descriptor live in content-addressed snapshots. They are not App data and never enter the ontology or KG. Skill `ontology_refs` reference existing canonical schemas only.

Runtime errors use stable `code`, `capability`, `operation`, `hint`, and safe `details`, and write bounded audit/diagnostic records for later repair.

See [Agent Skills](/en/agent/skills.md) for the complete installation, context, and security contract.
