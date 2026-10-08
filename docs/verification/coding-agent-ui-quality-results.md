# 图形界面质量评估结果

## 配对结果

最终配对报告通过 2/2 个案例。天气面板通过 22 项浏览器检查；修复后的运维跟进面板通过 23 项检查。两者都在生产 Widget frame 和 SDK 中以 Chromium 154.0.8037.97、Playwright Core 1.62.0 验证，并在 320、640 CSS 像素宽度及浅色、深色主题下留存首帧截图。

配对报告为 [coding-agent-ui-quality-paired.json](coding-agent-ui-quality-paired.json)，选取证据为 [coding-agent-ui-quality-selected-generation-evidence.json](coding-agent-ui-quality-selected-generation-evidence.json)。它逐案例链接不可变的生产生成报告、保留应用哈希和浏览器报告；汇总选择了初始天气应用与经实测修复的跟进事项应用。

天气图使用由 fixture 驱动的 CSS 几何温度区间柱；改动 fixture 的某日最高温后，图形高度和可见数值都变化。初始界面显示三条逐日详情，键盘激活披露控件后能查看全部七天数据和服务状态。历史检查 ID `svg-data-chart` 现接受 SVG 绘图几何或等效的 CSS 绘图几何，并不要求模型必须输出 SVG。

原始跟进事项应用的浏览器报告记录了一个真实问题：模拟私有文件写入失败后，Retry 重新读取文件并丢弃了尚未保存的事项。该初始应用和失败报告均保持原样。一次针对性模型修复根据这一观察要求保留编辑内容并重试待处理写入；修复版通过全部 CRUD、键盘、存储读取失败恢复、写入失败恢复和响应式检查。修复提示明确要求在写入失败后保留未保存输入；等效的规则随后才加入共享指南，因此这次修复是由案例级反馈触发。

初始生成阶段为两个保留应用进行了 3 次生产生成 dispatch：天气和 CRUD 各保留一份；另一次重复天气生成已执行并产生 staging 输出，但在保留目标碰撞时被丢弃。之后为 CRUD 修复执行了 1 次针对性生产模型调用，总计 4 次实际 dispatch。新加入的生成器前置碰撞保护避免后续重复付费调用。本次没有对生成的 controller 手工修补，也没有访问实时天气或其他外部网络。

初始生成与修复调用都使用共享指南 SHA-256 `2002d5ee19cfb9710ed599207d8139312e6e38247f52cc1ddda77ca483a1f99d`。持久化失败后保留待处理写入的通用规则后来加入指南；当前部署版本 SHA-256 为 `f1a9d811e4c1a3eb3724da2d0e68f75bf1def1039b2bd5f817a3f482ca98f2cc`。

## 截图

以下首帧及交互截图对应最终配对包。截图用于人工评审；自动检查只说明这两个案例满足对应的行为和渲染条件。

| 案例 | 浅色 320 px | 浅色 640 px | 深色 320 px | 深色 640 px | 交互态 |
|---|---|---|---|---|---|
| 天气面板 | [截图](coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard/screenshots/forecast-operations-dashboard-320-light.png) | [截图](coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard/screenshots/forecast-operations-dashboard-640-light.png) | [截图](coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard/screenshots/forecast-operations-dashboard-320-dark.png) | [截图](coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard/screenshots/forecast-operations-dashboard-640-dark.png) | [七日详情展开](coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard/screenshots/forecast-operations-dashboard-640-light-expanded.png) |
| 修复后的跟进事项 | [截图](coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard/screenshots/compact-follow-up-dashboard-320-light.png) | [截图](coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard/screenshots/compact-follow-up-dashboard-640-light.png) | [截图](coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard/screenshots/compact-follow-up-dashboard-320-dark.png) | [截图](coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard/screenshots/compact-follow-up-dashboard-640-dark.png) | [已填事项](coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard/screenshots/compact-follow-up-dashboard-640-light-populated.png)，[写入错误](coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard/screenshots/compact-follow-up-dashboard-640-light-write-error.png) |

最初天气和 CRUD 应用的浏览器验证报告分别为 [forecast-operations-dashboard](coding-agent-ui-quality-artifacts/forecast-operations-dashboard/browser-report.json) 和 [compact-follow-up-dashboard](coding-agent-ui-quality-artifacts/compact-follow-up-dashboard/browser-report.json)。CRUD 修复后的报告为 [repaired-after-write-retry](coding-agent-ui-quality-artifacts/compact-follow-up-dashboard/repaired-after-write-retry/browser-report.json)；对应应用均保存在报告旁的 `app/` 目录。早先由探针标题误命中删除按钮造成的过期报告则保存在 `superseded-harness-race/`，不参与最终配对结果。既有天气 controller 仍只作为文本源码基线，未更改也未声称有历史截图。
