# Widget 格式与生命周期

当前 Widget 使用单文件 React/HTM controller。不要再生成 `<html-content>`、`<css-styles>`、`index.html`、`style.css` 或 `ambient.model`。

## 1. 两种承载形式

持久应用的 live 产物是：

```text
workspace/apps/<app-id>/controller.js
workspace/apps/<app-id>/manifest.json
```

聊天模型需要内联返回 Widget 时，使用 XML 作为传输容器：

```xml
<ambient-widget id="task-board" title="任务看板">
<js-script>
export default function TaskBoard({ ambient }) {
  const { Card, Text } = ambient.components;
  return ambient.html`<${Card} title="任务"><${Text} text="准备就绪" /></${Card}>`;
}
</js-script>
</ambient-widget>
```

`AgentParser` 当前只提取第一个 `<ambient-widget>`，要求 `id` 和 `title` 使用双引号，并把 `<js-script>` 保存为 controller。XML 标签本身不能出现在 `controller.js` 中。

## 2. Controller 契约

- 必须提供一个可渲染的默认导出 React 组件。
- 组件接收 `{ ambient }`；模块执行阶段也可访问注入的 `React` 和 `ambient`。
- 使用 `ambient.html`（HTM）可避免 JSX；也可以使用能被 Babel React preset 转译的 JSX。
- 状态与副作用使用 `ambient.react` 暴露的 hooks。
- 持久数据通过 `ambient.graph`；外部能力通过 `ambient.runs`、`ambient.capabilities` 或 `ambient.mcp`。
- 组件卸载时必须清理订阅、timer 和浏览器事件监听器。

```javascript
export default function TaskList({ ambient }) {
  const { useEffect, useState } = ambient.react;
  const { Button, Card, Column, Text } = ambient.components;
  const [tasks, setTasks] = useState([]);

  useEffect(() => {
    return ambient.graph.subscribe({ type: "Task" }, setTasks);
  }, []);

  async function addTask() {
    await ambient.graph.mutate([{
      action: "create_node",
      type: "Task",
      properties: { title: "New task", description: "", status: "todo", due_date: "" }
    }]);
  }

  return ambient.html`
    <${Card} title="Tasks">
      <${Column} gap=${12}>
        <${Text} text=${`${tasks.length} items`} />
        <${Button} label="Add" onClick=${addTask} />
      </${Column}>
    </${Card}>`;
}
```

## 3. 标准组件

`ambient.components` 当前包含 `Column`、`Row`、`Card`、`Text`、`Button`、`TextField`、`Checkbox`、`List` 和 `Table`。它们提供宿主主题下的基础外观，但不会限制 controller 使用普通 React 元素。

## 4. 生成与发布检查

OpenCode 创建或修改应用时在 Run 专属 staging 目录写入 `controller.js`。发布前依次检查：

1. 路径、文件大小、UTF-8 和默认导出；
2. Node 侧模块/语法和禁止 host capability 规则；
3. controller 中 Graph subscribe/query/mutate 与有效 schema 的一致性；
4. 用户批准的计划或 schema proposal；
5. artifact hash、Run version 和 effect/idempotency 记录。

全部通过后才将 staging 原子提升到 live 目录。修改失败、取消或拒绝不会覆盖现有应用。

## 5. 调试

- 编译错误会显示在 Widget 区域并写入浏览器 console。
- Graph mutation 失败时检查 action 名、节点类型、属性名和 schema 类型。
- MCP 或 capability 失败时到任务抽屉查看对应 Run 和 interaction。
- controller 静态检查可运行 `node scripts/verify_widget_controller.mjs <controller.js>`。
- 不要把 `ErrorBoundary` 或组件名中的 “Sandbox” 当作安全隔离；详见[运行边界](/widgets/sandbox.md)。
