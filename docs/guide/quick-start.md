# 快速开始

## 环境要求

- Docker Desktop；或本机 Python 3.11–3.13、`uv`、Node.js 和 npm。使用本机 Codex Bridge 时也需要本机 Python 与 `uv`。
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

Codex 不安装在 Docker 容器中。要复用本机 Codex 登录和 ChatGPT 订阅：

1. 确认本机 `codex login status` 已登录。
2. 运行 `openssl rand -hex 32`，把结果填入 `.env` 的 `CODEX_HOST_BRIDGE_TOKEN`。
3. 在仓库根目录启动 `uv run python -m scripts.codex_host_bridge`，并保持该进程运行。
4. 启动 Docker Compose，然后在前端选择 Codex；卡片会显示本机 Bridge 与登录状态。

Bridge 默认仅监听 `127.0.0.1:8765`，使用 Bearer token 鉴权，并且只接受后端创建在共享 `workspace/apps` 下的随机 staging 目录。容器内不保存 Codex 凭据。

## 方式二：Dev Container

1. 用 VS Code 打开仓库并执行 **Dev Containers: Reopen in Container**。
2. Dev Containers 会用 `.devcontainer/docker-compose.yml` 同时启动开发工作区和 Neo4j sidecar；`postCreateCommand` 会运行 `uv sync`，并安装 `frontend/` 与 `docs/` 的 npm 依赖。
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
