# ambient SDK 参考手册

在 Widget 的 `<js-script>` 逻辑中，您可以通过全局注入的 `ambient` 对象直接调用系统的全部核心服务。下表是 `ambient` 提供的接口列表。

## 1. 对话与窗口管理 (Chat & Layout)

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

将当前全屏状态的 Widget 卡片恢复至其原本的网格大小。

## 2. 局部状态管理 (State)

用于在 A2UI 声明式模式或复杂数据交互中，读写卡片特定的临时数据。

### `ambient.state.get(pointer)`

获取当前局部状态中对应 JSON Pointer（RFC 6901）的值。

- **参数**: `pointer` (String) - 状态指针（如 `"/items/0/title"` 或 `"/todos"`）。
- **返回**: 目标值，若无则返回 `undefined`。

### `ambient.state.set(pointer, val)`

更新局部状态，并在多端同步修改（向后端广播 `STATE_DELTA` WebSocket 消息）。

- **参数**:
  - `pointer` (String) - 状态指针。
  - `val` (Any) - 存入的值。

### `ambient.state.onChange(pointer, callback)`

注册一个事件，当特定的状态字段发生修改时触发回调。

- **参数**:
  - `pointer` (String) - 监听的状态指针。
  - `callback` (Function) - 回调函数，传入新值：`(newVal) => void`。
- **返回**: `unsubscribe` (Function) - 取消监听的函数。

## 3. 图数据库操作 (Graph Database)

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
    // 更新你的 DOM
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
        completed: false,
        priority: 2,
      },
    },
  ]);
  ```

## 4. 模型上下文协议 (MCP)

允许 Widget 卡片调用宿主机上注册的 Model Context Protocol 外部命令行服务或读取外部资源。

### `ambient.mcp.callTool(name, args)`

调用 MCP 工具。

- **参数**:
  - `name` (String) - 工具名称（例如 `"git_commit"`, `"fetch_weather"`）。
  - `args` (Object) - 传递的参数。
- **返回**: Promise - 执行返回的结果。

### `ambient.mcp.readResource(uri)`

读取注册的 MCP 静态资源文件或数据源。

- **参数**: `uri` (String) - 资源的 URI（例如 `"file:///logs/today.txt"`）。
- **返回**: Promise - 资源解析的文本或数据。

## 5. 多智能体协作 (Agent Link)

允许 Widget 卡片和后台运行的高级智能体、外部 Webhook 进行直接的事件收发交互。

### `ambient.agent.connect()`

初始化与后台智能体的实时连接握手。

### `ambient.agent.send(msg)`

向连接的后台智能体发送一则自定义的 Payload 数据包。

- **参数**: `msg` (Object) - 消息对象。

### `ambient.agent.on(eventType, callback)`

监听由后台智能体发回的各类事件消息。

- **参数**:
  - `eventType` (String) - 事件类型（如 `"STATE_SNAPSHOT"`, `"STATE_DELTA"`, 或 `"*"` 监听全部）。
  - `callback` (Function) - 事件回调。
