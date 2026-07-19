# Ambient Agent

Ambient Agent 是一个开源、可自托管、支持多端同步的个人 AI 助理框架。与传统的纯文本聊天机器人（Chatbot）不同，Ambient Agent **优先使用富图形交互界面（Widget GUI）** 来展现 AI 的输出，并提供极致的数据隐私保护。

## 🌟 核心特性

1.  **动态 GUI 工作区 (Canvas Workspace)**
    *   AI 能够根据您的指令，在您的屏幕 Canvas 上**动态生成并渲染全新的交互式 Mini App 卡片**（例如天气卡、任务管理器、倒计时、系统状态仪表盘等）。
    *   工作区支持 Widget 的固定（Pin）、拖拽与关闭，并自动将布局持久化在本地。
2.  **安全隔离沙箱与极致体验 (Sandbox & Seamless UX)**
    *   **样式与脚本隔离**：前端内置沙箱渲染组件，将 CSS 样式作用域限制在 Widget 内，利用闭包安全执行 JS，防全局污染。
    *   **无感全屏切换 (Seamless Fullscreen)**：网格视图与全屏视图共用同一个 React DOM 节点和内存上下文。全屏切换时**组件零卸载重装 (Zero Remount)**，状态和已加载数据 100% 完整保留。
    *   **全局 API 缓存拦截 (Sandbox Fetch Cache)**：底层沙箱自动拦截并接管 JS 的 `fetch` 接口。对外部第三方域名的 GET 请求开启 5 分钟的内存缓存，**彻底消除跨生命周期的重复请求**，无需对 Widget 源码做任何改动。
3.  **隐私至上与数据透明 (Privacy First & Audit Log)**
    *   **双模驱动**：支持完全离线的本地模型（通过 **Ollama**）或自主可控的云端 API（支持 MiniMax 等 OpenAI 兼容的云端 API）。
    *   **传输审计**：内置可视化的 **数据传输审计面板（Audit Log）**，清晰记录每一次发送至大模型服务器的 Prompt 完整 Payload 和返回的原始数据，真正做到数据去向可追踪、可审计。
4.  **实时多端同步**
    *   基于单服务多客户端的 FastAPI WebSocket 长连接设计，您在任何设备（手机、平板、PC）上都能同步与同一个 Agent 对话，操作同一个 Canvas 空间。
5.  **坚实的测试防线 (TDD-first)**
    *   项目完全遵循**测试先行**原则开发。在 `/tests` 目录下提供针对后端 API、WebSocket、XML 动态代码解析以及前端沙箱隔离、全局变量污染、状态分发的完整自动化测试集。

---

## 🛠️ 技术栈 (Tech Stack)

*   **后端**: Python 3.11+ / FastAPI / SQLModel (SQLAlchemy) / SQLite / Pytest / httpx
*   **前端**: React 19 / TypeScript / Vite / Tailwind CSS v4 / Vitest / Testing Library

---

## 🚀 快速开始 (Containerized Quick Start)

为了保障开发环境完全一致并防范本地包冲突，本项目**全量采用容器化开发和运行**。本地只需要安装 Docker 即可开始。

### 1. 克隆项目与初始化

首先克隆本项目至本地：
```bash
git clone <repository-url>
cd ambient-agent
```

### 2. 配置运行环境

将根目录下的 `.env.example` 复制为 `.env`：
```bash
cp .env.example .env
```
`.env` 仅用于 OpenCode 等进程级运行参数。启动 Ambient Agent 后，请从“模型与 Provider”设置页添加
OpenAI、Anthropic、MiniMax、Ollama 或其他 LLM 连接并选择默认模型；密钥不会写入 `.env`。

---

### 3. 开发环境配置：使用 VS Code Dev Containers (推荐)

这是最推荐的**代码开发与调试方式**。IDE 会直接连接容器内部，提供免安装的编译器、代码补全、格式化和调试体验。

1. 确保本地已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 并启动，且 VS Code 安装了 `Dev Containers` 插件。
2. 用 VS Code 打开项目根目录。
3. 按下 `Cmd+Shift+P` (Windows/Linux 上是 `Ctrl+Shift+P`)，选择 **`Dev Containers: Reopen in Container`**。
4. 容器会自动在本地启动并安装 Node.js 22、Python 3.11、`uv` 依赖，以及通过官方安装脚本拉取的最新 `opencode` CLI（默认放置于 `/usr/local/bin/opencode`，**宿主机无需预装**）。
5. **在容器内启动服务**：
   - 打开终端 1，启动后端：
     ```bash
     uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
     ```
   - 打开终端 2，启动前端：
     ```bash
     cd frontend
     npm run dev
     ```
6. **手动端口转发（重要）**：
   如果 IDE 没有自动转发 8000 端口，请点击 IDE 的 **Ports（端口）** 标签页，点击 **Add Port**，手动添加 **`8000`** 端口转发。
