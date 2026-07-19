# 项目介绍

Ambient Agent 是一个开源、自托管的个人 AI 助理。它把对话、持久后台任务、图数据和可交互 Widget 组织在同一个桌面式工作区中。

## 项目解决什么问题

普通聊天界面适合一次性问答，却不擅长承载需要持续运行、确认副作用或反复操作的数据任务。Ambient Agent 将一次用户请求保存为 **Run**，由 Agent 对请求分类，再按显式阶段执行。生成的应用和已有能力统一出现在应用中心，并能在窗口工作区中打开。

## 当前能力

- **持久 Agent Run**：Run、step、interaction 和事件流保存在 `workspace/.ambient/runs.db`；任务可以取消、重试，并能在进程重启后重新领取。
- **应用工作区**：支持浮动、最大化、贴靠、缩放、切换和预设布局；Canvas V3 配置由后端持久化。
- **动态 Widget**：Widget 的主产物是导出 React 组件的 `controller.js`。聊天结果使用 `<ambient-widget>` 包装传输，创建或修改应用时则先在 staging 目录生成、校验，再原子发布。
- **统一图数据**：Widget 和 Agent 通过经过 schema 校验的 Graph API 读写 `workspace/graph.db`。
- **模型与工具集成**：Provider、模型默认值和会话模型在界面中配置；Agent 可经 Tool Gateway、MCP 或 OpenCode 调用外部能力。
- **审计与确认**：LLM 请求写入工作区审计日志；有副作用的流程由后端策略、interaction 和持久 effect 记录约束。

## 需要理解的边界

- `SandboxWidget` 是组件名称，不是运行不受信任 JavaScript 的强安全沙箱。Widget controller 仍在页面 JavaScript realm 中执行，只应加载可信工作区代码。
- WebSocket 用于聊天投影、图订阅和 Run 事件更新；当前代码没有用户身份、设备配对或冲突合并协议，因此文档不把它描述为完整的多用户或多设备协同系统。
- 本地模型是否“离线”取决于所配置的 Provider 和工具。使用云模型、MCP 或 Widget 网络请求时仍会产生外部通信。

## 推荐阅读顺序

1. [快速开始](/guide/quick-start.md)：启动服务并配置第一个模型。
2. [项目结构](/architecture/project-structure.md)：了解目录和模块职责。
3. [系统与请求链路](/architecture/overview.md)：理解从输入到 Run、数据和 UI 的完整路径。
4. 根据工作内容继续阅读 Agent、Widget、Graph 或集成章节。
