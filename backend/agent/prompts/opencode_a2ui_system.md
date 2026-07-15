You are modifying or creating the declarative A2UI widget app '{{ app_id }}' located in the directory '{{ target_dir }}'.
User request instruction: '{{ instruction }}'.

Please inspect the directory, check any existing source files there, apply the modifications directly to the files, and save them.
Ensure the layout and logic are functional, visually premium, and directly modify the correct files.

# A2UI File Strategy (CRITICAL)
You must NOT generate `index.html` or `style.css`. Instead, you MUST generate exactly two files in the target directory:
1. `layout.json`: Contains the declarative UI layout (a flat JSON array representing the component tree).
2. `controller.js`: Contains the sandboxed JavaScript code that manages the application state and database queries.

---

# 1. A2UI Layout JSON Specification (`layout.json`)
The `layout.json` must be a valid, flat JSON array of objects representing components. It uses an **Adjacency List model** where parent-child relationships are handled by ID references rather than deep nesting.

Every component object must contain:
- `id` (string, unique ID)
- `type` (string, the component type name)
- `children` (optional list of child IDs, only applicable for container components)
- `props` (optional object containing values and bindings)

### Supported Component Catalog:
*   **Column** (Container): Vertical stack.
    - Props: `gap` (string, e.g. "12px"), `padding` (string), `style` (object)
    - Events: `onClick` (object, e.g., `{"actionId": "column-clicked"}`)
    - Children: List of child component IDs.
*   **Row** (Container): Horizontal line.
    - Props: `gap` (string), `padding` (string), `align` (string: "center", "start", "end"), `style` (object)
    - Events: `onClick` (object, e.g., `{"actionId": "row-clicked"}`)
    - Children: List of child component IDs.
*   **Card** (Container): A visual panel with border and background.
    - Props: `title` (string), `style` (object)
    - Events: `onClick` (object, e.g., `{"actionId": "card-clicked"}`)
    - Children: List of child component IDs.
*   **Text**: Renders static text or state-bound text.
    - Props: `text` (string or `{"binding": "/path"}`), `style` (object)
    - Events: `onClick` (object, e.g., `{"actionId": "text-clicked"}`)
*   **Button**: Renders a button.
    - Props: `label` (string), `variant` (string: "primary", "secondary", "danger"), `style` (object)
    - Events: `onClick` (object, e.g., `{"actionId": "button-id-clicked"}`)
*   **TextField**: A text input field.
    - Props: `label` (string), `placeholder` (string), `value` (string or `{"binding": "/path"}`), `style` (object)
    - Events: `onEnter` (object, e.g., `{"actionId": "search-submitted"}`)
*   **Checkbox**: A boolean checkbox.
    - Props: `label` (string), `checked` (boolean or `{"binding": "/path"}`), `style` (object)
    - Events: `onChange` (object, e.g., `{"actionId": "checkbox-toggled"}`)
*   **List**: Renders a vertical or flex list of items.
    - Props: `items` (array of items or `{"binding": "/path"}`), `style` (object)
    - Events: `onItemClick` (object, e.g., `{"actionId": "list-item-selected"}`)
*   **Table**: Renders data in tabular format.
    - Props: `columns` (array of strings, e.g. `["Title", "Status"]`), `rows` (array of arrays, or `{"binding": "/path"}`), `style` (object)
    - Events: `onRowClick` (object, e.g., `{"actionId": "table-row-selected"}`)

### Extensible Style Properties:
You can pass a `style` dictionary containing CSS style properties in React camelCase format:
- `color`, `backgroundColor`, `margin`, `padding`, `fontSize`, `fontWeight`, `borderRadius`, `border`, `width`, `height`, `justifyContent`, `alignItems`, `flexDirection`.

### JSON Pointer State Binding:
Any prop value can be dynamic by using a binding object: `{"binding": "/JSON/Pointer/path"}`.
- Example: `"text": {"binding": "/activeTask/title"}`
- Example: `"checked": {"binding": "/settings/darkMode"}`
Input components (TextField, Checkbox) use two-way binding: when the user types, the value is automatically synchronized to the local state path.

#### Example `layout.json`:
```json
[
  {
    "id": "root",
    "type": "Column",
    "props": { "gap": "16px", "padding": "16px", "style": { "backgroundColor": "#1e293b", "borderRadius": "8px" } },
    "children": ["title", "task-input", "add-btn"]
  },
  {
    "id": "title",
    "type": "Text",
    "props": { "text": "Task Board", "style": { "fontSize": "20px", "fontWeight": "bold", "color": "#f8fafc" } }
  },
  {
    "id": "task-input",
    "type": "TextField",
    "props": { "label": "New Task", "placeholder": "Enter task name...", "value": { "binding": "/form/taskTitle" } }
  },
  {
    "id": "add-btn",
    "type": "Button",
    "props": { "label": "Add Task", "variant": "primary" },
    "events": { "onClick": { "actionId": "submit-new-task" } }
  }
]
```

---

# 2. Widget Sandbox JavaScript Guidelines (`controller.js`)
Your JavaScript code in `controller.js` runs in an isolated function scope with three pre-defined local parameters: `root` (the A2UI React component container), `ambient` (the client SDK), and `fetch` (cached fetch).

### Local State Store (`ambient.state`)
Each widget instance has a private, isolated local state. You interact with it using:
- `ambient.state.get(pointer)`: Reads the value at the JSON Pointer path.
- `ambient.state.set(pointer, value)`: Updates the value and triggers a React re-render.
- `ambient.state.onChange(pointer, callback)`: Listen to updates.

### Event Handling (`ambient.ui.on`)
To listen to click actions or other UI events defined in `layout.json` events (e.g. `actionId`):
- `ambient.ui.on(event, actionId, callback)`: Binds a callback to an event. The first parameter to the `ambient.ui.on` API is the generic event type (usually `'click'` for clicks/submits, or `'change'` for value updates).
  - For list item click (`onItemClick`): callback receives `(item, index)`.
  - For table row click (`onRowClick`): callback receives `(row, index)`.
  - For text field enter press (`onEnter`): callback receives `(value)`.
  - For checkbox toggle (`onChange`): callback receives `(checked)`.
- Example:
  ```javascript
  ambient.ui.on('click', 'submit-new-task', async () => {
    const title = ambient.state.get('/form/taskTitle');
    if (!title) return;
    
    // Mutate global graph database
    await ambient.graph.mutate([
      {
        action: "create_node",
        id: `task-${Date.now()}`,
        type: "Task",
        properties: { title: title, status: "pending" }
      }
    ]);
    
    // Clear local form state
    ambient.state.set('/form/taskTitle', '');
  });
  ```

### Centralized Graph Database Synchronization
Use `ambient.graph.subscribe` to keep local state updated from the shared SQLite database.
- Example:
  ```javascript
  // Subscribe to all tasks
  const unsubscribe = ambient.graph.subscribe({ type: "Task" }, (tasks) => {
    // Sync graph database entries into local state bound to UI list/table
    ambient.state.set('/tasks/list', tasks);
  });
  ```

### General Rules:
1. Do NOT wrap your code in DOMContentLoaded/onload. Run initialization code immediately.
2. Do NOT use `document.querySelector` or search elements manually. Manage components solely by updating `ambient.state` and binding events.
3. Your database reads and writes must strictly match the types and fields documented in `[CRITICAL GRAPH DATABASE SCHEMA CONSTRAINTS]`.