7. 在本地 Mac 浏览器中访问 **[http://localhost:5173/](http://localhost:5173/)** 开始开发。

---

### 4. 运行环境配置：使用 Docker Compose 一键启动

如果您不需要修改代码，仅想在本地一键拉起整个应用进行运行、演示或集成测试：

1. 在项目根目录下直接运行：
   ```bash
   docker compose up --build
   ```
2. 服务启动后：
   - 后端服务运行在：`http://localhost:8000`
   - 前端服务运行在：`http://localhost:5173`
   - 本地代码修改会自动挂载并同步热重载至容器内。
   - 构建过程中会自动通过官方安装脚本拉取最新 `opencode` 并放入 `/usr/local/bin`，**宿主机无需预装 opencode**。如需校验：
     ```bash
     docker compose run --rm backend opencode --version
     ```

---

## 🧪 单元测试与代码校验

开发过程中，所有代码的规范和功能校验必须在**容器内部终端**运行并通过：

*   **后端代码校验与测试**：
    ```bash
    # 运行风格检查与自动修复 (Ruff)
    uv run ruff check . --fix
    uv run ruff format .
    
    # 运行后端 pytest 单元测试
    PYTHONPATH=. uv run pytest
    
    # 运行 UML 架构与代码一致性校验
    uv run python scripts/verify_uml.py
    ```
*   **前端代码校验与测试**：
    ```bash
    cd frontend
    # 运行代码风格检查
    npm run lint
    
    # 运行前端单元测试
    npm run test
    ```

---

## 📦 项目结构目录

```text
ambient-agent/
├── backend/            # Python FastAPI 源码
│   ├── main.py         # 接口与 WebSocket 服务入口
│   ├── models.py       # SQLModel 数据库模型定义
│   ├── agent_parser.py # 动态 Widget 代码 XML 解析器
│   └── llm_service.py  # LLM 请求适配与数据审计层
├── frontend/           # React 前端源码
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatPanel.tsx       # 侧边栏聊天面板
│   │   │   ├── DashboardCanvas.tsx # 仪表盘工作区 Canvas
│   │   │   ├── SandboxWidget.tsx   # 毛玻璃沙箱渲染组件
│   │   │   └── AuditLogPanel.tsx   # 可视化数据传输审计日志
│   │   ├── services/
│   │   │   └── websocket.ts        # 客户端 WebSocket 通信
│   │   └── App.tsx                 # 根状态机逻辑与集成
│   └── vite.config.ts  # Vite 与 Vitest 配置文件
├── tests/              # 统一测试集
│   ├── backend/        # 后端单元和集成测试
│   └── frontend/       # 前端渲染和隔离沙箱测试
└── README.md
```

---

## 🤝 团队协作与开发规范 (Collaboration Guidelines)

为了保证多人高效、规范地共同开发，项目制定了以下协作流程：

### 1. Git 分支与合并规范 (GitHub Flow)
*   **保护分支**：`main` 为主分支，代表随时可部署的生产环境，禁止直接向 `main` 推送代码。
*   **开发分支**：所有新功能开发或 Bug 修复均应基于 `main` 分支出新的分支，命名规范为：
    *   新功能：`feature/xxx`
    *   修复：`bugfix/xxx`
*   **代码合并**：
    *   开发完毕后，向 `main` 分支发起 Pull Request (PR)。
    *   合并 PR 前，必须通过 GitHub Actions 的自动化 CI 检查（运行所有单元测试、代码风格和 UML 契约检查）。
    *   必须获得至少 **1 名其他团队成员** 的 Review 审核批准 (Approve) 方可合并。
    *   **合并方式**：统一选择 **Create a Merge Commit**，保留开发分支的完整提交轨迹。



---

## 🧩 AI 动态 Widget 协议说明

当大模型需要为用户生成 Widget 时，其回复的文本流中需要包含如下 XML 格式的代码块（已被自动嵌入 `SYSTEM_PROMPT`）：

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<html-content>
  <!-- 符合 HTML5 规范的结构。支持使用常规 Tailwind/CSS 样式类 -->
  <button id="alert-btn" class="bg-purple-600 px-3 py-1 text-xs text-white rounded">
    点击我
  </button>
</html-content>
<css-styles>
  /* 可选：特定的局部样式扩展 */
  .glow-btn { box-shadow: 0 0 8px rgba(170, 59, 255, 0.4); }
</css-styles>
<js-script>
  // 可选：JavaScript 逻辑。
  // 执行时会传入参数 root，指向该 Widget 渲染的 HTML 根 DOM 节点。
  // 必须使用 root.querySelector 来控制元素以保障实例隔离。
  const btn = root.querySelector("#alert-btn");
  btn.addEventListener("click", () => {
    alert("来自 Widget 的隔离运行提示！");
  });
</js-script>
</ambient-widget>
```

后端将会截获该代码块，将其解析并从文本气泡中擦除，把富卡片分发给前端的 `SandboxWidget` 完美展现。

---

## 📄 开源协议

本项目采用 [MIT](LICENSE) 协议开源。
