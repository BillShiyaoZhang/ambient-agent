# ambient SDK

`SandboxWidget` always injects pure UI host features and injects external-access methods only for the current App's approved grants. This page documents the Manifest V2 SDK. Callers handle both an absent method and backend denial.

## 1. Always-available Host Features

| API | Behavior |
| --- | --- |
| `ambient.sendMessage(text)` | Submit a user message to the current chat |
| `ambient.fullscreen()` / `ambient.minimize()` | Ask the host to change the current App window state |
| `ambient.theme.preference` / `effective` | Read theme preference and effective theme |
| `ambient.html` | HTM tag bound to React createElement |
| `ambient.react` | Supported React hooks |
| `ambient.components` | `Column`, `Row`, `Card`, `Text`, `Button`, `TextField`, `Checkbox`, `List`, `Table` |

These interfaces grant no external-data access. Controllers do not use `window`, DOM queries, storage, imports, `fetch`, raw WebSockets, `eval`, or `Function`.

## 2. Graph Grants

`graph.query` injects `ambient.graph.subscribe(query, callback)`. A query names its `type`; every include names `target_type`; all entities are within grant scope. It returns an unsubscribe function:

```javascript
useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);
```

`graph.mutate` injects `ambient.graph.mutate(actions)`. Actions map to `create`, `update`, and `delete`; entities and edge types must be approved:

```javascript
await ambient.graph.mutate([{
  action: "update_node_property",
  id: taskId,
  properties: { status: "done" }
}]);
```

The SDK binds current App identity and an idempotency key. The backend resolves actual node types and authorizes before entering the durable Graph effect/interaction flow.

## 3. Network Grant

`network.request` injects `ambient.net.request(sourceId, request)`. Source origin, paths, methods, and response limit come from the grant:

```javascript
const forecast = await ambient.net.request("forecast", {
  path: "/v1/forecast",
  method: "GET",
  query: { latitude: 31.23, longitude: 121.47 }
});
```

The Controller cannot supply a full URL, replace the host, follow redirects, or attach a secret. Authenticated access requests `capability.invoke` for an App Center action.

## 4. File Grants

File paths are POSIX paths relative to `app://data/`:

| Grant | API |
| --- | --- |
| `file.read` | `ambient.files.read(path)`, `ambient.files.list(path)` |
| `file.write` | `ambient.files.write(path, text)` |
| `file.delete` | `ambient.files.delete(path)` |

```javascript
const draft = await ambient.files.read("drafts/today.md");
await ambient.files.write("drafts/today.md", `${draft}\nDone`);
```

Every operation checks path globs, size, escape, and symlinks. The file SDK never accesses the Manifest, Controller, README, or another workspace directory.

## 5. Installed Capability Grant

`capability.invoke` injects `ambient.capabilities.invoke(catalogId, input, actionId)`. Both IDs are approved string literals:

```javascript
const result = await ambient.capabilities.invoke(
  "mcp:calendar:calendar",
  { title: "Review", start: "2026-07-22T09:00:00+08:00" },
  "create-event"
);
```

The call creates a durable Run and waits for its terminal result. Progress, approval, and `needs_attention` are handled in the Task Drawer. The new version does not inject `ambient.mcp` or arbitrary `runs.start(catalogId, ...)`, preventing bypass of an exact action grant.

## 6. SDK Membrane and errors

- Without a matching grant, the namespace or method is absent. A Controller uses only APIs listed in its Runtime Contract.
- Even when a method exists, the backend may deny a revoked grant, out-of-scope resource, changed Manifest revision, or adapter-policy violation.
- A denial Error includes `code`, `capability`, `operation`, `hint`, and safe `details`.
- Clean up subscriptions and timers in `useEffect`; every async method provides loading, error, and retry UI.

See [Widget Capability Security](/en/architecture/capability-security.md) for authorization and [Runtime Boundary](/en/widgets/sandbox.md) for isolation limits.
