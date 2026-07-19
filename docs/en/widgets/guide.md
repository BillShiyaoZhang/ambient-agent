# Widget Format and Lifecycle

Current Widgets use a single-file React/HTM controller. Do not generate `<html-content>`, `<css-styles>`, `index.html`, `style.css`, or `ambient.model`.

## 1. Two carrier forms

The live artifacts of a persistent app are:

```text
workspace/apps/<app-id>/controller.js
workspace/apps/<app-id>/manifest.json
```

When a chat model needs to return an inline Widget, it uses XML as a transport container:

```xml
<ambient-widget id="task-board" title="Task Board">
<js-script>
export default function TaskBoard({ ambient }) {
  const { Card, Text } = ambient.components;
  return ambient.html`<${Card} title="Tasks"><${Text} text="Ready" /></${Card}>`;
}
</js-script>
</ambient-widget>
```

`AgentParser` currently extracts only the first `<ambient-widget>`, requires double-quoted `id` and `title`, and saves `<js-script>` as the controller. XML tags must not appear inside `controller.js` itself.

## 2. Controller contract

- Provide a default-exported React component that can be rendered.
- The component receives `{ ambient }`; injected `React` and `ambient` are also available during module execution.
- `ambient.html` (HTM) avoids JSX; JSX that the Babel React preset can transpile is also supported.
- Use hooks exposed by `ambient.react` for state and effects.
- Use `ambient.graph` for persistent data, and `ambient.runs`, `ambient.capabilities`, or `ambient.mcp` for external capabilities.
- Clean up subscriptions, timers, and browser listeners when the component unmounts.

```javascript
export default function TaskList({ ambient }) {
  const { useEffect, useState } = ambient.react;
  const { Button, Card, Column, Text } = ambient.components;
  const [tasks, setTasks] = useState([]);

  useEffect(() => {
    return ambient.graph.subscribe({ type: "Task" }, setTasks);
  }, []);

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
      </${Column}>
    </${Card}>`;
}
```

## 3. Standard components

`ambient.components` currently contains `Column`, `Row`, `Card`, `Text`, `Button`, `TextField`, `Checkbox`, `List`, and `Table`. They provide basic host-themed appearance but do not prevent a controller from using normal React elements.

## 4. Generation and publication checks

When OpenCode creates or modifies an app, it writes `controller.js` into a Run-specific staging directory. Publication checks, in order:

1. path, file size, UTF-8, and default export;
2. Node-side module/syntax and forbidden host-capability rules;
3. Graph subscribe/query/mutate usage against effective schemas;
4. user-approved plan or schema proposal;
5. artifact hash, Run version, and effect/idempotency records.

Only then is staging atomically promoted to the live directory. A failed, cancelled, or rejected modification does not overwrite the existing app.

## 5. Debugging

- Compilation errors appear inside the Widget and in the browser console.
- For Graph mutation failures, check action names, node types, property names, and schema types.
- For MCP or capability failures, inspect the corresponding Run and interaction in the Task Drawer.
- Run `node scripts/verify_widget_controller.mjs <controller.js>` for a static controller check.
- Do not treat `ErrorBoundary` or the word “Sandbox” in a component name as security isolation. See [Runtime Boundary](/en/widgets/sandbox.md).
