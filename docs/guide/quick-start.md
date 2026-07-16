# 快速开始

本项目为了保障开发环境完全一致并防范本地依赖冲突，全量支持**容器化开发和运行**。本地只需要安装 Docker 即可开始。

## 📦 环境要求

- **Docker Desktop** (保证容器运行环境)
- **VS Code** 并安装 **Dev Containers** 插件 (推荐的 IDE 容器开发模式)

## 🚀 步骤 1：克隆项目与环境变量配置

首先克隆本项目至本地目录：

```bash
git clone <repository-url>
cd ambient-agent
```

将根目录下的 `.env.example` 复制为 `.env`：

```bash
cp .env.example .env
```

打开 `.env` 文件，根据您的需要填入大模型（LLM）配置（如 Ollama 端口、MiniMax API Key 等）：

```ini
# LLM Providers Config
OLLAMA_API_BASE=http://localhost:11434
# MiniMax or OpenAI-compatible settings
MINIMAX_API_KEY=your-api-key
```

## 🛠️ 步骤 2：使用 VS Code Dev Containers 开发 (推荐)

这是最推荐的**代码开发与调试方式**。IDE 会直接连接容器内部，提供免安装的编译器、代码补全、格式化和调试体验。

1.  启动本地 Docker Desktop。
2.  用 VS Code 打开项目根目录。
3.  按下 `Cmd+Shift+P` (Windows/Linux 上是 `Ctrl+Shift+P`)，选择 **`Dev Containers: Reopen in Container`**。
4.  容器会自动在本地启动并安装 Node.js 22、Python 3.11、`uv` 依赖，以及 `opencode` CLI 等开发工具。
5.  **启动后端服务**（打开终端 1）：
    ```bash
    uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
    ```
6.  **启动前端服务**（打开终端 2）：
    ```bash
    cd frontend
    npm run dev
    ```
7.  **进行端口转发（重要）**：
    如果 IDE 没有自动转发 8000 端口，请点击 VS Code 底部的 **Ports** 标签页，点击 **Add Port** 手动添加 `8000` 端口转发。
8.  在浏览器中访问 `http://localhost:5173/` 即可开始体验。

## 🐳 步骤 3：使用 Docker Compose 一键运行

如果您不需要修改代码，仅想在本地一键拉起整个应用进行运行、演示或集成测试：

1.  在项目根目录下直接运行：
    ```bash
    docker compose up --build
    ```
2.  服务启动后：
    - 后端服务运行在：`http://localhost:8000`
    - 前端服务运行在：`http://localhost:5173`
    - 本地代码修改会自动挂载并同步热重载至容器内。

## 🧪 单元测试与代码校验

开发过程中，所有代码的规范和功能校验必须在容器内部终端运行并通过：

### 后端代码校验与测试

```bash
# 运行风格检查与自动修复 (Ruff)
uv run ruff check . --fix
uv run ruff format .

# 运行后端 pytest 单元测试
PYTHONPATH=. uv run pytest

# 运行 UML 架构与代码一致性校验
uv run python scripts/verify_uml.py
```

### 前端代码校验与测试

```bash
cd frontend
# 运行代码风格检查
npm run lint

# 运行前端单元测试
npm run test
```
