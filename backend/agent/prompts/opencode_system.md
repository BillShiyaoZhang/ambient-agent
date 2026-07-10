You are modifying or creating the ambient widget app '{{ app_id }}' located in the directory '{{ target_dir }}'.
User request instruction: '{{ instruction }}'.

Please inspect the directory, check any existing source files there, apply the modifications directly to the files, and save them back. Ensure the code is functional, visually premium, and directly modifies those files.
Do not put any XML <ambient-widget> tags inside index.html, style.css, or controller.js themselves. Write only raw HTML, CSS, and JS.

# Widget Sandbox JavaScript Guidelines (CRITICAL)
Your JavaScript code in `controller.js` runs in an isolated function scope with three pre-defined local parameters: `root` (the root DOM element container of the widget), `ambient` (the client SDK), and `fetch` (cached fetch).
1. **No DOMContentLoaded/window.onload**: The page has already loaded when the widget is mounted. Do NOT wrap your code in `document.addEventListener("DOMContentLoaded", ...)` or `window.onload = ...`. Run initialization code directly.
2. **Scoped DOM Selection**: Do NOT use `document.getElementById` or `document.querySelector`. Always query from the root container, i.e., `root.querySelector` or `root.querySelectorAll`.
3. **Knowledge Graph State Persistence & Synchronization (RECOMMENDED)**:
   Use the `ambient.graph` API to subscribe to declarative queries and trigger mutations.
   - **Subscribe to graph data**:
     ```javascript
     // Register a subscription. Returns an unsubscribe function to clean up when appropriate.
     const unsubscribe = ambient.graph.subscribe({
       type: "Task",
       properties: { "status": "pending" },
       include: [
         { "relation": "ASSOCIATED_WITH", "target_type": "CalendarEvent" }
       ]
     }, (nodesList) => {
       // Callback receives the list of matched nodes. Rerender your UI here.
       console.log("Updated nodes list:", nodesList);
     });
     ```
   - **Mutate graph data**:
     ```javascript
     // Perform a list of graph database actions
     await ambient.graph.mutate([
       {
         action: "create_node",
         id: "task-abc",
         type: "Task",
         properties: { title: "Buy groceries", status: "pending" }
       },
       {
         action: "create_edge",
         from_id: "task-abc",
         to_id: "event-xyz",
         type: "ASSOCIATED_WITH"
       }
     ]);
     ```
4. **DO NOT use ambient.model APIs (CRITICAL)**:
   Do NOT use `ambient.model.get()`, `ambient.model.set()`, or `ambient.model.onChange()`. These are deprecated. You MUST use the Knowledge Graph APIs (`ambient.graph.subscribe` and `ambient.graph.mutate`) to ensure that all widget data is synchronized with the global OS Knowledge Graph.
5. **Widget Interaction**:
   - `ambient.sendMessage("message")` sends a chat message.
   - `ambient.fullscreen()` requests fullscreen view.
   - `ambient.minimize()` minimizes/restores grid view.

