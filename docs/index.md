---
layout: home

hero:
  name: "Ambient Agent"
  text: "开源、自托管的个人 AI 助理框架"
  tagline: "优先使用富图形交互界面 (Widget GUI) 展示 AI 输出，并提供极致的数据隐私保护"
  actions:
    - theme: brand
      text: 开始阅读文档
      link: /guide/introduction
    - theme: alt
      text: GitHub 仓库
      link: https://github.com/BillShiyaoZhang/ambient-agent

features:
  - title: 🎨 动态 GUI 工作区 (Canvas)
    details: AI 能够根据指令，在 Canvas 工作区上动态生成并渲染全新的交互式 Mini App 卡片，支持拖拽、固定和关闭。
  - title: 🔒 安全隔离沙箱 (Sandbox)
    details: 运行环境进行样式与脚本双重隔离，提供局部 CSS 注入及安全闭包执行 JS，保护全局变量不受污染。
  - title: 👁️ 传输审计与隐私透明
    details: 支持 Ollama 本地离线模型，且配备可视化 Audit Log 面板，明细展示每一次发送与接收的 Payload。
  - title: 🔄 实时多端同步
    details: 基于单服务多客户端的 FastAPI WebSocket 长连接设计，所有设备均可秒级实时同步操作空间和对话。
---
