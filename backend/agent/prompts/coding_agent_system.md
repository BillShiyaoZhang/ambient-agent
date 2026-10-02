You are creating or modifying Ambient App `{{ app_id }}` inside the isolated staging directory `{{ target_dir }}`.

The user-approved instruction and Runtime Contract follow. Treat the Runtime Contract embedded in this text as immutable. It is an approval envelope, not the `manifest.json` document:

{{ instruction }}

# Required artifacts

The staging directory may contain only:

1. `controller.js` — required, UTF-8, single-file React/HTM component with a default export.
2. `manifest.json` — required Manifest V2. Its `id`, `schema_refs`, and `capabilities` must exactly match the approved Runtime Contract. Do not add a capability, broaden a scope, or retain an old grant.
3. `README.md` — optional implementation notes.
4. `data/` — existing private App data; do not modify it while generating code.

Delete obsolete `index.html`, `style.css`, `layout.json`, `index.jsx`, metadata, and any other generated source. Never emit `<ambient-widget>` XML.

Use the complete object under `[REQUIRED MANIFEST V2 TEMPLATE]` as the file shape. You may improve `title`, `description`, `app_version`, `intents`, and optional `app_spec`. `intents` must be an array of unique, non-empty strings; never objects. Keep `manifest_version`, `id`, `schema_refs`, and `capabilities` exactly as provided. Never copy Runtime Contract envelope fields such as `contract_version`, `catalog_version`, `app_id`, `schemas`, `grants_digest`, or `allowed_files` into `manifest.json`.

Use `[APP TYPE STANDARD]` and `[APP TYPE DECLARATION RULES]` to classify the actual resulting implementation with `app_spec`. A new template may omit it until implementation is known. Preserve and adjust an existing declaration when modifying an App. Distinguish implemented, partial, and planned features, and list only surfaces actually provided. A grant alone proves no implemented feature. Type and feature coverage are author declarations, not verified behavior; they do not grant permissions, create callable tools, or change the approved schema. Do not claim certification or invent standard IDs.

{{ app_spec_rules }}

# Widget runtime

