# 项目介绍 (Introduction)

Ambient Agent 是一个开源、可自托管、支持多端同步的个人 AI 助理框架。与传统的纯文本聊天机器人（Chatbot）不同，Ambient Agent **优先使用富图形交互界面（Widget GUI）** 来展现 AI 的输出，并提供极致的数据隐私保护。

## 💡 为什么选择 Ambient Agent？

在传统的 AI 聊天界面中，用户与大语言模型（LLM）的互动仅限于单调的文字气泡和代码块。当需要处理具有结构化、时间敏感或需要反复操作的数据（如待办列表、天气预报、系统状态仪表盘、计时器）时，纯文本对话显得极其低效。

Ambient Agent 提出了 **Canvas Workspace（画布工作区）** 的概念。AI 能够根据您的自然语言指令，动态生成、加载并渲染一个微型应用卡片（Widget）。这种**富图形交互**让大模型变成了真正的个人系统助理。

## 🌟 核心特性

### 1. 动态 GUI 工作区 (Canvas Workspace)

- **动态生成卡片**：AI 能通过特殊的 XML 格式，在对话中即时构建包含 HTML/CSS/JS 的迷你小程序卡片并热重载到桌面上。
- **灵活布局管理**：画布支持 Widget 的固定（Pin）、自由拖拽布局、最小化与关闭，所有状态都会在本地数据库和浏览器中自动持久化。

### 2. 安全隔离沙箱与极致体验 (Sandbox & Seamless UX)

- **样式与脚本隔离**：前端通过闭包机制执行 Widget JS 并对 CSS 进行 Scope 限制，防止恶意卡片污染全局 Window 或造成样式混乱。
- **无感全屏切换 (Seamless Fullscreen)**：网格与全屏视图共用同一个 React DOM 节点，全屏切换零销毁重装，状态数据完美保留。
- **全局 API 缓存拦截 (Fetch Cache)**：底层沙箱自动拦截并缓存 Widget 的第三方 `fetch` GET 请求，缓存时长为 5 分钟，彻底消除重复网络请求。

### 3. 隐私至上与数据透明 (Privacy First & Audit Log)

- **双模驱动**：支持通过 **Ollama** 完全离线本地运行大模型，也能连接兼容 OpenAI/MiniMax 的云端大模型。
- **传输审计面板 (Audit Log)**：明细记录发送到大模型服务器的每一次 Request Payload 和 Response Payload，数据去向完全透明、可审计。

### 4. 实时多端同步

- 基于 FastAPI WebSocket 长连接服务。您在电脑上创建的卡片，手机或平板登录相同 Session 即可秒级同步显示并支持实时协同交互。
