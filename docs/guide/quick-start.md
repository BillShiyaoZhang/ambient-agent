# 快速开始

## 环境要求

- Docker Desktop；或本机 Python 3.11–3.13、`uv`、Node.js 和 npm。
- 使用 Dev Container 开发时，还需要 VS Code 与 Dev Containers 扩展。

## 方式一：Docker Compose

```bash
git clone <repository-url>
cd ambient-agent
cp .env.example .env
docker compose up --build
```

打开 `http://localhost:5173`。后端 API 位于 `http://localhost:8000`。

Docker Compose 也会为规范知识图谱启动 Neo4j，其 Browser 位于 `http://localhost:7474`。在非纯本机环境暴露服务前请修改 `NEO4J_PASSWORD`。如果需要导入已有的 `workspace/graph.db`，请在一次启动中设置 `GRAPH_MIGRATE_SQLITE=1`，完成后再改回 `0`。

`.env` 只保存 Coding Agent 等进程级参数。LLM Provider、密钥、默认模型和 OpenCode/Codex 选择在应用的“模型与 Provider”界面配置；密钥写入被 Git 忽略的 `workspace/llm/secrets.json`，不会写入 `.env`。

Coding Agent CLI 不全部预装在镜像中。打开“模型与 Provider”后可按需安装 Codex；安装产物和原生凭据保存在 `coding_agent_data` volume。安装完成后点击“使用 ChatGPT 登录”，在浏览器打开设备码页面并输入一次性代码；成功后界面会从 Codex 动态加载当前账号可用模型。删除该 volume 会同时删除已安装 CLI 和容器内登录状态。

Provider Connection 与凭据集中管理，但模型绑定按消费者隔离：Ambient 使用主模型和快速模型；OpenCode 默认继承 Ambient 主模型，也可选择专用 Provider 模型；Codex 使用自己的原生登录和可选原生模型，不接收 Ambient Provider 凭据。

聊天输入框显示的是 `Ambient` 主模型，负责理解请求、路由与规划；代码生成阶段使用单独显示的 `Coding Agent` 及其模型。看到 Ambient 模型名称不表示 Codex 使用了该 Provider。

## 方式二：Dev Container

1. 用 VS Code 打开仓库并执行 **Dev Containers: Reopen in Container**。
2. Dev Containers 会用 `.devcontainer/docker-compose.yml` 同时启动开发工作区和 Neo4j sidecar；`postCreateCommand` 会运行 `uv sync`，并安装 `frontend/` 与 `docs/` 的 npm 依赖。Python 虚拟环境位于容器专用的 `python_env` volume，不与宿主机项目目录中的 `.venv` 混用，避免 macOS/Linux 解释器互相覆盖。
3. 在开发容器终端中分别启动后端和前端：

```bash
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm run dev
```

开发工作区已经设置 `GRAPH_DATABASE_BACKEND=neo4j`，并通过容器网络地址 `bolt://neo4j:7687` 连接 sidecar。Neo4j 的开发凭据是 `neo4j` / `ambient-agent-dev`，数据保存在独立的 Compose volume 中；这些凭据只适合本机开发。

Dev Container 已声明转发工作区端口 8000、5173、5174，以及 Neo4j Browser/Bolt 端口 7474、7687。Browser 位于 `http://localhost:7474`；如果 IDE 未自动转发，请在 Ports 面板手动添加。关闭 Dev Container 会停止这组 Compose 服务，但不会删除 Neo4j 数据卷。

## 方式三：本机开发

```bash
uv sync
npm --prefix frontend install
npm --prefix docs install
```

本地测试显式使用 SQLite 兼容适配器。若要运行接近生产的本机后端，请先启动 Neo4j，并在启动 Uvicorn 前设置 `GRAPH_DATABASE_BACKEND=neo4j`、`NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` 与 `NEO4J_DATABASE`。

然后使用与 Dev Container 相同的后端和前端命令。若要预览文档：

```bash
npm --prefix docs run dev
```

文档站运行在 `http://localhost:5174`。

## 首次配置模型

1. 打开工作区右上角的“模型与 Provider”。
2. 新建 Provider，填写类型、API Base 和凭据。
3. 执行连接测试或模型发现。
4. 选择默认模型；需要时可为单个会话覆盖模型。

未配置有效默认模型时，提交需要模型的请求会返回可操作的 LLM 配置错误。

## 验证命令

```bash
uv run ruff check .
PYTHONPATH=. uv run pytest
uv run python scripts/verify_uml.py
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
```

文档结构或链接修改后，还应运行文档校验脚本：

```bash
uv run python scripts/verify_docs.py
```
