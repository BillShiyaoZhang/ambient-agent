# ambient SDK

在 Widget 脚本逻辑中，您可以通过全局注入的 `ambient` 对象直接调用系统的全部核心服务。下表是 `ambient` 提供的接口列表。

## 1. 对话与窗口管理

### `ambient.sendMessage(text)`

以用户的身份向会话发送一条文本消息，使大模型能够收到反馈。

- **参数**: `text` (String) - 消息内容。
- **示例**:
  ```javascript
  ambient.sendMessage("帮我总结当前这个卡片的状态");
  ```

### `ambient.fullscreen()`

将当前 Widget 卡片在 Canvas 画布上扩展至全屏显示。

### `ambient.minimize()`

将当前最大化 Widget 恢复为可移动、可缩放的浮动窗口。

### `ambient.theme`

当前宿主主题的只读快照：

```javascript
ambient.theme.preference // "system" | "light" | "dark"
ambient.theme.effective  // "light" | "dark"
```

主题变化会让 Widget React 根节点重新渲染。标准 `ambient.components` 自动使用新的语义色；自定义颜色可根据 `ambient.theme.effective` 自行切换。

## 2. 图数据库操作

这是最核心的部分，允许卡片与系统的 SQLite 图数据库实时读写。所有数据必须对齐注册的 Schema 契约（如 `Task`, `Event`, `Note`）。

### `ambient.graph.subscribe(query, callback)`

发起对图数据库节点的实时查询订阅。每当底层数据发生 Mutate 修改时，后台会自动重跑查询并通过 WebSocket 将最新数据推送给该回调。

- **参数**:
  - `query` (Object) - 查询契约参数。例如按类型查询节点。
  - `callback` (Function) - 接收更新后数据的回调：`(data) => void`。
- **返回**: `unsubscribe` (Function) - 销毁订阅的函数。
- **示例**:
  ```javascript
  const unsub = ambient.graph.subscribe({ type: "Task" }, (tasks) => {
    console.log("最新的 todo 列表：", tasks);
  });
  // 组件销毁时调用 unsub();
  ```

### `ambient.graph.mutate(actions)`

向后端发送事务型的图变更操作包（如增删改节点或关联关系）。

- **参数**: `actions` (Array) - 操作动作数组。
- **返回**: Promise - 包含操作执行结果。
- **示例**:
  ```javascript
  await ambient.graph.mutate([
    {
      action: "create_node",
      id: "todo-new-uuid",
      type: "Task",
      properties: {
        title: "明天去买咖啡",
        status: "pending",
      },
    },
  ]);
  ```

## 3. 模型上下文协议

允许 Widget 卡片调用宿主机上注册的 Model Context Protocol 外部服务工具：

### `ambient.mcp.callTool(name, args)`

调用 MCP 工具。

- **参数**:
  - `name` (String) - 工具名称（例如 `"git_commit"`, `"fetch_weather"`）。
  - `args` (Object) - 传递的参数。
- **返回**: Promise - 执行返回的结果。
- **示例**:
  ```javascript
  const weather = await ambient.mcp.callTool("fetch_weather", { location: "Beijing" });
  ```

## 4. 后台 Run

- `ambient.runs.start(catalogId, actionId, input)`：创建持久后台 Run，立即返回 Run snapshot。
- `ambient.runs.get(runId)`：读取 Run 当前状态、进度和结构化结果。
- `ambient.runs.cancel(runId)`：请求协作式取消。
- `ambient.runs.subscribe(runId, callback)`：订阅该 Run 的持久事件，返回取消订阅函数。
- `ambient.capabilities.invoke(catalogId, input, actionId?)`：创建 Run 并等待 terminal result 的便捷封装。

关闭 Widget 窗口只会移除订阅回调，不会取消 Run。

## 5. 内置 React 与 UI 支持

`ambient` 对象还暴露了 React 环境本身以及一套由 Tailwind CSS 渲染的优质 UI 组件库，Widget 无需自行导入或使用外部 CSS：

- **`ambient.react`**: 暴露标准的 React Hooks，例如 `useState`, `useEffect`, `useMemo`, `useRef`, `useCallback`。
- **`ambient.components`**: 暴露精心设计的 React 元组件。包括：
  - `Card` (卡片容器)
  - `Button` (按钮)
  - `TextField` (输入框)
  - `Checkbox` (复选框)
  - `List` (列表容器)
  - `Table` (表格)
  - `Column` / `Row` (弹性盒子布局)
  - `Text` (排版文本)
- **`ambient.html`**: 提供基于 `htm` 的声明式模板标记渲染方法。
