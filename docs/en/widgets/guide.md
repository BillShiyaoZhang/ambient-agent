# Widget XML Protocol

When the LLM intends to output an interactive widget, it embeds code blocks inside special `<ambient-widget>` XML containers in the streaming response. The backend parser extracts and compiles these blocks, removing the raw XML text from chat bubbles.

---

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
