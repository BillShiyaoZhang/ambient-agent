# Coding Agent 图形界面质量评估

## 目的

本评估针对原生 `gpt-6-luna` Coding Agent 生成图形应用的具体案例，补充现有的通用生成评估。既有天气应用源码保持不变；基线问题是预报信息逐行堆叠，温度趋势用 Unicode 字符拼成。它是源码基线，没有保留对应截图，也不是本次生成的评估产物。

既有天气控制器为 `workspace/apps/weather-app-8f3a/controller.js`，SHA-256 为 `ce29615b1442eed8aee40328204124cbfdc9489d13e5a3e3edd80142862ee905`。它请求 11 项当前天气字段、24 小时数据和 7 天数据，并把 `makeTrend` 的结果渲染成 Unicode 文本。

本次使用两类不同界面需求：

1. Harbor Station 七天天气和服务状态页，展示数据驱动的温度趋势图，提供渐进式完整预报。
2. 私有运维跟进项看板，支持增删改查，保持紧凑，无须图表或额外说明文案。

## 执行与数据边界

- 使用生产 `backend.coding_agent.run_coding_agent`，原生模型 `gpt-6-luna`，`promote=False`、隔离临时工作区和正常的 staging 校验。
- 案例使用确定、显式批准的 fixture。网络 source 使用 `.invalid` 地址，由浏览器宿主 fixture 返回数据；浏览器禁止对外联网。
- 保留生成的 controller 和 manifest 原样。仅当浏览器记录到可观察失败时，才可让模型修复；保留初次产物，并让修复报告关联到失败检查。
- 使用生产 Widget frame、SDK 和带标准沙箱的 Chromium。分别在 320、640 CSS 像素宽度和浅色、深色主题截图。
- 浏览器宿主的 `color-scheme` 和背景色与每次 frame 的有效主题保持一致；这对透明 frame 尤其重要，也适用于交互后的截图。
- 报告记录 case-set SHA-256、应用 SHA-256、浏览器及运行时源码版本、视口、主题、截图位置和检查结果。截图不进入应用源码哈希。

## 可观察验收条件

每个案例都需要通过生产 staging 校验、生产 frame 启动和静态功能覆盖。浏览器报告必须引用相同的应用哈希，并为两种视口和两种主题分别生成并验证截图。它还要检查 320 和 640 像素下无水平溢出、可见控件有可访问名称、frame 没有运行时错误，以及错误界面不会泄漏原始 provider 堆栈。

天气页必须展示 fixture 的地点和当前天气，以非空的 SVG 或 CSS 几何图形呈现数据驱动的温度曲线；初始视图只展开部分逐日详情，用户可以通过可访问控件展开全部七天。修改 fixture 温度后，图形几何和对应的可见数值都必须改变。宿主 RPC 只能请求已批准的 source、path 和 method。浅色、深色页面都不能产生浏览器或运行时错误。

跟进事项页从简洁空状态和已标记的输入控件开始；浏览器检查会通过私有文件存储实际创建、读取、改名、完成和删除记录。每次成功写入后，对应记录要在界面更新。测试会注入文件读取和写入失败，错误提示须简洁，失败写入不能显示为已保存；点击重试后必须成功保存待处理记录。320 像素下应保持可用且不出现横向滚动，不要求图表或长篇说明。

## 报告与解释范围

生成报告、浏览器报告和配对报告分别保留。应用文件保存在 `case-id/app`；浏览器报告和截图放在同级目录之外，避免污染应用哈希。配对报告会重新计算保留应用哈希，并将每个运行时源码哈希与当前仓库文件核对；案例、case-set 哈希、必要检查、截图或视口／主题组合缺失或不符时会拒绝通过。它也会把生产 staging、静态功能覆盖和生产首帧结果绑定到同一应用。初次失败和模型修复版本使用各自不可覆盖的目录。

截图供人工评审；自动检查只适用于这两个案例，不能代表普遍的视觉质量评分，也不代替审阅。

## 复现

以下命令在已保留的最终证据包上重跑浏览器检查和配对校验，不触发模型调用。源码和运行时文件须来自当前仓库工作区：

```sh
node scripts/verify_coding_agent_ui_quality.mjs --case forecast-operations-dashboard \
  --cases docs/verification/coding-agent-ui-quality-generated-cases.json \
  --case-root docs/verification/coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard
node scripts/verify_coding_agent_ui_quality.mjs --case compact-follow-up-dashboard \
  --cases docs/verification/coding-agent-ui-quality-generated-cases.json \
  --case-root docs/verification/coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard
UV_CACHE_DIR=/tmp/ambient-ui-uv-cache uv run --offline python scripts/merge_coding_agent_ui_quality_results.py \
  --generation docs/verification/coding-agent-ui-quality-selected-generation-evidence.json \
  --browser-root docs/verification/coding-agent-ui-quality-final-artifacts \
  --output docs/verification/coding-agent-ui-quality-paired.json
```

如需新跑生产模型，先使用 `--list` 查看案例，再为生成目录和输出 JSON 指定全新的 run ID；runner 会在 dispatch 前拒绝任何已保留的目标，避免意外重复调用。每次把示例中的 `UNIQUE_ID` 替换为新的字母数字标识：

```sh
UV_CACHE_DIR=/tmp/ambient-ui-uv-cache uv run --offline python scripts/evaluate_coding_agent_ui_quality.py --execute \
  --artifact-dir docs/verification/coding-agent-ui-quality-artifacts/run-UNIQUE_ID \
  --output docs/verification/coding-agent-ui-quality-generation-run-UNIQUE_ID.json
```

生成器调用生产 `run_coding_agent`，不会发布产物，并保留静态功能检查和生产首帧报告。最终证据目录中的每个浏览器报告为 `browser-report.json`，旁边保留 4 张规定视口与主题的首帧截图，以及适用案例的展开或交互截图。浏览器报告同时记录 Chromium、Playwright 和 frame/SDK 源码哈希。原有通用评估案例和天气应用源码保持不变。实际结果和生成／修复调用来源见[结果报告](coding-agent-ui-quality-results.md)。
