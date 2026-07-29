# 系统与请求链路

## 1. 运行时组成与分层

```mermaid
flowchart LR
    Browser[表现层：React 工作区] -->|REST / WebSocket| API[组合根：FastAPI]
    Browser -->|固定静态资产| Frame["widget-frame：opaque-origin iframe"]
    API --> Workflow[应用层：Use Case / Durable Workflow]
    Workflow --> Domain[领域层：Run / Ontology / Capability Policy]
    Workflow --> Infra[基础设施层：Graph / Files / HTTP / MCP / LLM]
    Infra --> Workspace[workspace 持久状态]
    API <-.->|仅 pixel 回滚：Unix socket| Runtime["按需 widget-runtime + Chromium"]
```

`backend/main.py` 是组合根，只创建并连接 `WorkspaceStorage`、graph adapter、App/Capability 服务、`RunCoordinator` 和 Workflow。业务规则属于领域/应用对象，route 不直接决定授权或操作存储。完整依赖规则见 [Widget 能力安全架构](/architecture/capability-security.md)。

## 2. 用户请求如何执行

1. 前端通过 `/ws/chat` 发送消息。
2. 后端先保存 `ChatMessage`，解析当前会话语言、模型与 Coding Agent 快照，再向 `RunCoordinator` 提交 `internal_agent` Run。
3. Coordinator 持久化 Run，并为同一 session 管理执行 lane。
4. `DurableAgentWorkflow` 调用 `IntentRouter` 生成 `IntentPlan`，然后按 phase 推进状态。
5. 只读对话或查询可以直接完成；Graph mutation、复合任务和 Widget 创建/修改经过计划、schema + capability 对齐审批、预检、执行与校验。
6. 每个 step 使用 claim、lease epoch 和 run version 防止过期 worker 提交。可见事件写入 `run_events` 后经 `/ws/runs` 推送。
7. 前端将 Run 状态投影到聊天、任务抽屉、应用中心和工作区。

旧的内存 Agent 循环和 Widget DAG 不再是生产执行路径。`AgentOrchestrator` 只提供路由和有界只读 Converse helper，执行所有权属于 Run 控制平面。

## 3. Widget 创建与加载

Widget 只有一条发布路径：durable workflow 先确认计划，再让用户批准 schema 与 capability proposal；随后用户选择的 OpenCode 或 Codex 在 staging 目录生成 manifest V2 与 controller。只有当代码使用是批准 grants 的子集、manifest grants 与批准值完全相等，并通过语法/安全/schema 校验后，产物才会原子提升。对话内联 XML Widget 与未验证直写路径已退出新版本。

前端从 `/api/apps/{id}` 获取应用元数据，但可信 React host 不执行 Controller。
默认 `SandboxWidget` 获取一次性 ticket，通过
`/ws/widgets/{id}/client-runtime` 连接 Backend，并把 Controller 交给
`sandbox="allow-scripts"` 的 opaque-origin iframe；Babel、renderer 和
Controller 都在该 frame 内运行。Workspace 稳态只保留当前 Active Widget 与
至多一个 Warm Widget；其余 Widget 在有界 `before_suspend` 刷盘后卸载，过渡期
最多额外保留一个 Suspending Widget。Graph、Network、Files 和 capability RPC
回到 Backend，由 server-side session 绑定 App 身份并逐次授权。

`VITE_WIDGET_UI_TRANSPORT=pixels` 才启用旧的 `/ws/widgets/{id}/runtime` 像素流。
该回滚链路在零网络 `widget-runtime` 中按需启动一个共享 Chromium，每个会话使用
独立 BrowserContext；最后一个会话关闭 60 秒后回收浏览器。

## 4. 数据与通信职责

