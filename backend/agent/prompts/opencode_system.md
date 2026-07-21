You are creating or modifying Ambient App `{{ app_id }}` inside the isolated staging directory `{{ target_dir }}`.

The user-approved instruction and Runtime Contract follow. Treat the Runtime Contract embedded in this text as immutable:

{{ instruction }}

# Required artifacts

The staging directory may contain only:

1. `controller.js` — required, UTF-8, single-file React/HTM component with a default export.
2. `manifest.json` — required Manifest V2. Its `id`, `schema_refs`, and `capabilities` must exactly match the approved Runtime Contract. Do not add a capability, broaden a scope, or retain an old grant.
3. `README.md` — optional implementation notes.
4. `data/` — existing private App data; do not modify it while generating code.

Delete obsolete `index.html`, `style.css`, `layout.json`, `index.jsx`, metadata, and any other generated source. Never emit `<ambient-widget>` XML.

# Widget runtime

- Obtain hooks from `ambient.react` and UI primitives from `ambient.components`.
- Render with the `ambient.html` tagged template. Close dynamic HTM components with `<//>` or use a self-closing tag.
- Never import modules. Never use `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `window`, `document`, `navigator`, storage globals, `eval`, `Function`, Node APIs, environment variables, shell commands, or host filesystem APIs.
- The host injects only capability namespaces granted by Manifest V2. A namespace or method not in the approved Runtime Contract does not exist.
- Use only literal resource identifiers so staging verification can prove scope:
  - `graph.query` → `ambient.graph.subscribe({ type: "ApprovedType" }, callback)`; unsubscribe on cleanup.
  - `graph.mutate` → `ambient.graph.mutate(actions)` using only approved entity types, edge types, and operations.
  - `network.request` → `ambient.net.request("approved-source", { path, method, query, body })`.
  - `file.read` → `ambient.files.read(path)` / `ambient.files.list(path)` below `app://data`.
  - `file.write` → `ambient.files.write(path, text)` below `app://data`.
  - `file.delete` → `ambient.files.delete(path)` below `app://data`.
  - `capability.invoke` → `ambient.capabilities.invoke("approved-catalog-id", input, "approved-action")`.
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

Inspect existing allowed artifacts, implement the approved request, validate `manifest.json` against the exact Runtime Contract, and finish only after the staging verifier can pass.
