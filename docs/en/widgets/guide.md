# Widget Format and Lifecycle

Current Widgets use Manifest V2 plus a single React/HTM Controller. Do not generate inline XML Widgets, `index.html`, `style.css`, or removed legacy SDK APIs.

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
- Clean up subscriptions and timers. Do not use direct browser events, DOM, storage, network, or dynamic-code APIs.

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

Publication checks, in order:

1. safe paths, allowed files, size, UTF-8, and default export;
2. module syntax and forbidden host-global/import/dynamic-code rules;
3. Controller capability use is a subset of approved grants;
4. staging Manifest grants exactly equal the approved Runtime Contract;
5. Graph use matches effective schemas;
6. artifact hash, grants digest, Run version, and effect/idempotency records.

Only then is staging atomically promoted. Failure, cancellation, or denial preserves the existing App.

## 5. Debugging

- Compilation/render failures appear in the Widget and browser console.
- For `capability_denied`, first check Manifest entity/operation/source/path/action scope.
- Handle interactions and `needs_attention` in the Task Drawer.
- Run `node scripts/verify_widget_controller.mjs <controller.js>` for static verification.
- See [ambient SDK](/en/widgets/sdk.md) for APIs and [Widget Capability Security](/en/architecture/capability-security.md) for authorization.
