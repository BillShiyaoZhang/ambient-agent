# 快速开始

## 环境要求

- Docker Desktop（包含 Compose v2）；或本机 Python 3.11–3.13、`uv`、Node.js 22.18+（22.x）或 24.11+，以及 npm。
- 使用 Dev Container 开发时，还需要 VS Code 与 Dev Containers 扩展。

## 方式一：Docker Compose（推荐）

```bash
git clone https://github.com/BillShiyaoZhang/ambient-agent.git
cd ambient-agent
cp .env.example .env
docker compose up --build -d
```

打开 `http://localhost:5173`。后端 API 位于 `http://localhost:8000`。

根目录的 `docker-compose.yml` 是面向日常使用的生产栈：前端在构建阶段生成静态文件，再由小型 Nginx 容器提供服务；运行中的容器不会挂载前端/后端源码、测试目录或 `node_modules`。`workspace/` 仍会挂载到后端，用来持久化会话、App、配置和本地凭据；Neo4j 与 Coding Agent 安装数据使用命名卷。

生产栈发布到宿主机的所有端口默认只绑定 `127.0.0.1`，包括 Frontend、Backend、Widget Frame 以及便于本机调试的 Neo4j Browser/Bolt；同一局域网中的其他设备不能直接访问。当前 Backend 是受信任本机控制面，并不提供登录或多用户认证，因此不要用 Compose 覆盖把这些端口改绑到 `0.0.0.0`，也不要直接经公网或 LAN 反向代理暴露它们。

需要从另一台受信任电脑临时访问时，使用经过认证的 SSH 端口转发，并继续让 Docker 端口保持 loopback：

```bash
ssh -N \
  -L 5173:127.0.0.1:5173 \
  -L 8000:127.0.0.1:8000 \
  -L 8001:127.0.0.1:8001 \
  user@docker-host
```

随后在建立隧道的电脑上打开 `http://localhost:5173`。不要转发或公开 Neo4j 的 `7474`/`7687`。SSH 隧道只保护网络入口，并不会把 Ambient Agent 变成可供不受信任用户共享的服务。

默认配置针对 8GB 家用电脑设置了明确的资源边界：

| 服务 | 内存 / CPU / PID 上限 |
| --- | --- |
| Neo4j | 1250MiB / 1 CPU / 256 PID |
| Backend（包含按需 Coding Agent 子进程） | 1GiB / 1.5 CPU / 256 PID |
| Widget Runtime | 768MiB / 1 CPU / 192 PID |
| Widget Frame | 192MiB / 0.25 CPU / 32 PID |
| Frontend | 128MiB / 0.25 CPU / 32 PID |

所有容器的内存与 swap 上限相同，避免内存压力被持续 swap 掩盖；容器日志也会自动轮转。Backend 使用容器 init 进程回收 Coding Agent 与 MCP 产生的已退出子进程。Neo4j 的 heap 最大值为 512MiB、page cache 为 256MiB。后台 Run 默认全局并发为 1。默认 iframe 工作区稳态只驻留 Active Widget 与至多一个 Warm Widget，挂起过渡最多再保留一个 Suspending Widget；仅 pixel 回滚链路使用的服务端 Chromium 默认最多保留 4 个 Context。需要提高这些值时，请先确认宿主机有足够内存。

从旧版本升级时要检查已有 `.env`：其中显式的
`WIDGET_RUNTIME_MAX_CONTEXTS=16` 会覆盖 Compose 的新默认值。除非已经同步提高并
压测 Runtime 的 768MiB 内存与 192 PID 上限，否则应改为 `4`。

`VITE_WIDGET_UI_TRANSPORT` 与 `VITE_API_BASE_URL` 都会写入前端构建产物。后者统一控制主工作区和隔离 Widget client runtime 的 HTTP/WebSocket Backend 地址：留空时沿用页面协议和 hostname，并使用 `8000` 端口；也可以填写绝对 `http(s)` URL 或相对当前 origin 的路径前缀，HTTPS 会自动派生 WSS。修改任一构建变量后，需要重新运行 `docker compose up --build -d`，而不只是重启容器。

只要浏览器实际访问的 Frontend origin 发生变化，或 Frontend 与 Backend 跨源，就必须把该 Frontend origin（精确的 scheme、host 和 port，不含任何路径；多项用逗号分隔）同步加入 `AMBIENT_FRONTEND_ORIGINS`。设置 `VITE_API_BASE_URL` 只会改变客户端连接地址，不会自动放行 Backend 的 CORS 或 WebSocket Origin 校验。例如，本地前端从 `http://localhost:5173` 连接 `http://localhost:8000`：

```dotenv
VITE_API_BASE_URL=http://localhost:8000
AMBIENT_FRONTEND_ORIGINS=http://localhost:5173
```

修改 `VITE_API_BASE_URL` 后需要重新构建 Frontend；修改 `AMBIENT_FRONTEND_ORIGINS` 后需要重启 Backend。

