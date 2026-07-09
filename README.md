# Ambient Agent

Ambient Agent 是一个开源、可自托管、支持多端同步的个人 AI 助理框架。与传统的纯文本聊天机器人（Chatbot）不同，Ambient Agent **优先使用富图形交互界面（Widget GUI）** 来展现 AI 的输出，并提供极致的数据隐私保护。

## 🌟 核心特性

1.  **动态 GUI 工作区 (Canvas Workspace)**
    *   AI 能够根据您的指令，在您的屏幕 Canvas 上**动态生成并渲染全新的交互式 Mini App 卡片**（例如天气卡、任务管理器、倒计时、系统状态仪表盘等）。
    *   工作区支持 Widget 的固定（Pin）、拖拽与关闭，并自动将布局持久化在本地。
2.  **安全隔离沙箱 (Sandbox UI Rendering)**
    *   前端内置沙箱渲染组件，将 AI 生成的 HTML 独立挂载，将 CSS 样式作用域严格限制在 Widget 容器内，同时利用局部作用域安全执行 JS 脚本，防止污染全局全局 `window` 上下文。
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

## 🚀 快速开始

### 1. 克隆与初始化

首先克隆本项目至本地：

```bash
git clone <repository-url>
cd ambient-agent
```

### 2. 配置环境变量

将根目录下的 `.env.example` 复制为 `.env`：

```bash
cp .env.example .env
```

打开 `.env` 文件，根据您的需要填入 LLM 配置。例如配置 **MiniMax** 的 API：

```ini
LLM_PROVIDER=minimax
LLM_MODEL=abab6.5g-chat
LLM_API_URL=https://api.minimax.chat/v1/text/chatcompletion_v2
LLM_API_KEY=您的_minimax_api_key_
```

如果使用本地 **Ollama**：
```ini
LLM_PROVIDER=ollama
LLM_MODEL=llama3
# OLLAMA_API_URL=http://localhost:11434/api/generate
```

### 3. 运行后端 (FastAPI)

1.  创建并激活 Python 虚拟环境：
    ```bash
    python -m venv venv
    # Windows 激活方式:
    .\venv\Scripts\activate
    # macOS/Linux 激活方式:
    source venv/bin/activate
    ```
2.  安装依赖项：
    ```bash
    pip install -r backend/requirements.txt
    ```
3.  启动开发服务器：
    ```bash
    uvicorn backend.main:app --reload --port 8000
    ```

### 4. 运行前端 (React)

1.  进入前端目录：
    ```bash
    cd frontend
    ```
2.  安装依赖项并启动开发服务器：
    ```bash
    npm install
    npm run dev
    ```
3.  在浏览器中打开：`http://localhost:5173/`

---

## 🧪 运行自动化测试

我们提供了完善的前后端测试套件，用以在您修改代码后立即验证边界功能。

*   **运行后端测试 (Pytest)**:
    在根目录下执行：
    ```bash
    .\venv\Scripts\python -m pytest
    ```
*   **运行前端测试 (Vitest)**:
    在 `/frontend` 目录下执行：
    ```bash
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
