# Widget XML Protocol

When the LLM intends to output an interactive widget, it embeds code blocks inside special `<ambient-widget>` XML containers in the streaming response. The backend parser extracts and compiles these blocks, removing the raw XML text from chat bubbles.

## 1. Protocol Definition

Writers can output widgets in two modes:

### A. HTML / CSS / JS Mode (Default)

Inlines direct browser-standard markups:

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<html-content>
  <!-- Tailwind classes supported -->
  <div class="p-4 rounded bg-slate-900">
    <button id="action-btn" class="bg-purple-600 px-3 py-1 text-white">Click Me</button>
  </div>
</html-content>
<css-styles>
  #action-btn { transition: transform 0.1s; }
</css-styles>
<js-script>
  // root element and ambient SDK are injected parameters
  const btn = root.querySelector("#action-btn");
  btn.addEventListener("click", () => {
    ambient.sendMessage("Button clicked in sandboxed scope!");
  });
</js-script>
</ambient-widget>
```

### B. A2UI JSON Mode

Maintains structured declarative layout configurations:

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<layout-json>
  {
    "type": "container",
    "children": [
      {
        "type": "button",
        "id": "my-btn",
        "label": "Confirm"
      }
    ]
  }
</layout-json>
<js-script>
  ambient.ui.on("click", "my-btn", () => {
    ambient.sendMessage("Declartive button clicked");
  });
</js-script>
</ambient-widget>
```

### C. React JSX Mode

For dynamic and stateful React-based components, the LLM can output JSX code and controller Hook scripts directly:

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<react-jsx>
  // index.jsx: React component script. Must export default (or name export) a React component.
  // Can import "react" and "./controller.js".
  import React, { useState } from "react";
  import { useController } from "./controller.js";

  export default function MyWidget({ ambient }) {
    const { items, addItem } = useController(ambient);
    const [text, setText] = useState("");

    return (
      <div className="p-4 bg-slate-900/50 rounded-xl text-white">
        <h3 className="text-sm font-semibold mb-2">React Dynamic Card</h3>
        <ul className="space-y-1 text-xs mb-3">
          {items.map((item, idx) => <li key={idx}>- {item.title}</li>)}
        </ul>
        <div className="flex gap-2">
          <input 
            type="text" 
            value={text} 
            onChange={(e) => setText(e.target.value)} 
            className="bg-black/20 border border-white/10 px-2 py-1 rounded text-xs w-full"
          />
          <button 
            onClick={() => { addItem(text); setText(""); }} 
            className="bg-blue-600 px-3 py-1 rounded text-xs text-white"
          >
            Add
          </button>
        </div>
      </div>
    );
  }
</react-jsx>
<js-script>
  // controller.js: Core controller custom Hook logic. Must export a useController Hook.
  import { useState, useEffect } from "react";

  export function useController(ambient) {
    const [items, setItems] = useState([]);

    useEffect(() => {
      const unsub = ambient.graph.subscribe({ type: "Task" }, (tasks) => {
        setItems(tasks);
      });
      return unsub;
    }, [ambient]);

    const addItem = async (title) => {
      await ambient.graph.mutate([
        {
          action: "create_node",
          type: "Task",
          properties: { title, status: "pending" }
        }
      ]);
    };

    return { items, addItem };
  }
</js-script>
</ambient-widget>
```

## 2. Field Specifications

- `id`: Unique identifier for the widget. Alphanumeric and hyphens only (e.g., `weather-card`).
- `title`: Widget title displayed on the Canvas drag-and-drop bar.
- `<html-content>`: HTML markup elements (HTML/CSS mode only). Supporting inline Tailwind classes.
- `<css-styles>`: Scoped CSS stylesheet rules (HTML/CSS mode only).
- `<layout-json>`: Declarative UI component config in JSON structure (A2UI mode only).
- `<react-jsx>`: React component JSX source code (React mode only). Front-end uses `@babel/standalone` to transpile and isolate execution.
- `<js-script>`: Interaction script (available for all modes). Executes as a closure function in HTML/A2UI modes, or serves as the `controller.js` Hook module in React mode. Runs inside the sandboxed SDK wrapper.