Docker Compose 也会为规范知识图谱启动 Neo4j，其 Browser 位于 `http://localhost:7474`。默认 Neo4j 凭据只适合受信任本机；修改 `NEO4J_PASSWORD` 也不会给 Backend 增加认证，不能据此公开整个栈。如果需要导入已有的 `workspace/graph.db`，请在一次启动中设置 `GRAPH_MIGRATE_SQLITE=1`，完成后再改回 `0`。

`.env` 只保存 Coding Agent 等进程级参数。LLM Provider、密钥、默认模型和 OpenCode/Codex 选择在应用的“模型与 Provider”界面配置；密钥写入被 Git 忽略的 `workspace/llm/secrets.json`，不会写入 `.env`。

Coding Agent CLI 不全部预装在镜像中。生产镜像与 Dev Container 固定包含 OpenCode 1.17.18；`OPENCODE_VERSION` 是构建参数，修改后必须重建镜像。打开“模型与 Provider”后可按需安装 Codex；安装产物和原生凭据保存在 `coding_agent_data` volume。安装完成后点击“使用 ChatGPT 登录”，在浏览器打开设备码页面并输入一次性代码；成功后界面会从 Codex 动态加载当前账号可用模型。删除该 volume 会同时删除已安装 CLI 和容器内登录状态。生产镜像与 Dev Container 都固定并预装 Codex ACP bridge；直接在宿主机运行后端时，需要安装 `@agentclientprotocol/codex-acp` 并用 `CODEX_ACP_COMMAND` 指向其可执行命令。更新 bridge 配置后必须重建 Dev Container，单纯重启不会刷新镜像层。

Provider Connection 与凭据集中管理，但模型绑定按消费者隔离：Ambient 使用主模型和快速模型；OpenCode 默认继承 Ambient 主模型，也可选择专用 Provider 模型；Codex 使用自己的原生登录和可选原生模型，不接收 Ambient Provider 凭据。

聊天输入框显示的是 `Ambient` 主模型，负责理解请求、路由与规划；代码生成阶段使用单独显示的 `Coding Agent` 及其模型。看到 Ambient 模型名称不表示 Codex 使用了该 Provider。

### 使用 Docker Compose 开发

需要源码热更新时，在生产基线之上叠加开发覆盖文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

该覆盖文件把 Backend 切换为 Uvicorn reload、把 Frontend 切换为 Vite HMR，并重新挂载源码、测试和隔离的 `node_modules` volume。Neo4j、Widget Runtime、端口、数据卷和无 swap 等资源保护仍沿用生产基线；由于 Vite/HMR 本身需要更多资源，开发 Frontend 的上限会提高到 768MiB、1 CPU 和 128 PID。不要用这个命令评估最终用户的空闲资源占用。

## 方式二：Dev Container

1. 用 VS Code 打开仓库并执行 **Dev Containers: Reopen in Container**。
2. Dev Containers 会用 `.devcontainer/docker-compose.yml` 同时启动开发工作区、Neo4j sidecar、服务端 Widget Runtime 和浏览器 Widget Frame；`postCreateCommand` 会运行 `uv sync`，并安装 `frontend/` 与 `docs/` 的 npm 依赖。Python 虚拟环境位于容器专用的 `python_env` volume，不与宿主机项目目录中的 `.venv` 混用，避免 macOS/Linux 解释器互相覆盖。
3. 在开发容器终端中分别启动后端和前端：

```bash
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm run dev
```

开发工作区已经设置 `GRAPH_DATABASE_BACKEND=neo4j`，并通过容器网络地址 `bolt://neo4j:7687` 连接 sidecar。Neo4j 的开发凭据是 `neo4j` / `ambient-agent-dev`，数据保存在独立的 Compose volume 中；这些凭据只适合本机开发。

Dev Container 已声明转发工作区端口 8000、5173、5174、浏览器 Widget Frame 端口 8001，以及 Neo4j Browser/Bolt 端口 7474、7687。Widget Frame 必须能从浏览器通过 `http://localhost:8001` 访问；否则生成阶段的服务端 Runtime smoke test 仍可成功，但打开 Widget 时会报 `runtime_handshake_timeout`。Neo4j Browser 位于 `http://localhost:7474`；如果 IDE 未自动转发，请在 Ports 面板手动添加。关闭 Dev Container 会停止这组 Compose 服务，但不会删除 Neo4j 数据卷。

## 方式三：本机开发

```bash
uv sync
npm --prefix frontend install
npm --prefix docs install
```

本地测试显式使用 SQLite 兼容适配器。若要运行接近生产的本机后端，请先启动 Neo4j，并在启动 Uvicorn 前设置 `GRAPH_DATABASE_BACKEND=neo4j`、`NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` 与 `NEO4J_DATABASE`。

本机后端应只监听 loopback；再在另一个终端启动前端：

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```

若要预览文档：

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
