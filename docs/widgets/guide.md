# Widget 格式与生命周期

当前 Widget 使用 Manifest V2 + 单文件 React/HTM Controller。默认情况下，Controller 在用户浏览器的 opaque-origin sandbox iframe 内执行并原生渲染；Docker 中的 Chromium 像素流只作为临时回滚路径。不要生成内联 XML Widget、`index.html`、`style.css` 或已删除的旧 SDK。

## 1. 唯一承载形式

```text
workspace/apps/<app-id>/
├── manifest.json
├── controller.js
├── README.md
└── data/
```

所有创建与修改都通过持久 Widget workflow，在 schema + capability 审批后写入 staging，再校验并原子发布。聊天模型不能直接返回或保存可执行 Widget。

## 2. Controller 契约

- 必须默认导出可渲染的 React 组件，并接收 `{ ambient }`。
- 使用 `ambient.html` 或可由 Babel React preset 转译的 JSX。
- 状态与副作用使用 `ambient.react` hooks。
- 只使用 Runtime Contract 中列出的 SDK；Graph、Network、Files 或 installed capabilities 需要对应 grant。
- Capability/source/catalog/action ID 使用字符串字面量，不能在运行时拼接。
- 卸载时清理订阅和 timer；禁止直接浏览器事件、DOM、Cookie、浏览器 storage globals、网络和动态代码 API。本地非秘密状态只使用 `ambient.storage`。

```javascript
export default function TaskList({ ambient }) {
  const { useEffect, useState } = ambient.react;
  const { Button, Card, Column, Text } = ambient.components;
  const [tasks, setTasks] = useState([]);

  useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);

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
      <//>
    <//>`;
}
```

## 3. 标准组件

`ambient.components` 包含 `Column`、`Row`、`Card`、`Text`、`Button`、`TextField`、`Checkbox`、`List` 和 `Table`。它们提供宿主主题下的基础外观，不授予外部访问能力。

## 4. 生成与发布检查

Runtime Contract 是发布协调器使用的审批信封，不是 `manifest.json` 的文件格式。Coding Agent 只能把其中的 `app_id`、schema ID 列表和规范化 capabilities 分别映射为 Manifest V2 的 `id`、`schema_refs` 和 `capabilities`；不得把 `contract_version`、`catalog_version`、`schemas`、`grants_digest` 或 `allowed_files` 写入 Manifest。生成提示必须同时给出完整的 Manifest V2 模板，修复提示也必须保持这一映射，避免把审批信封误复制为 App 产物。`intents` 必须是由唯一、非空字符串组成的数组，不能使用对象。

发布前依次检查：

1. 安全路径、允许文件、大小、UTF-8 和默认导出；
2. 模块语法、禁止 host global/import/dynamic code 规则；
3. Controller capability 使用是批准 grants 的子集；
4. staging Manifest grants 与批准 Runtime Contract 完全相等；
5. Graph 使用与有效 schema 一致；
6. artifact hash、grants digest、Run version 和 effect/idempotency 记录。

全部通过后才将 staging 原子提升。失败、取消或拒绝不会覆盖现有 App。校验错误若被确定性策略判定为只需改代码且不改变批准 contract，Coding Agent 会在同一 staging 和 ACP session 中持续“修复 → 独立校验”，不再受固定三轮上限约束。完全相同的 finding 连续出现或受校验 artifact hash 没有变化时立即熔断；基础设施错误、需要扩权/改 schema 的错误也不会被盲目交给 Coding Agent。finding 历史会随 failed draft 持久化，因此新的 Run attempt 不能从零开始重复同一失败。promotion 前仍未解决的内部校验失败、超时或系统错误会连同草稿一起保留在不可执行的隐藏 staging 中；用户重试会在该目录原地修复或继续校验，而不是先删除再生成。若错误来自 Controller 与 Manifest grant 不一致，修复 turn 会再次获得已批准 Runtime Contract，只能修正现有 `controller.js`/`manifest.json` 使其匹配，不能申请或扩大权限。只有显式取消、返工或超过草稿保留期后才会清理该 staging。

## 5. 调试

- 编译/渲染错误由隔离 iframe Runtime 以结构化 `runtime_error` 返回并显示在 Widget 区域；Controller console 不进入宿主页面 realm。
- 生成失败时，聊天会显示 App ID、失败阶段、错误码和原因；直接回复 `/repair <app-id> [补充说明]` 可在保留草稿上继续修复。
- `capability_denied` 先检查 Manifest grant 的 entity/operation/source/path/action scope。
- 有 interaction 或 `needs_attention` 时到任务抽屉处理。
- 静态检查运行 `node scripts/verify_widget_controller.mjs <controller.js>`。
- 完整 API 见 [ambient SDK](/widgets/sdk.md)，隔离模型见 [Widget 隔离运行时](/widgets/sandbox.md)，授权模型见 [Widget 能力安全架构](/architecture/capability-security.md)。
