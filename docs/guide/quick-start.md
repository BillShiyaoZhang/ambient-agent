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

`.env` 只保存 OpenCode 等进程级参数。LLM Provider、密钥和默认模型在应用的“模型与 Provider”界面配置；密钥写入被 Git 忽略的 `workspace/llm/secrets.json`，不会写入 `.env`。

## 方式二：Dev Container

1. 用 VS Code 打开仓库并执行 **Dev Containers: Reopen in Container**。
2. `postCreateCommand` 会运行 `uv sync`，并安装 `frontend/` 与 `docs/` 的 npm 依赖。
3. 分别启动后端和前端：

```bash
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm run dev
```

Dev Container 已声明转发 8000、5173 和 5174 端口；如果 IDE 未自动转发，请在 Ports 面板手动添加。

## 方式三：本机开发

```bash
uv sync
npm --prefix frontend install
npm --prefix docs install
```

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
