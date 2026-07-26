# Widget Format and Lifecycle

Current Widgets use Manifest V2 plus a single React/HTM Controller. By default, the Controller executes and renders natively inside an opaque-origin sandbox iframe in the user's browser; the Docker Chromium pixel stream is only a temporary rollback path. Do not generate inline XML Widgets, `index.html`, `style.css`, or removed legacy SDK APIs.

## 1. One carrier form

```text
workspace/apps/<app-id>/
├── manifest.json
├── controller.js
├── README.md
└── data/
```

Every create and modify operation uses the durable Widget workflow, writes staging only after schema + capability approval, then verifies and atomically publishes. A chat model cannot directly return or persist an executable Widget.

## 2. Controller contract

- Default-export a renderable React component that receives `{ ambient }`.
- Use `ambient.html` or JSX accepted by the Babel React preset.
- Use `ambient.react` hooks for state and effects.
- Use only the SDK listed by the Runtime Contract. Graph, network, files, and installed capabilities require matching grants.
- Capability/source/catalog/action IDs are string literals and are never assembled at runtime.
- Clean up subscriptions and timers. Do not use direct browser events, DOM, Cookies, browser storage globals, network, or dynamic-code APIs. Use only `ambient.storage` for local non-secret state. User drafts, form values, and editor content must hydrate during initialization and write through after meaningful changes; they must not live only in React hook memory because workspace suspension unmounts the Controller. A bounded debounce must register an async flush through `ambient.lifecycle.onBeforeSuspend(handler)`, write the latest ref-backed value to `ambient.storage`, await completion, and unsubscribe the handler during effect cleanup.

```javascript
export default function TaskList({ ambient }) {
  const { useEffect, useState } = ambient.react;
  const { Button, Card, Column, Text } = ambient.components;
  const [tasks, setTasks] = useState([]);

  useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);

  async function addTask() {
    await ambient.graph.mutate([{
      action: "create_node",
      type: "Task",
      properties: { title: "New task", description: "", status: "todo", due_date: "" }
    }]);
  }

  return ambient.html`
    <${Card} title="Tasks">
      <${Column} gap=${12}>
        <${Text} text=${`${tasks.length} items`} />
        <${Button} label="Add" onClick=${addTask} />
      <//>
    <//>`;
}
```

## 3. Standard components

`ambient.components` includes `Column`, `Row`, `Card`, `Text`, `Button`, `TextField`, `Checkbox`, `List`, and `Table`. They provide host-themed appearance without granting external authority.

## 4. Generation and publication checks

The Runtime Contract is an approval envelope used by the publication coordinator, not the `manifest.json` file format. The Coding Agent maps only its `app_id`, schema ID list, and normalized capabilities to Manifest V2 `id`, `schema_refs`, and `capabilities`; it must not write `contract_version`, `catalog_version`, `schemas`, `grants_digest`, or `allowed_files` into the Manifest. Generation prompts must include a complete Manifest V2 template, and repair prompts must preserve this mapping so the approval envelope is never copied as an App artifact. `intents` must be an array of unique, non-empty strings, never objects.

Publication checks, in order:

1. safe paths, allowed files, size, UTF-8, and default export;
2. module syntax and forbidden host-global/import/dynamic-code rules;
3. Controller capability use is a subset of approved grants;
4. staging Manifest grants exactly equal the approved Runtime Contract;
5. Graph use matches effective schemas;
6. artifact hash, grants digest, Run version, and effect/idempotency records.

Only then is staging atomically promoted. Failure, cancellation, or denial preserves the existing App. When deterministic policy classifies a validation error as code-only with no approved-contract change, the Coding Agent keeps running repair and independent verification in the same staging directory and ACP session without a fixed three-turn ceiling. The loop stops immediately when the exact same finding repeats consecutively or the validated artifact hash does not change; infrastructure failures and errors requiring broader authority or Schema changes are also not blindly sent to the Coding Agent. Finding history is persisted with the failed draft, so a new Run attempt cannot restart the same failure loop from empty history. Any unresolved internal validation failure, timeout, or system error before promotion retains the failed draft together with its error in non-executable hidden staging; retry repairs that directory in place or continues verification instead of deleting and regenerating it. When the Controller and Manifest grants disagree, the repair turn receives the approved Runtime Contract again and may only edit the existing `controller.js`/`manifest.json` to match it; it cannot request or broaden authority. Only explicit cancellation, rework, or expiry of the draft-retention period may clean that staging.

## 5. Debugging

- The isolated iframe Runtime returns compilation/render failures as structured `runtime_error` events displayed in the Widget; Controller console output never enters the host-page realm.
- Generation failures appear in chat with the App ID, failed phase, error code, and cause. Reply with `/repair <app-id> [feedback]` to continue from the retained draft.
- For `capability_denied`, first check Manifest entity/operation/source/path/action scope.
- Handle interactions and `needs_attention` in the Task Drawer.
- Run `node scripts/verify_widget_controller.mjs <controller.js>` for static verification.
- See [ambient SDK](/en/widgets/sdk.md) for APIs, [Widget Isolation Runtime](/en/widgets/sandbox.md) for execution isolation, and [Widget Capability Security](/en/architecture/capability-security.md) for authorization.
