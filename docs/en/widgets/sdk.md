# ambient SDK

`SandboxWidget` always injects pure UI host features and injects external-access methods only for the current App's approved grants. This page documents the Manifest V2 SDK. Callers handle both an absent method and backend denial.

## 1. Always-available Host Features

| API | Behavior |
| --- | --- |
| `ambient.sendMessage(text)` | Submit a user message to the current chat |
| `ambient.fullscreen()` / `ambient.minimize()` | Ask the host to change the current App window state |
| `ambient.theme.preference` / `effective` | Compatibility accessors that always read the current preference and effective theme |
| `ambient.theme.getSnapshot()` / `subscribe(listener)` | Read a theme snapshot and subscribe to in-session theme changes |
| `ambient.presentation.getSnapshot()` / `subscribe(listener)` | Read and subscribe to the `{ theme, locale, reducedMotion }` presentation context |
| `ambient.storage.get/set/delete/clear/list` | Persist non-secret JSON state for the current browser and App |
| `ambient.lifecycle.onBeforeSuspend(handler)` | Register the single async pre-suspend flush handler and return its unsubscribe function |
| `ambient.html` | HTM tag bound to React createElement |
| `ambient.react` | `useState`, `useEffect`, `useMemo`, `useRef`, `useCallback`, `useContext`, `useReducer`; pre-publication verification rejects any other non-injected hook |
| `ambient.components` | `Column`, `Row`, `Card`, `Text`, `Button`, `TextField`, `Checkbox`, `List`, `Table`; pre-publication verification rejects any other non-injected component |

`TextField` delivers the current string value to `onChange(value)` and `onEnter(value)`; `Checkbox` delivers a boolean to `onChange(checked)`. Component callbacks do not expose DOM event objects.

`Button` uses its `label` prop or its children for visible content. Children render when `label` is absent; an explicitly supplied `label` takes precedence, including an empty string. Children may contain text and elements such as icons. Other props, including `onClick`, `aria-*`, and `disabled`, are forwarded to the native button:

```javascript
const { Button } = ambient.components;
return <Button onClick={openForecast} aria-label="View forecast" disabled={loading}>
  <span aria-hidden="true">☀</span> View all seven days
</Button>;
```

`List` renders text rows from its `items` prop. Each item can be a string or an object with `label` or `name`; it also accepts `onItemClick` and `itemStyle`. It does not render child elements. `Table` renders text cells from `columns` and `rows` and supports row clicks only. For editable records or rows with buttons, checkboxes, or fields, map records to `<${Row}>` children inside `<${Column}>`.

These interfaces grant no external-data access. Controllers do not use `window`, DOM queries, Cookies, browser storage globals, imports, `fetch`, raw WebSockets, `eval`, or `Function`.

Presentation context updates without restarting the Widget. A Controller that reacts to theme, language, or reduced-motion changes subscribes instead of reading only once during module load:

```javascript
const [presentation, setPresentation] = useState(
  ambient.presentation.getSnapshot()
);

useEffect(
  () => ambient.presentation.subscribe(setPresentation),
  []
);
```

The Runtime automatically synchronizes the `--widget-*` CSS variables used by built-in components, page `color-scheme`, and prefers-reduced-motion media emulation. Use `presentation.locale` for dynamic language changes.

### Native HTML, SVG, and styles

`ambient.html` can also create native HTML and SVG elements. Controller JSX is compiled in classic mode to the injected React renderer. Use object-form `style` for CSS, including CSS Grid, and map supplied data into SVG marks. Give graphics an accessible name with semantic elements and `aria-label` or `title`. Markup still goes through HTM/React creation; it needs no DOM APIs, imports, external chart components, or HTML-string parsing.

```javascript
const points = [{ day: "Mon", value: 3 }, { day: "Tue", value: 5 }, { day: "Wed", value: 4 }];
const pointList = points.map((point, index) => `${index * 50},${60 - point.value * 10}`).join(" ");

return (
  <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 16 }}>
    <div>
      <h2>Weekly activity</h2>
      <svg viewBox="0 0 100 70" role="img" aria-label="Activity trend, Monday to Wednesday">
        <title>Activity trend</title>
        <rect x="0" y="0" width="100" height="70" fill="var(--widget-surface-soft)" />
        <polyline points={pointList} fill="none" stroke="var(--accent)" strokeWidth="3" />
      </svg>
    </div>
    <ul aria-label="Daily activity details">
      {points.map((point) => <li key={point.day}>{point.day}: {point.value}</li>)}
    </ul>
  </section>
);
```

SVG and responsive layout are ordinary intrinsic elements and styles inside the existing HTM renderer. Keep a textual summary or detail list beside a chart when readers need exact values.

## 2. Local Widget Storage

`ambient.storage` requires no capability grant. The trusted host persists it in IndexedDB and scopes it to the current App ID; a Controller cannot select another App's namespace.

```javascript
const settings = await ambient.storage.get("settings");
await ambient.storage.set("settings", { compact: true });
const keys = await ambient.storage.list();
await ambient.storage.delete("settings");
await ambient.storage.clear();
```

