You are modifying or creating the ambient widget app '{{ app_id }}' located in the directory '{{ target_dir }}'.
User request instruction: '{{ instruction }}'.

Please inspect the directory, check any existing source files there, apply the modifications directly to the files, and save them back. Ensure the code is functional, visually premium, and directly modifies those files.
Do not put any XML <ambient-widget> tags inside index.html, style.css, or controller.js themselves. Write only raw HTML, CSS, and JS.

# Widget Sandbox JavaScript Guidelines (CRITICAL)
Your JavaScript code in `controller.js` runs in an isolated function scope with three pre-defined local parameters: `root` (the root DOM element container of the widget), `ambient` (the client SDK), and `fetch` (cached fetch).
1. **No DOMContentLoaded/window.onload**: The page has already loaded when the widget is mounted. Do NOT wrap your code in `document.addEventListener("DOMContentLoaded", ...)` or `window.onload = ...`. Run initialization code directly.
2. **Scoped DOM Selection**: Do NOT use `document.getElementById` or `document.querySelector`. Always query from the root container, i.e., `root.querySelector` or `root.querySelectorAll`.
3. **State Persistence & Synchronization**: Use the `ambient.model` API to load, save, and sync data:
   - `const data = await ambient.model.get();` (returns a dictionary, initially `{}`)
   - `await ambient.model.set(newData);` (saves to data.json and broadcasts to other synced clients)
   - `ambient.model.onChange(data => { ... });` (sets up a callback for when data changes remotely)
4. **Widget Interaction**:
   - `ambient.sendMessage("message")` sends a chat message.
   - `ambient.fullscreen()` requests fullscreen view.
   - `ambient.minimize()` minimizes/restores grid view.

