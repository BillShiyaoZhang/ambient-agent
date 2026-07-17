You are modifying or creating the dynamic React + Tailwind CSS widget app '{{ app_id }}' located in the directory '{{ target_dir }}'.
User request instruction: '{{ instruction }}'.

{% raw %}
Please inspect the directory, check any existing source files there, apply the modifications directly to the files, and save them.
Ensure the layout and logic are functional, visually premium, and directly modify the correct file.

# File Strategy (CRITICAL)
You must NOT generate `index.html`, `style.css`, `layout.json`, or `index.jsx`. Instead, you MUST generate/modify exactly ONE file in the target directory:
1. `controller.js`: The React component (containing both state/logic and View/HTM layout).

Do NOT put any XML <ambient-widget> tags inside controller.js. Write only raw JavaScript.

---

# 1. Component Specification (`controller.js`)
The `controller.js` must export a default React component function.

- **Destructure React Hooks**: Retrieve hooks from `ambient.react`:
  ```javascript
  const { useState, useEffect, useMemo, useRef, useCallback } = ambient.react;
  ```
- **Destructure Pre-built Components**: Retrieve premium components from `ambient.components`:
  ```javascript
  const { Card, Button, TextField, Checkbox, List, Table, Column, Row, Text } = ambient.components;
  ```
- **HTML / Layout Rendering**: Return layout using `ambient.html` template literal:
  ```javascript
  return ambient.html`
    <${Card} title="My App">
      <${Column} gap="12px">
        <${Text} text="Hello World" />
      <//>
    <//>
  `;
  ```
- **React State & Graph DB Sync**: Use standard React hooks for state, and `ambient.graph.subscribe` inside `useEffect` for real-time synchronization with the database. Always return the unsubscribe function.
- **Mutations & Events**: Submit modifications via `ambient.graph.mutate`.
- **MCP Tools**: Call system tools using `ambient.mcp.callTool(toolName, args)`.

#### Example `controller.js`:
```javascript
// controller.js
const { useState, useEffect } = ambient.react;
const { Card, Button, TextField, List, Column, Row, Text } = ambient.components;

export default function App() {
  const [tasks, setTasks] = useState([]);
  const [input, setInput] = useState("");

  // Sync with graph DB
  useEffect(() => {
    const unsubscribe = ambient.graph.subscribe({ type: "Task" }, (nodes) => {
      setTasks(nodes || []);
    });
    return unsubscribe; // Crucial for cleanup
  }, []);

  const handleAddTask = async () => {
    if (!input.trim()) return;
    await ambient.graph.mutate([
      {
        action: "create_node",
        type: "Task",
        properties: { title: input, status: "pending" }
      }
    ]);
    setInput("");
  };

  const handleToggle = async (taskId, currentStatus) => {
    const nextStatus = currentStatus === "completed" ? "pending" : "completed";
    await ambient.graph.mutate([
      {
        action: "update_node",
        id: taskId,
        properties: { status: nextStatus }
      }
    ]);
  };

  return ambient.html`
    <${Card} title="Dynamic Tasks">
      <${Column} gap="16px">
        <${Row} gap="8px" align="center">
          <${TextField} 
            placeholder="Add task..." 
            value=${input} 
            onChange=${e => setInput(e.target.value)}
            onEnter=${handleAddTask}
          />
          <${Button} label="Add" onClick=${handleAddTask} />
        <//>
        <${List} 
          items=${tasks} 
          onItemClick=${item => handleToggle(item.id, item.properties.status)}
          itemStyle=${{ padding: '8px', cursor: 'pointer' }}
        />
      <//>
    <//>
  `;
}
```

---

# 2. Database Schema and Types Constraints (CRITICAL)
- **Schema Type Constraint**: Your JS database writes and reads must strictly match the types and fields documented in the system database schema. Key names, string vs integer vs boolean types must match exactly.
- **DO NOT use ambient.model**: Do NOT use `ambient.model.get()`, `ambient.model.set()`, or `ambient.model.onChange()`. These are deprecated. Use `ambient.graph.subscribe` and `ambient.graph.mutate` exclusively.
{% endraw %}
