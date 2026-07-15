import { withMermaid } from 'vitepress-mermaid'

export default withMermaid({
  title: "Ambient Agent",
  description: "An open-source, self-hostable personal AI assistant framework with Widget GUI workspace.",
  base: process.env.GITHUB_ACTIONS ? '/ambient-agent/' : '/', // Auto-fallback for GitHub Actions deployment

  locales: {
    root: {
      label: '简体中文',
      lang: 'zh-CN',
      themeConfig: {
        nav: [
          { text: '文档首页', link: '/guide/introduction' }
        ],
        sidebar: [
          {
            text: '开始使用',
            collapsed: false,
            items: [
              { text: '项目介绍', link: '/guide/introduction' },
              { text: '快速开始', link: '/guide/quick-start' }
            ]
          },
          {
            text: '核心架构',
            collapsed: false,
            items: [
              { text: '系统概述', link: '/architecture/overview' },
              { text: 'Widget 应用架构', link: '/architecture/apps' },
              { text: '类图设计', link: '/architecture/uml' },
              { text: '图数据库', link: '/architecture/graph-db' }
            ]
          },
          {
            text: '动态卡片开发',
            collapsed: false,
            items: [
              { text: '协议说明', link: '/widgets/guide' },
              { text: '沙箱隔离机制', link: '/widgets/sandbox' },
              { text: '接口参考 (SDK)', link: '/widgets/sdk' }
            ]
          },
          {
            text: '智能体引擎',
            collapsed: false,
            items: [
              { text: '意图路由', link: '/agent/intent-router' },
              { text: '任务流水线', link: '/agent/dag-pipeline' },
              { text: '智能体调度中心', link: '/agent/harness' }
            ]
          },
          {
            text: '集成与安全',
            collapsed: false,
            items: [
              { text: '工具集成 (MCP)', link: '/integrations/mcp' },
              { text: '权限与安全审计', link: '/integrations/permissions' }
            ]
          }
        ]
      }
    },
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      themeConfig: {
        nav: [
          { text: 'Docs', link: '/en/guide/introduction' }
        ],
        sidebar: [
          {
            text: 'Getting Started',
            collapsed: false,
            items: [
              { text: 'Introduction', link: '/en/guide/introduction' },
              { text: 'Quick Start', link: '/en/guide/quick-start' }
            ]
          },
          {
            text: 'Core Architecture',
            collapsed: false,
            items: [
              { text: 'System Overview', link: '/en/architecture/overview' },
              { text: 'Widget Apps Architecture', link: '/en/architecture/apps' },
              { text: 'Class Diagrams', link: '/en/architecture/uml' },
              { text: 'Graph Database (GraphDB)', link: '/en/architecture/graph-db' }
            ]
          },
          {
            text: 'Widget Development',
            collapsed: false,
            items: [
              { text: 'XML Protocol', link: '/en/widgets/guide' },
              { text: 'Sandbox Isolation', link: '/en/widgets/sandbox' },
              { text: 'ambient SDK Reference', link: '/en/widgets/sdk' }
            ]
          },
          {
            text: 'Agent Engine',
            collapsed: false,
            items: [
              { text: 'Intent Router (IntentRouter)', link: '/en/agent/intent-router' },
              { text: 'DAG Pipeline', link: '/en/agent/dag-pipeline' },
              { text: 'Agent Harness', link: '/en/agent/harness' }
            ]
          },
          {
            text: 'Integrations & Security',
            collapsed: false,
            items: [
              { text: 'MCP Tools Integration', link: '/en/integrations/mcp' },
              { text: 'Permissions & Audit', link: '/en/integrations/permissions' }
            ]
          }
        ]
      }
    }
  },

  themeConfig: {
    socialLinks: [
      { icon: 'github', link: 'https://github.com/BillShiyaoZhang/ambient-agent' }
    ],
    search: {
      provider: 'local',
      options: {
        locales: {
          root: {
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
        }
      }
    }
  }
})
