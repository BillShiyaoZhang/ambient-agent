import { withMermaid } from 'vitepress-mermaid'

export default withMermaid({
  title: "Ambient Agent",
  description: "An open-source, self-hostable personal AI assistant framework with Widget GUI workspace.",
  base: process.env.GITHUB_ACTIONS ? '/ambient-agent/' : '/', // Auto-fallback for GitHub Actions deployment
  themeConfig: {
    nav: [
      { text: '开始使用', link: '/guide/introduction' },
      { text: '核心架构', link: '/architecture/overview' },
      { text: '卡片开发', link: '/widgets/guide' },
      { text: '智能体引擎', link: '/agent/intent-router' },
      { text: '外部集成', link: '/integrations/mcp' }
    ],
    sidebar: {
      '/guide/': [
        {
          text: '开始使用',
          items: [
            { text: '项目介绍', link: '/guide/introduction' },
            { text: '快速开始', link: '/guide/quick-start' }
          ]
        }
      ],
      '/architecture/': [
        {
          text: '核心架构',
          items: [
            { text: '系统概述', link: '/architecture/overview' },
            { text: '类图设计', link: '/architecture/uml' },
            { text: '图数据库 (GraphDB)', link: '/architecture/graph-db' }
          ]
        }
      ],
      '/widgets/': [
        {
          text: '动态卡片开发 (Widget)',
          items: [
            { text: 'XML 协议说明', link: '/widgets/guide' },
            { text: '沙箱隔离机制', link: '/widgets/sandbox' },
            { text: 'ambient SDK 参考', link: '/widgets/sdk' }
          ]
        }
      ],
      '/agent/': [
        {
          text: '智能体引擎',
          items: [
            { text: '意图路由 (IntentRouter)', link: '/agent/intent-router' },
            { text: 'DAG 任务流水线', link: '/agent/dag-pipeline' },
            { text: 'Agent 调度中心', link: '/agent/harness' }
          ]
        }
      ],
      '/integrations/': [
        {
          text: '集成与安全',
          items: [
            { text: 'MCP 工具集成', link: '/integrations/mcp' },
            { text: '权限与敏感审计', link: '/integrations/permissions' }
          ]
        }
      ]
    },
    search: {
      provider: 'local',
      options: {
        translations: {
          button: {
            buttonText: '搜索文档',
            buttonAriaLabel: '搜索文档'
          },
          modal: {
            noResultsText: '无法找到相关结果',
            resetButtonTitle: '清除查询条件',
            footer: {
              selectText: '选择',
              navigateText: '切换',
              closeText: '关闭'
            }
          }
        }
      }
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/BillShiyaoZhang/ambient-agent' }
    ]
  }
})
