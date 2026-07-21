# ambient SDK

`SandboxWidget` 将以下对象作为组件 prop 和模块执行参数注入。本文只记录当前代码实际提供的接口。

## 1. 宿主与主题

| API | 行为 |
| --- | --- |
| `ambient.sendMessage(text)` | 通过聊天 WebSocket 以用户身份发送消息 |
| `ambient.fullscreen()` | 将当前应用窗口切换为最大化 |
| `ambient.minimize()` | 将当前应用窗口切换为浮动 |
| `ambient.theme.preference` | 当前偏好：`system`、`light` 或 `dark` |
| `ambient.theme.effective` | 当前有效主题，通常为 `light` 或 `dark` |

这些 API 依赖宿主回调；它们不是浏览器全屏 API，也不负责持久业务数据。

## 2. Graph

### `ambient.graph.subscribe(query, callback)`

注册持久 WebSocket 查询，立即或在数据变化时把查询结果交给 callback。返回 unsubscribe 函数，组件 effect 应直接返回它：

```javascript
useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);
```

### `ambient.graph.mutate(actions)`

向 `POST /api/graph/mutate` 提交一批原子 action。公开 action：`create_node`、`update_node_property`、`delete_node`、`create_edge`、`delete_edge`。

```javascript
await ambient.graph.mutate([{
  action: "update_node_property",
  id: taskId,
  properties: { status: "done" }
}]);
```

宿主为每次调用生成 idempotency key；后端仍会校验 schema、端点和 action。

## 3. 持久 Run 与能力

| API | 返回值/用途 |
| --- | --- |
| `ambient.runs.start(catalogId, actionId, input)` | 创建 Run，返回 Run snapshot |
| `ambient.runs.get(runId)` | 获取最新 Run snapshot |
| `ambient.runs.cancel(runId)` | 请求取消 Run |
| `ambient.runs.subscribe(runId, callback)` | 订阅该 Run 的浏览器事件，返回 unsubscribe |
| `ambient.capabilities.invoke(catalogId, input, actionId?)` | 创建 Run 并等待终态结果 |

调用后端能力时优先使用 `capabilities.invoke`。需要显示进度、取消或自行管理生命周期时使用 `runs.*`。

## 4. App-scoped 外部数据

`ambient.net.request(sourceId, request)` 通过后端安全网关访问当前 App `manifest.json` 中声明的 `data_sources`。`sourceId` 是 App 私有逻辑名，不是 Ambient 预置 capability。

```javascript
const forecast = await ambient.net.request("forecast", {
  path: "/v1/forecast",
  method: "GET",
  query: { latitude: 31.23, longitude: 121.47, hourly: "temperature_2m" }
});
```

返回值是上游 JSON。失败时抛出的 Error 包含 `code`、`hint` 和 `details`；UI 应显示可重试状态。controller 不能传完整 URL、覆盖 host 或直接使用 `fetch`。

## 5. MCP

`ambient.mcp.callTool(name, args)` 通过聊天 WebSocket 请求应用 manifest 声明的 MCP tool，并返回 Promise：

```javascript
const result = await ambient.mcp.callTool("calendar.list_events", { limit: 20 });
```

前端传入的 `name` 不是授权依据。后端按 app identity、manifest、server 生命周期和权限规则重新校验。

## 6. React、HTM 与组件

- `ambient.html`：绑定到 React `createElement` 的 HTM tag。
- `ambient.react`：`useState`、`useEffect`、`useMemo`、`useRef`、`useCallback`、`useContext`、`useReducer`。
- `ambient.components`：`Column`、`Row`、`Card`、`Text`、`Button`、`TextField`、`Checkbox`、`List`、`Table`。

`Row` 与 `Column` 接受 `gap`、`padding`、`align`、`justify`、`wrap` 和 `style`。`wrap` 为布尔值并映射为 flex wrapping；这些布局 prop 由组件消费，不会透传为无效 DOM attribute。

Controller 也能使用注入的 `React`。SDK 不包含 fetch cache、`ambient.model`、任意文件系统访问或秘密读取接口。

## 7. 生命周期与错误处理

- 在 `useEffect` cleanup 中取消 Graph/Run 订阅和自建 timer。
- `graph.mutate`、`net.request`、`capabilities.invoke`、`runs.*` 和 `mcp.callTool` 都可能 reject；在 UI 中提供可重试错误状态。
- Run 可能进入 `waiting_user` 或 `needs_attention`，需要用户在任务抽屉处理，而不是由 Widget 假定自动完成。
- 这些方法暴露的是便利接口，权限 enforcement 位于后端。安全边界见[运行边界](/widgets/sandbox.md)。
