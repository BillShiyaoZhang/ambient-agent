# 系统与请求链路

## 1. 运行时组成

```mermaid
flowchart LR
    Browser[React 工作区] -->|REST| API[FastAPI]
    Browser <-->|聊天 / Graph / Run WebSocket| API
    API --> Workspace[会话、Canvas、审计文件]
    API --> RunStore[.ambient/runs.db]
    API --> Graph[graph.db]
    API --> Apps[workspace/apps]
    API --> LLM[LLM Provider]
    API --> External[MCP / Coding Agent / 本地工具]
```

`backend/main.py` 是装配点。它创建 `WorkspaceStorage`、`LLMConfigStore`、`GraphDatabase`、`AppManager`、`AppStoreService`、`RunStore`、`RunCoordinator` 和 `DurableAgentWorkflow`，并在应用生命周期中恢复可运行任务与清理遗留 staging。

## 2. 用户请求如何执行

1. 前端通过 `/ws/chat` 发送消息。
2. 后端先保存 `ChatMessage`，解析当前会话语言、模型与 Coding Agent 快照，再向 `RunCoordinator` 提交 `internal_agent` Run。
3. Coordinator 持久化 Run，并为同一 session 管理执行 lane。
4. `DurableAgentWorkflow` 调用 `IntentRouter` 生成 `IntentPlan`，然后按 phase 推进状态。
5. 只读对话或查询可以直接完成；Graph mutation、复合任务和 Widget 创建/修改会经过计划、必要的用户 interaction、预检、执行与校验。
6. 每个 step 使用 claim、lease epoch 和 run version 防止过期 worker 提交。可见事件写入 `run_events` 后经 `/ws/runs` 推送。
7. 前端将 Run 状态投影到聊天、任务抽屉、应用中心和工作区。

旧的内存 Agent 循环和 Widget DAG 不再是生产执行路径。`AgentOrchestrator` 只提供路由和有界只读 Converse helper，执行所有权属于 Run 控制平面。

## 3. Widget 创建与加载

Widget 有两条进入路径：

- 对话返回 `<ambient-widget>`：`AgentParser` 提取单个 `<js-script>`，`AppManager` 保存 `controller.js` 和 manifest。
- 创建或修改应用：durable workflow 让用户选择的 OpenCode 或 Codex 在 staging 目录生成 controller；完成语法、安全规则与 schema 校验后才提升为 live app。失败或未获批不会覆盖现有产物。

前端从 `/api/apps/{id}` 获取应用，`SandboxWidget` 使用 Babel 转译 controller，并注入 React、`ambient` API 和系统组件。它提供故障隔离，但不提供 hostile JavaScript 安全边界。

## 4. 数据与通信职责

| 通道/存储 | 用途 |
| --- | --- |
| REST `/api/sessions`, `/api/canvas` | 会话和 Canvas CRUD |
| REST `/api/runs`, `/api/run-interactions` | Run 查询、取消、重试、协调和用户决策 |
| REST `/api/apps`, `/api/app-store` | 应用产物和统一能力目录 |
| REST `/api/coding-agents` | Coding Agent 可用性与默认选择 |
| REST `/api/graph/mutate` | 后端预检后的 Graph mutation |
| `/ws/chat` | 聊天消息、兼容投影和 Widget Graph 订阅/命令 |
| `/ws/runs` | 带 sequence、event ID 和 stream epoch 的可恢复事件流 |
| `workspace/sessions/*.json` | 会话与消息 |
| `workspace/.ambient/runs.db` | Run、step、interaction 和 canonical event |
| `workspace/graph.db` | schema、节点、边、effect 和 mutation history |

## 5. 安全与一致性原则

- Provider 密钥不返回给前端，凭据文件位于 Git 忽略的工作区。
- Codex 只通过带 Bearer token 的本机 Bridge 运行，复用本机 CLI 登录/ChatGPT 订阅；Docker 镜像不安装 Codex、不保存其凭据，后端也不会把 Ambient Agent Provider 密钥或 Run 模型传给 Codex 进程。Bridge 只接受共享 `workspace/apps` 下由后端创建的随机 staging 目录。
- Graph mutation 必须通过 schema 预检，并在 SQLite transaction 中原子提交。
- MCP、工具和 Coding Agent 的授权与沙箱策略在后端执行；前端不注入 API 不能替代授权。
- 有副作用的 durable step 使用 effect/idempotency 记录、interaction 和 fencing，避免恢复或并发造成重复提交。
- Run event 是版本化契约；前端保留未知事件以兼容未来版本。

下一步可阅读[持久 Run](/architecture/runs.md)、[Agent Harness](/agent/harness.md)、[Widget 架构](/architecture/apps.md)或[图数据库](/architecture/graph-db.md)。
