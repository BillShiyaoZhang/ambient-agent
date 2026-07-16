You are modifying or creating the dynamic React + Tailwind CSS widget app '{{ app_id }}' located in the directory '{{ target_dir }}'.
User request instruction: '{{ instruction }}'.

Please inspect the directory, check any existing source files there, apply the modifications directly to the files, and save them.
Ensure the layout and logic are functional, visually premium, and directly modify the correct files.

# React + Tailwind File Strategy (CRITICAL)
You must NOT generate `index.html`, `style.css`, or `layout.json`. Instead, you MUST generate exactly two files in the target directory:
1. `index.jsx`: The React component (View) that renders the UI.
2. `controller.js`: The React hook (Controller) that manages state, actions, and database synchronization.

---

# 1. Component View Specification (`index.jsx`)
The `index.jsx` must define a React component function that renders the layout.
- **Import Controller**: You must import the custom hook `useController` from `./controller.js` to retrieve all state variables and action handlers:
  ```jsx
  import { useController } from "./controller.js";
  ```
- **Props**: The component receives `ambient` (the client SDK) as a parameter or prop.
- **Styling**: You must style the UI exclusively using Tailwind CSS utility classes (e.g. `bg-slate-900`, `rounded-xl`, `p-4`, `shadow-xl`, `font-semibold`, `text-indigo-400`, etc.). Do NOT write raw CSS or compile inline `style` objects unless for dynamic values (like progress percentages).
- **JSX Format**: Ensure the file contains clean, well-formed JSX markup. Do NOT import React or any hook directly in `index.jsx`; instead, reference hooks if needed from React scope (e.g. `React.useMemo`) or use the variables returned by `useController(ambient)`.
- **Aesthetic Excellence**: Render a highly polished, visually stunning, modern interface with consistent spacing, rounded borders, clear typographic hierarchy, status badges, metrics, and hover states.

#### Example `index.jsx`:
```jsx
import { useController } from "./controller.js";

export default function Widget({ ambient }) {
  const { tasks, taskInput, setTaskInput, handleAddTask, handleToggleTask } = useController(ambient);

  return (
    <div className="w-full bg-slate-900/80 backdrop-blur-md border border-white/5 rounded-2xl p-5 shadow-2xl text-slate-100">
      <h2 className="text-xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent mb-4">
        Dynamic Task Board
      </h2>
      <div className="flex gap-2 mb-4">
        <input
          type="text"
          value={taskInput}
          onChange={(e) => setTaskInput(e.target.value)}
          placeholder="What needs to be done?"
          className="flex-1 bg-slate-950/60 border border-white/10 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-indigo-500 text-white placeholder-slate-500"
        />
        <button
          onClick={handleAddTask}
          className="bg-indigo-600 hover:bg-indigo-700 active:scale-95 text-white font-medium rounded-xl px-4 py-2 text-sm transition-all"
        >
          Add Task
        </button>
      </div>
      <ul className="space-y-2">
        {tasks.map(task => (
          <li key={task.id} className="flex items-center gap-3 bg-slate-950/40 border border-white/5 rounded-xl p-3 hover:bg-slate-950/60 transition">
            <input
              type="checkbox"
              checked={task.properties.status === "completed"}
              onChange={() => handleToggleTask(task.id, task.properties.status)}
              className="w-4 h-4 accent-indigo-500 rounded cursor-pointer"
            />
            <span className={task.properties.status === "completed" ? "line-through text-slate-500 text-sm" : "text-slate-200 text-sm"}>
              {task.properties.title}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

---

# 2. Controller Specification (`controller.js`)
The `controller.js` manages states, database queries, and mutations. It must export a custom hook named `useController(ambient)`:
- **No DOM Manipulation**: Do NOT use `root.querySelector` or query elements manually. All state and value flows must be managed purely via React state and bindings.
- **Graph Database Synchronization**:
  - Use `React.useEffect` to subscribe to database changes. You MUST return the unsubscribe callback inside `useEffect` so that the subscription cleans up when the component unmounts.
  - Sync the database node arrays to local states using standard React state hooks (e.g. `React.useState`).
- **Events & Mutations**: Define callbacks (e.g. `handleAddTask`) that call `ambient.graph.mutate` to perform database modifications.

#### Example `controller.js`:
```javascript
// controller.js
export function useController(ambient) {
  const [tasks, setTasks] = React.useState([]);
  const [taskInput, setTaskInput] = React.useState("");

  // Subscribe to graph DB Task updates
  React.useEffect(() => {
    const unsubscribe = ambient.graph.subscribe({ type: "Task" }, (nodes) => {
      setTasks(nodes);
    });
    return unsubscribe; // Crucial for cleanup
  }, [ambient]);

  const handleAddTask = async () => {
    if (!taskInput.strip()) return;
    await ambient.graph.mutate([
      {
        action: "create_node",
        id: `task-${Date.now()}`,
        type: "Task",
        properties: { title: taskInput, status: "pending" }
      }
    ]);
    setTaskInput("");
  };

  const handleToggleTask = async (taskId, currentStatus) => {
    const nextStatus = currentStatus === "completed" ? "pending" : "completed";
    await ambient.graph.mutate([
      {
        action: "create_node", // create_node with same id updates/overwrites properties
        id: taskId,
        type: "Task",
        properties: { status: nextStatus }
      }
    ]);
  };

  return {
    tasks,
    taskInput,
    setTaskInput,
    handleAddTask,
    handleToggleTask
  };
}
```

---

# 3. [CRITICAL GRAPH DATABASE SCHEMA CONSTRAINTS]
Your database operations must strictly match the types and fields registered in the SQLite backend:
- **Task**: properties `{"title": "string", "description": "string", "status": "string", "due_date": "string"}`.
- **Event**: properties `{"title": "string", "description": "string", "start_time": "string", "end_time": "string", "location": "string"}`.
- **Note**: properties `{"title": "string", "content": "string", "tags": "string"}`.
- Relationships use `ASSOCIATED_WITH` edges (actions: `create_edge`, `delete_edge`).

# General Rules:
1. Do NOT wrap your code in DOMContentLoaded/onload. Run hooks immediately.
2. Do NOT import React or any hooks directly in the code (e.g. `import React, { useState } from 'react'`); instead, use the global `React` object namespace (e.g. `React.useState`, `React.useEffect`) inside your code.
3. Every CSS style must utilize only Tailwind CSS utility classNames.
