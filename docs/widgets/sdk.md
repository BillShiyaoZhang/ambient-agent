# ambient SDK

`SandboxWidget` 始终注入纯 UI host features，只为当前 App 的批准 grants 注入对外访问方法。本文记录 Manifest V2 的 SDK；调用方必须处理方法不存在和请求被后端拒绝两种情况。

## 1. 始终可用的 Host Features

| API | 行为 |
| --- | --- |
| `ambient.sendMessage(text)` | 向当前聊天提交用户消息 |
| `ambient.fullscreen()` / `ambient.minimize()` | 请求宿主切换当前 App 窗口状态 |
| `ambient.theme.preference` / `effective` | 读取主题偏好和有效主题 |
| `ambient.html` | 绑定 React createElement 的 HTM tag |
| `ambient.react` | 受支持的 React hooks |
| `ambient.components` | `Column`、`Row`、`Card`、`Text`、`Button`、`TextField`、`Checkbox`、`List`、`Table` |

这些接口不授予外部数据访问。Controller 不使用 `window`、DOM 查询、storage、import、`fetch`、原始 WebSocket、`eval` 或 `Function`。

## 2. Graph Grants

`graph.query` 注入 `ambient.graph.subscribe(query, callback)`；query 必须明确 `type`，include 必须明确 `target_type`，所有实体都在 grant scope 内。方法返回 unsubscribe：

```javascript
useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);
```

`graph.mutate` 注入 `ambient.graph.mutate(actions)`。action 映射到 `create`、`update`、`delete` operation；实体和 edge type 必须获批：

```javascript
await ambient.graph.mutate([{
  action: "update_node_property",
  id: taskId,
  properties: { status: "done" }
}]);
```

SDK 自动绑定当前 App identity 和 idempotency key。后端先解析节点真实类型并授权，再进入 Graph durable effect/interaction 流程。

## 3. Network Grant

`network.request` 注入 `ambient.net.request(sourceId, request)`。source 的 origin、path、method 和 response limit 来自 grant：

```javascript
const forecast = await ambient.net.request("forecast", {
  path: "/v1/forecast",
  method: "GET",
  query: { latitude: 31.23, longitude: 121.47 }
});
```

Controller 不能传完整 URL、覆盖 host、跟随 redirect 或附带 secret。认证访问必须申请应用中心 action 的 `capability.invoke`。

## 4. File Grants

文件路径相对于 `app://data/`，使用 POSIX 分隔符：

| Grant | API |
| --- | --- |
| `file.read` | `ambient.files.read(path)`、`ambient.files.list(path)` |
| `file.write` | `ambient.files.write(path, text)` |
| `file.delete` | `ambient.files.delete(path)` |

```javascript
const draft = await ambient.files.read("drafts/today.md");
await ambient.files.write("drafts/today.md", `${draft}\nDone`);
```

每次操作都检查 path glob、大小、路径逃逸和符号链接。文件 SDK 不访问 Manifest、Controller、README 或其他工作区目录。

## 5. Installed Capability Grant

`capability.invoke` 注入 `ambient.capabilities.invoke(catalogId, input, actionId)`。`catalogId` 与 `actionId` 都必须是批准的字符串字面量：

```javascript
const result = await ambient.capabilities.invoke(
  "mcp:calendar:calendar",
  { title: "Review", start: "2026-07-22T09:00:00+08:00" },
  "create-event"
);
```

调用创建持久 Run，并等待终态结果。进度、approval 或 `needs_attention` 在任务抽屉处理。新版本不向 Widget 注入 `ambient.mcp` 或任意 `runs.start(catalogId, ...)`，避免绕过精确 action grant。

## 6. SDK Membrane 与错误

- 没有对应 grant 时，整个 namespace 或方法不存在；Controller 应只使用 Runtime Contract 中列出的 API。
- 即使方法存在，后端仍可能因 grant 已撤销、resource 超 scope、manifest revision 变化或 adapter policy 而拒绝。
- 拒绝 Error 包含 `code`、`capability`、`operation`、`hint` 和安全 `details`。
- 在 `useEffect` cleanup 中取消订阅和 timer；所有异步方法都必须提供 loading、error 和 retry UI。

完整授权语义见 [Widget 能力安全架构](/architecture/capability-security.md)，运行隔离限制见[运行边界](/widgets/sandbox.md)。
