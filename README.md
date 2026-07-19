# Ambient Agent

[English](#english)

Ambient Agent 是一个开源、自托管、以应用工作区为核心的个人 AI 助理。它把对话、持久后台 Run、图数据和 React/HTM Widget 组织在同一个桌面式界面中。

## 核心能力

- 持久 Run：支持 checkpoint、用户确认、取消、重试、恢复和版本化事件流。
- App-first 工作区：应用中心、浮动/最大化/贴靠窗口、任务抽屉和浮层聊天。
- 动态 Widget：`controller.js` 默认导出 React 组件，生成产物先 staging、校验，再原子发布。
- Schema-first Graph：Widget 与 Agent 通过后端校验的 Graph API 共享 Task、Event、Note 及扩展数据。
- Provider Registry：在 UI 中配置本地或云端模型、默认/快速模型和会话覆盖。
- 后端权限与审计：Tool Gateway、MCP、OpenCode、mutation interaction 和 LLM audit。

`SandboxWidget` 不是执行不受信任 JavaScript 的强安全沙箱。Widget controller 与宿主页同 realm 执行，只应加载可信工作区代码。

## 快速开始

```bash
cp .env.example .env
docker compose up --build
```

打开 `http://localhost:5173`，然后在“模型与 Provider”中添加连接并选择默认模型。`.env` 只保存 OpenCode 等进程级参数；Provider 密钥保存在 Git 忽略的 `workspace/llm/secrets.json`。

开发模式：

```bash
uv sync
npm --prefix frontend install
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
npm --prefix frontend run dev
```

完整说明见[中文文档](docs/guide/introduction.md)或 [English documentation](docs/en/guide/introduction.md)。

## 项目结构

```text
backend/          FastAPI、Agent durable workflow、Run、Graph、应用与集成
frontend/src/     React 工作区、应用中心、Widget 宿主和客户端服务
docs/             严格对应的中文与 docs/en/ 英文文档
scripts/          契约生成与 UML、文档、Widget 校验
tests/            Pytest 与 Vitest 测试
workspace/        本地运行数据（Git 忽略）
```

## 验证

```bash
uv run ruff check .
PYTHONPATH=. uv run pytest
uv run python scripts/verify_uml.py
uv run python scripts/verify_docs.py
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
```

## English

Ambient Agent is an open-source, self-hosted personal AI assistant built around an app-first workspace. It combines chat, durable background Runs, graph data, and React/HTM Widgets in one desktop-style interface.

Key capabilities include durable Runs with confirmation and recovery, a windowed App Center workspace, staged and verified Widget publication, schema-first Graph data, UI-configured local or cloud LLM providers, and backend enforcement for tools, MCP, OpenCode, mutations, and audit records.

`SandboxWidget` is not a strong sandbox for untrusted JavaScript. Widget controllers execute in the host page realm and should only load trusted workspace code.

Start with:

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:5173`, add a connection in “Models & Providers,” and select a default model. See the [English documentation](docs/en/guide/introduction.md) for architecture, development, and security details.

## License

[MIT](LICENSE)
