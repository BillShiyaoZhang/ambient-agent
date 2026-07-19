# 项目结构

本页说明每个顶层目录负责什么，以及一次改动通常应该落在哪里。

## 顶层目录

```text
ambient-agent/
├── backend/               # FastAPI、Agent、持久 Run、Graph、应用与集成
│   └── agent/             # 路由、reducer、工具、Provider、Prompt 与评测
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
├── forward/               # 设计方向和实施计划，不是运行时代码
├── workspace/             # 本地运行数据，Git 忽略
├── docker-compose.yml     # backend + frontend 本地编排
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
| `backend/graph_db.py` | schema、节点、边、effect 和 mutation history 的 SQLite 存储 |
| `backend/schema_*` | 从 controller 提取 Graph 使用并完成 schema diff、对齐与验证 |
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
| `SandboxWidget.tsx` | 编译 `controller.js`、注入 `ambient` API、渲染 React Widget |
| `services/runs.ts` | Run REST 客户端、版本化事件流和断线后的游标恢复 |
| `lib/windowManager.ts` | Canvas V3 迁移、归一化窗口坐标和布局算法 |

## 工作区数据

```text
workspace/
├── sessions/<id>.json       # 会话元数据与消息
├── canvas.json               # Canvas V3 窗口配置
├── audit_logs.jsonl          # LLM 审计记录
├── graph.db                  # schema、节点、边、effect、mutation history
├── llm/config.json           # Provider profile 与模型选择（不含秘密）
├── llm/secrets.json          # Provider 凭据
├── apps/<app-id>/            # manifest.json、controller.js、README.md
├── apps/.ambient/app_records.db
└── .ambient/runs.db          # Runs、steps、events、interactions
```

`workspace/` 是本地状态，不应提交到 Git。旧的 `db.sqlite3`、`backend/apps/` 和 `graph.json` 只在启动迁移或兼容代码中出现，不是当前写入接口。

## 常见改动入口

- 新增或修改 API：先更新对应架构/API 文档和测试，再修改 `backend/main.py` 与服务层。
- 修改 Agent 行为：从 `IntentPlan`、durable phase 和 Run event 契约一起考虑。
- 修改 Widget API：同步更新 `SandboxWidget.tsx`、后端 enforcement、SDK 中英文页和测试。
- 修改核心类或模型：同步更新中英文 UML，并运行 `scripts/verify_uml.py`。
- 修改文档页面：中文路径与 `docs/en/` 下英文路径必须一一对应，并运行 `scripts/verify_docs.py`。