| 通道/存储 | 用途 |
| --- | --- |
| REST `/api/sessions`, `/api/canvas` | 会话和 Canvas CRUD |
| REST `/api/runs`, `/api/run-interactions` | Run 查询、取消、重试、协调和用户决策 |
| REST `/api/apps`, `/api/app-store` | 应用产物和统一能力目录 |
| REST `/api/coding-agents` | Coding Agent 可用性与默认选择 |
| REST `/api/apps/{id}/graph/*` | App-scoped、grant 授权并预检后的 Graph 查询与 mutation |
| REST `/api/apps/{id}/files/*` | `app://data/` 内经过 path grant 授权的文件操作 |
| REST `/api/apps/{id}/data-sources/*` | `network.request` grant 中声明的公共 HTTPS JSON source |
| `/ws/chat` | 聊天命令与 App-scoped Graph 订阅 |
| `/ws/widgets/{id}/client-runtime` | 默认 iframe Widget 的 ticket 认证 bootstrap、RPC 与生命周期 |
| `/ws/widgets/{id}/runtime` | 仅 pixel 回滚的帧、输入和生命周期 |
| Unix socket `widget-runtime.sock` | 仅 pixel 回滚/生成 smoke test 使用的 Runtime 协议 |
| `/ws/runs` | 带 sequence、event ID 和 stream epoch 的可恢复事件流 |
| `workspace/sessions/*.json` | 会话与消息 |
| `workspace/.ambient/runs.db` | Run、step、interaction 和 canonical event |
| Neo4j | 规范本体实体、上下文 record、graph edge、effect 和 mutation history |
| `workspace/graph.db` | 仅用于显式 SQLite 测试适配器和按需迁移源 |

## 5. 安全与一致性原则

- Provider 密钥不返回给前端，凭据文件位于 Git 忽略的工作区。
- Coding Agent Runtime 使用可信内置 Adapter，将 CLI 按需安装到专用持久卷，并统一管理安装、认证、动态模型发现、模型绑定与运行状态。代码生成只经过一个 ACP orchestration：OpenCode 提供原生 ACP server；Codex 由固定版本的 ACP Registry bridge 映射到官方 app-server。两者共用 Ambient 的 session、权限、staging、验证和同 session repair 状态机。Codex 通过容器内设备码登录使用自己的 ChatGPT 订阅，并通过 app-server `model/list` 返回当前账号可选模型；后端不会把 Ambient Provider 密钥或模型绑定传给 native 模式的 Codex。
- Docker Compose 放开默认 seccomp 对非特权 user namespace 的拦截，使 Codex 能在容器边界内继续使用自己的 bubblewrap `workspace-write` 沙箱；不授予 `SYS_ADMIN`，也不切换到 `danger-full-access`。
- Backend 镜像内置与前端锁文件一致的 Node.js 与 `@babel/standalone` verifier runtime。所有 Coding Agent 生成的 `controller.js` 只有通过语法、禁用 host/network global 与受限 VM 执行检查后才会从 staging 提升为 live App；校验器缺失时必须失败关闭，不能发布未验证代码。
- Coding Agent 只接收从 [Agent 系统能力目录](/agent/system-capabilities.md) 生成的角色投影和不可变 Runtime Contract。生成契约禁止 `fetch`、浏览器 host global、直接 MCP 和未批准访问；staging 校验失败时只返回有界诊断进行修复。
- Graph mutation 必须通过规范本体预检，并在一个 Neo4j transaction 中原子提交。
- Widget 外部访问由 Capability Ontology、批准 grant、静态 verifier、SDK membrane 与后端 authorizer 共同约束；MCP、工具和 Coding Agent 仍叠加各自的 adapter policy。
- 默认路径的不可信 Widget 代码只在 opaque-origin iframe 内执行，可信 React host
  与 Backend 都不求值 Controller。pixel 回滚路径改在独立 `widget-runtime`
  容器中执行；该容器无网络、无宿主/工作区/Docker socket/凭据挂载，使用只读
  rootfs、tmpfs、非 root 用户和资源上限。
- Runtime session 在 Backend 绑定 `app_id + manifest revision + grants digest + artifact digest`。Controller payload 中的身份字段一律忽略；撤权或 revision/digest 改变会终止旧 session。
- 有副作用的 durable step 使用 effect/idempotency 记录、interaction 和 fencing，避免恢复或并发造成重复提交。
- Run event 是版本化契约；前端保留未知事件以兼容未来版本。

下一步可阅读 [Widget 隔离运行时](/widgets/sandbox.md)、[Widget 能力安全架构](/architecture/capability-security.md)、[Agent 系统能力目录](/agent/system-capabilities.md)、[持久 Run](/architecture/runs.md)或[图数据库](/architecture/graph-db.md)。