Keys contain 1–256 characters. Values must be acyclic JSON and no larger than 64 KiB each; each App is limited to 128 keys and 1 MiB total. A missing key returns `null`. Data exists only in the current browser profile and may be cleared by the user or reclaimed by the browser. Never store credentials, tokens, cross-device state, or the sole copy of user data. The legacy `VITE_WIDGET_UI_TRANSPORT=pixels` rollback does not provide this API.

Write user input through after meaningful changes. Gate writes until `storage.get` hydration finishes; otherwise an initial empty/default value can overwrite saved data before it loads. If writes use a debounce, keep the latest value in a ref and register a bounded flush:

```javascript
useEffect(
  () => ambient.lifecycle.onBeforeSuspend(async () => {
    await ambient.storage.set("draft", latestDraftRef.current);
  }),
  []
);
```

A later handler replaces the previous one; a stale effect-cleanup unsubscribe cannot remove a newer registration. The host waits at most one second for acknowledgement before it still unmounts the Runtime, so the handler must remain bounded and cannot replace normal write-through persistence.

## 3. Graph Grants

`graph.query` injects `ambient.graph.subscribe(query, callback)`. A query names its `type`; every include names `target_type`; all entities are within grant scope. It returns an unsubscribe function:

```javascript
useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);
```

`graph.mutate` injects `ambient.graph.mutate(actions)`. Actions map to `create`, `update`, and `delete`; entities and edge types must be approved:

```javascript
await ambient.graph.mutate([{
  action: "create_node",
  type: "Task",
  properties: { title: "Prepare weekly report", status: "open" }
}]);

await ambient.graph.mutate([{
  action: "update_node_property",
  id: taskId,
  properties: { status: "done" }
}]);
```

`action` uses one of the complete DSL names: `create_node`, `update_node_property`, `delete_node`, `create_edge`, or `delete_edge`. The Manifest grant values `create`, `update`, and `delete` are authorization operations, not action payloads; never write `action: "create"` or add an `operation` field. Static verification requires an array literal passed directly to `ambient.graph.mutate`, object-literal entries, and literal action/entity/edge-type identifiers.

The SDK binds current App identity and an idempotency key. The backend resolves actual node types and authorizes before entering the durable Graph effect/interaction flow.

## 4. Network Grant

`network.request` injects `ambient.net.request(sourceId, request)`. Source origin, paths, methods, and response limit come from the grant:

```javascript
const forecast = await ambient.net.request("forecast", {
  path: "/v1/forecast",
  method: "GET",
  query: { latitude: 31.23, longitude: 121.47 }
});
```

The Controller cannot supply a full URL, replace the host, follow redirects, or attach a secret. Authenticated access requests `capability.invoke` for an App Center action.

## 5. File Grants

File paths are POSIX paths relative to `app://data/`:

| Grant | API |
| --- | --- |
| `file.read` | `ambient.files.read(path)`, `ambient.files.list(path)` |
| `file.write` | `ambient.files.write(path, text)` |
| `file.delete` | `ambient.files.delete(path)` |

```javascript
const draft = await ambient.files.read("drafts/today.md").catch((error) => {
  if (error?.code === "file_not_found" || error?.message === "App data file not found") return "";
  throw error; // The caller displays a retryable load error.
});
await ambient.files.write("drafts/today.md", `${draft}\nDone`);
```

Every operation checks path globs, size, escape, and symlinks. The file SDK never accesses the Manifest, Controller, README, or another workspace directory.

`files.read(path)` rejects with code `file_not_found` when a file has not been created yet (older Runtimes may expose only the exact message `App data file not found`). On first load, treat only this missing-file condition as an empty initial value and let the user create the file. Show a visible retryable error for other read failures; do not silently present them as an empty list.

## 6. Installed Capability Grant

`capability.invoke` injects `ambient.capabilities.invoke(catalogId, input, actionId)`. Both IDs are approved string literals:

```javascript
const result = await ambient.capabilities.invoke(
  "mcp:calendar:calendar",
  { title: "Review", start: "2026-07-22T09:00:00+08:00" },
  "create-event"
);
```

The call creates a durable Run and waits for its terminal result. Progress, approval, and `needs_attention` are handled in the Task Drawer. The new version does not inject `ambient.mcp` or arbitrary `runs.start(catalogId, ...)`, preventing bypass of an exact action grant.

## 7. SDK Membrane and errors

- Without a matching grant, the namespace or method is absent. A Controller uses only APIs listed in its Runtime Contract.
- Even when a method exists, the backend may deny a revoked grant, out-of-scope resource, changed Manifest revision, or adapter-policy violation.
- A denial Error includes `code`, `capability`, `operation`, `hint`, and safe `details`.
- Clean up subscriptions and timers in `useEffect`; every async method provides loading, error, and retry UI.

See [Widget Capability Security](/en/architecture/capability-security.md) for authorization and [Runtime Boundary](/en/widgets/sandbox.md) for isolation limits.