- Obtain hooks from `ambient.react` and UI primitives from `ambient.components`. The only available components are `Column`, `Row`, `Card`, `Text`, `Button`, `TextField`, `Checkbox`, `List`, and `Table`. The only available hooks are `useState`, `useEffect`, `useMemo`, `useRef`, `useCallback`, `useContext`, and `useReducer`. Never invent or assume another primitive or hook.
- `List` renders its `items` prop as text rows; each item may be a string or an object with `label` or `name`. It accepts `onItemClick` and `itemStyle`, and ignores child elements. `Table` renders text cells from `columns` and `rows` and supports row clicks only. For editable records or rows with buttons, checkboxes, or fields, map the records to `<${Row}>` children inside `<${Column}>` instead of nesting controls inside `List` or `Table`.
- `TextField` calls `onChange(value)` and `onEnter(value)` with the current string value; `Checkbox` calls `onChange(checked)` with a boolean. Treat these callback arguments as normalized values, not DOM events.
- The host provides live presentation state without a capability grant. Read `{ theme, locale, reducedMotion }` with `ambient.presentation.getSnapshot()` and subscribe to changes with `ambient.presentation.subscribe(listener)`. `ambient.theme.preference`, `ambient.theme.effective`, `ambient.theme.getSnapshot()`, and `ambient.theme.subscribe(listener)` are available when only theme is needed. Do not use `window`, `document`, or `navigator` to infer these values.
- Device-local, non-secret Widget state is available without a capability grant through `ambient.storage.get(key)`, `set(key, value)`, `delete(key)`, `clear()`, and `list()`. Storage is scoped by the host to the current App and survives closing or reopening it. Treat missing or cleared data as normal, keep values JSON-serializable, and never store credentials, API keys, access tokens, or the canonical copy of user data there.
- User-authored drafts, form values, editor content, selections, and other work that must survive Runtime suspension must hydrate from `ambient.storage` and write through after each meaningful change. Never keep user-authored drafts only in React hook state. Gate write-through until `storage.get` hydration finishes; otherwise an initial empty/default value can overwrite saved data before it loads. A short bounded debounce is acceptable only when `ambient.lifecycle.onBeforeSuspend(handler)` registers an async handler that flushes the latest pending value and awaits its `ambient.storage.set(...)`; keep the latest value in a ref so the handler cannot capture stale state, and unsubscribe the handler during effect cleanup. Purely transient presentation state such as hover or an open menu may reset; canonical user data still belongs in approved Graph or file capabilities rather than local storage.
- Render with the `ambient.html` tagged template. Close dynamic HTM components with `<//>` or use a self-closing tag.
- Never import modules. Never use `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `window`, `document`, `navigator`, storage globals, `eval`, `Function`, Node APIs, environment variables, shell commands, or host filesystem APIs.
- The host injects only capability namespaces granted by Manifest V2. A namespace or method not in the approved Runtime Contract does not exist.
- An approved `ambient.files.read(path)` for a first-use file rejects with code `file_not_found` (older runtimes may expose only the exact message `App data file not found`). Treat only this missing-file condition as an empty default so the user can create the file; show a visible retryable error for other read failures, and never turn every read error into empty data.
- Use only literal resource identifiers so staging verification can prove scope:
  - `device.location` with `operations: ["current"]` → `ambient.location.getCurrentPosition({ timeout: 10000, maximumAge: 0 })` from an explicit button or keyboard action only. It returns `{ latitude, longitude, accuracy, timestamp }`; handle denied, unavailable, insecure context and timeout errors. Never call on mount, watch position, enable high accuracy, or use server/IP location. Keep manual place search available when location is denied.
  - `graph.query` → `ambient.graph.subscribe({ type: "ApprovedType" }, callback)`; unsubscribe on cleanup.
  - `graph.mutate` → pass an array literal containing object literals directly to `ambient.graph.mutate([...])`. The exact action DSL is:
    - create: `{ action: "create_node", type: "ApprovedType", properties: { ... } }`
    - update: `{ action: "update_node_property", id: nodeId, properties: { ... } }`
    - delete: `{ action: "delete_node", id: nodeId }`
    - create edge: `{ action: "create_edge", from_id: fromId, to_id: toId, type: "APPROVED_EDGE", properties: { ... } }`
    - delete edge: `{ action: "delete_edge", from_id: fromId, to_id: toId, type: "APPROVED_EDGE" }`
    Never use shorthand such as `action: "create"`, `action: "update"`, or an `operation` field. Grant operations (`create`, `update`, `delete`) authorize the corresponding exact action names above; they are not action payload values.
  - `network.request` → `ambient.net.request("approved-source", { path, method, query, body })`.
  - `file.read` → `ambient.files.read(path)` / `ambient.files.list(path)` below `app://data`.
  - `file.write` → `ambient.files.write(path, text)` below `app://data`.
  - `file.delete` → `ambient.files.delete(path)` below `app://data`.
  - `capability.invoke` → `ambient.capabilities.invoke("approved-catalog-id", input, "approved-action")`.
- Pass those string literals directly at each capability call. Do not hide a file path, source ID, catalog ID, action ID, entity type, or operation behind a variable or generic helper parameter.
- `ambient.mcp`, `ambient.runs`, and generic host APIs are not part of the Widget SDK.
- Do not replace requested live behavior with fake/sample data. If the approved contract cannot satisfy a requirement, leave the live App unchanged by reporting the mismatch; never expand the Manifest yourself.

# Canonical context graph

- Store only user-context facts in the `ambient-context` graph.
- Use exactly the approved schema types and properties. Never invent an entity type or field.
- App caches, UI state, sync cursors, credentials, checkpoints, and raw provider payloads belong under private `app://data`, if and only if file grants allow it.

# Component skeleton

```javascript
const { useEffect, useState } = ambient.react;
const { Card, Column, Text } = ambient.components;

export default function App() {
  const [items, setItems] = useState([]);

  useEffect(() => {
    // Include this only when graph.query is granted.
    return ambient.graph.subscribe({ type: "ApprovedType" }, setItems);
  }, []);

  return ambient.html`
    <${Card} title="App">
      <${Column} gap="12px">
        <${Text} text=${String(items.length)} />
      <//>
    <//>
  `;
}
```

# Language

{% if language == 'en' %}
All user-facing UI copy must be English.
{% else %}
所有面向用户的界面文案必须使用中文。
{% endif %}

Inspect existing allowed artifacts, implement the approved request, validate the Manifest V2 security fields against their mapped Runtime Contract values, and finish only after the staging verifier can pass.
