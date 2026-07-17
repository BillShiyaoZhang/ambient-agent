# Widget XML Protocol

When the LLM intends to output an interactive widget card, it embeds special `<ambient-widget>` XML containers in the streaming response. The backend parser extracts and compiles these blocks, removing the raw XML text from chat bubbles, and delivers them to the frontend canvas.

To combine high development efficiency, fast cold-start rendering, design consistency, and layout flexibility, the system utilizes the unified **React + HTM** declarative rendering mode.

---

## 1. Unified Declarative Rendering Protocol (Highly Recommended)

The LLM outputs a single `<js-script>` block, defining a declarative React component via `ambient.html` (powered by `htm`). This mode eliminates compile overhead, supports reactive Hooks, and provides system-styled components out of the box.

### A. Protocol Schema Example

```xml
<ambient-widget id="todo-manager" title="Task Board">
<js-script>
  // 1. Destructure React Hooks from ambient.react
  const { useState, useEffect } = ambient.react;

  // 2. Destructure pre-defined components (offline-ready, pre-styled)
  const { Card, Button, TextField, List } = ambient.components;

  export default function TodoWidget() {
    const [tasks, setTasks] = useState([]);
    const [input, setInput] = useState("");

    useEffect(() => {
      // Subscribe to backend graph DB updates
      const unsub = ambient.graph.subscribe({ type: "Task" }, (data) => {
        setTasks(data.nodes || []);
      });
      return unsub;
    }, []);

    const handleAdd = async () => {
      if (!input.trim()) return;
      await ambient.graph.mutate([
        {
          action: "create_node",
          type: "Task",
          properties: { content: input, completed: false }
        }
      ]);
      setInput("");
    };

    // Render using ambient.html (use \$ to avoid template literal conflict in raw text)
    return ambient.html`
      <\${Card} title="My Tasks">
        <div class="flex gap-2 mb-3">
          <\${TextField} 
            placeholder="Add a new task..." 
            value=\${input} 
            onChange=\${e => setInput(e.target.value)} 
            onEnter=\${handleAdd}
          />
          <\${Button} label="Add" onClick=\${handleAdd} />
        </div>
        
        <!-- Standard HTML and Tailwind CSS classes can be freely combined -->
        <div class="border-t border-white/5 pt-3">
          <\${List} 
            items=\${tasks.map(t => t.properties.content)} 
            itemStyle=\${{ backgroundColor: 'rgba(255,255,255,0.01)' }}
          />
        </div>
      <//>
    `;
  }
</js-script>
</ambient-widget>
```

### B. Pre-defined Components (`ambient.components`)

Pre-defined components inherit the host app's Design System, adapt automatically to dark/transparent themes, and are **fully offline-compatible**.

1. **Layout & Containers**:
   - `<\${Card} title="Title" onClick=\${...}>Children<//>`: Rounded container card.
   - `<\${Column} gap="8px" padding="10px">Children<//>`: Vertical flex column.
   - `<\${Row} gap="8px" align="center">Children<//>`: Horizontal flex row.
2. **Basic Inputs & Controls**:
   - `<\${Text} text="Content" style=\${...} />`: Text label.
   - `<\${Button} label="Label" variant="primary|secondary|danger" onClick=\${...} />`: Flat interactive button.
   - `<\${TextField} label="Label" placeholder="..." value=\${value} onChange=\${...} onEnter=\${...} />`: Input text field.
   - `<\${Checkbox} label="Label" checked=\${checked} onChange=\${...} />`: Inline checkbox.
3. **Data Visualizations**:
   - `<\${List} items=\${itemsArray} onItemClick=\${...} itemStyle=\${...} />`: Standard spacing vertical list.
   - `<\${Table} columns=\${columnsArray} rows=\${rowsArray} onRowClick=\${...} />`: Self-adjusting responsive data table.

---

## 2. Field Specifications

- `id`: Unique identifier for the widget. Alphanumeric and hyphens only (e.g., `weather-card`).
- `title`: Widget title displayed on the Canvas drag-and-drop bar.
- `<js-script>`: Core rendering and logic script. In the unified mode, it must `export default` a standard React component utilizing the injected hooks, components, and template parser from the `ambient` property.

---
