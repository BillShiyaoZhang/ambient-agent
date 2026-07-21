# 项目结构

本页说明每个顶层目录负责什么，以及一次改动通常应该落在哪里。

## 顶层目录

```text
ambient-agent/
├── .devcontainer/          # 开发工作区镜像及 Neo4j sidecar 编排
├── backend/               # FastAPI 组合根与基础设施 adapter
│   ├── agent/             # 路由、reducer、工具、Provider、Prompt 与评测
│   └── capabilities/      # capability ontology、grant、policy 与系统能力目录
├── frontend/              # React 19 + TypeScript + Vite 工作区
│   └── src/
│       ├── components/    # 工作区、聊天、应用中心、任务、设置与 Widget 宿主
│       ├── lib/           # 窗口状态与消息合并等纯逻辑
│       ├── services/      # HTTP、WebSocket、Run、LLM、主题与国际化客户端
│       └── types/         # 由后端契约生成的 Run event 类型
├── docs/                  # Docsify 文档；根目录中文，docs/en/ 英文
├── scripts/               # 契约生成、UML/文档/Widget 校验与评测脚本
├── tests/backend/         # Pytest 后端测试
├── tests/frontend/        # Vitest + Testing Library 前端测试
├── workspace/             # 本地运行数据，Git 忽略
├── docker-compose.yml     # Neo4j + backend + frontend 本地编排
└── pyproject.toml         # Python 版本、依赖与 Ruff 配置
```

## 后端模块地图

| 模块 | 职责 |
| --- | --- |
| `backend/main.py` | FastAPI 生命周期、REST/WebSocket 路由和各服务装配 |
| `backend/run_service.py` | `RunStore`、Run claim/fencing、interaction、event stream、adapter 调度 |
| `backend/agent/durable_workflow.py` | `internal_agent` Run 的显式 phase reducer；计划、确认、执行、校验和发布 |
| `backend/agent/router.py` | 把用户输入分类为 `IntentPlan` |
| `backend/agent/harness.py` | reducer 使用的路由与有界只读 Converse helper；不是第二套执行循环 |
| `backend/capabilities/ontology.py` | Widget 可申请能力的稳定类目与 scope contract |
| `backend/capabilities/models.py` | grant、alignment proposal 与 Runtime Contract 值对象 |
| `backend/capabilities/policy.py` | 默认拒绝的统一 App capability authorizer |
| `backend/capabilities/catalog.py` | 向各类 Agent 投影结构化系统能力目录 |
| `backend/capabilities/files.py` | `app://data/` 文件 adapter 与路径/原子写边界 |
| `backend/graph_db.py` | schema、节点、边、effect 和 mutation history 的 SQLite 存储 |
| `backend/schema_*` | 生成 schema + capability 对齐 proposal，并验证 staging 产物不扩大批准 contract |
| `backend/app_manager.py` | `workspace/apps/<app-id>/` 产物读写和安全路径处理 |
| `backend/app_store.py` | 应用、skill、MCP 能力的统一目录与布局 |
| `backend/opencode_service.py` | 通过 ACP 在隔离 staging 中生成 Widget，并校验/提升产物 |
| `backend/llm_config.py` | Provider profile、凭据、模型目录和默认/会话选择 |
| `backend/workspace_storage.py` | 会话消息、Canvas 和审计日志的工作区文件存储 |

## 前端模块地图

| 模块 | 职责 |
| --- | --- |
| `App.tsx` | 会话、消息、Canvas、Widget 和全局对话框的顶层协调 |
| `AppWorkspace.tsx` | 桌面 chrome、窗口移动/缩放/贴靠/最大化和响应式模式 |
| `AppCenter.tsx` | 统一能力目录、搜索、文件夹、排序和 UI 生成入口 |
| `TaskDrawer.tsx` | Run 历史、待处理 interaction 和 runtime 控制 |
| `SandboxWidget.tsx` | 编译 `controller.js`，根据批准 grants 构造最小 `ambient` membrane，并渲染 React Widget |
| `services/runs.ts` | Run REST 客户端、版本化事件流和断线后的游标恢复 |
| `lib/windowManager.ts` | Canvas V3 迁移、归一化窗口坐标和布局算法 |

## 工作区数据

```text
workspace/
├── sessions/<id>.json       # 会话元数据与消息
├── canvas.json               # Canvas V3 窗口配置
├── audit_logs.jsonl          # LLM 审计记录
├── graph.db                  # SQLite 测试适配器 / 可选 Neo4j 迁移源
├── llm/config.json           # Provider profile 与模型选择（不含秘密）
├── llm/secrets.json          # Provider 凭据
├── apps/<app-id>/            # manifest.json V2、controller.js、README.md
│   └── data/                 # grant 约束的 App 私有 Widget 文件根
├── apps/.ambient/app_records.db
└── .ambient/runs.db          # Runs、steps、events、interactions
```

`workspace/` 是本地状态，不应提交到 Git。部署环境的规范本体与上下文图保存在 Neo4j；App 私有运行态数据位于 `workspace/apps/<app-id>/data/`。新版本不加载 V1 App 或旧三文件 Widget；迁移必须显式重新走 schema/capability 对齐并发布 manifest V2。

## 常见改动入口

- 新增或修改 API：先更新对应架构/API 文档和测试，再修改 `backend/main.py` 与服务层。
- 修改 Agent 行为：从 `IntentPlan`、durable phase 和 Run event 契约一起考虑。
- 修改 Widget API：同步更新 `SandboxWidget.tsx`、后端 enforcement、SDK 中英文页和测试。
- 修改 Widget 对外能力：先修改 Capability Ontology 和本页的安全架构，再写 policy/contract 测试；不得在 route 内添加特例。
- 修改核心类或模型：同步更新中英文 UML，并运行 `scripts/verify_uml.py`。
- 修改文档页面：中文路径与 `docs/en/` 下英文路径必须一一对应，并运行 `scripts/verify_docs.py`。
