# Coding Agent 系统检查与应用开发测试

本次检查发现了多处会削弱 Coding Agent 效果的集成问题，已修复并更新到本地 Backend 和 Widget Runtime。实际生成和修复均通过生产 `backend.coding_agent.run_coding_agent` ACP 链路，明确选择原生 Codex `gpt-6-luna`。

## 问题与修复

| 问题 | 对开发效果的影响 | 修复 |
| --- | --- | --- |
| Router 改写需求后，后续开发主要依赖摘要 | 用户原话中的细节和约束可能丢失 | 单 App 的计划、Schema 和编码阶段保留完整原始需求；多 App 与 slash 步骤保持各自范围 |
| 计划提示要求极短、只覆盖用途和 UI | 持久化、交互、错误恢复和验收条件容易遗漏 | 计划覆盖适用的行为、数据来源、持久化、状态和可观察验收条件 |
| 修复提示遗漏原始需求和 required-feature 条件 | 模型修复当前错误时可能遗忘功能目标 | 修复上下文重新携带原始需求、批准计划和功能验收条件，并保留原有权限边界 |
| Schema 的模型兜底只读取前 8,000 字符 | 后半段代码中的问题可能被漏检 | 检查完整 controller；超过 2 MiB 的输入直接拒绝，避免用局部代码得出无问题结论 |
| Node 26 warning 混入 verifier 输出 | 具体错误被掩盖为通用校验失败，降低修复反馈质量 | 从有界 stdout/stderr 中提取结构化诊断，保留错误代码与提示 |
| 组件说明只列名称，缺少 `List` 的接口语义 | 模型把可编辑行放进忽略 children 的 `List`，文件写入成功但记录不可见 | 明确 `List`/`Table` 的文本渲染接口，交互行使用 `Column` 中的 `Row` |
| 首帧验收 Runtime 缺少原生 SDK 的 storage/lifecycle 接口 | 合法的草稿存储代码被误判为代码错误 | 仅在宿主启用的验收会话中提供有界临时存储与生命周期注册接口 |
| 文件不存在没有独立错误代码 | 首次使用容易被当成权限拒绝或普通加载失败 | 授权后返回 `file_not_found`；越权访问仍拒绝，其他读取错误继续显示可重试状态 |
| 初始状态写回可能早于异步存储读取 | 已保存草稿可能被空默认值覆盖 | 提示与 SDK 文档明确要求初始读取完成后再允许写回 |
| timeout 只检查大于零 | NaN/Infinity 配置可能逃过校验 | ACP 启动与兼容入口要求有限正数 |

检查确认了模型选择从 `native_model` 传递到 Codex 启动配置的路径。Coding ACP 与主 Agent 的原生推理调用使用不同执行链路。上述修复保留 staging、文件范围、Manifest、Schema 与能力授权约束。

## 真实应用结果（2026-10-03）

先实际生成三个案例，再针对读书清单执行两次带失败反馈的模型修复，共五次生产 ACP 模型调用。所有产物使用隔离工作区和 `promote=False`。生成的应用代码没有手工修改。

| 案例 | 最终首帧 | 浏览器检查 | 覆盖行为 |
| --- | --- | --- | --- |
| 私有读书清单 | 通过，640×480 | 14 项通过 | 首次空状态、增删改查、已读/未读、文件内容、重新加载、读取重试和写入错误 |
| Graph 任务看板 | 通过，640×480 | 14 项通过 | Task 订阅、增删改、完成状态、筛选、重新加载、草稿恢复和 Graph 错误 |
| 服务状态页 | 通过，640×480 | 10 项通过 | 已批准请求、加载、成功、空结果、失败重试和畸形响应拒绝 |

最终汇总为 **3/3 完整通过**。汇总器要求生产 staging 已验证，且生成记录、首帧记录、浏览器记录的产物 SHA-256 一致。哈希覆盖 controller、Manifest、可选 README 和 data 文件，排除测试截图与报告。修改代码后不能沿用旧的通过结果。

读书清单首次生成确实存在 UI 缺陷：点击添加触发 `files.write`，记录却因 `List` 忽略 children 而不可见。第一次模型修复改正了行布局，但测试工作区漏复制 verifier 脚本，产生宿主侧 `widget_verifier_unavailable`。该产物被保留，并单独通过静态与功能声明校验；浏览器随后确认它把首次文件不存在当成加载失败。补齐文件错误契约后，第二次模型修复通过全部检查。报告中的 `repair_attempts: 0` 表示该次 ACP 调用内部没有自动修复，不代表没有上述两次后续模型调用。

任务看板最初按 SDK 使用草稿存储，却被旧首帧 Runtime 拒绝。更新验收 Runtime 后，同一份生成代码通过首帧与浏览器测试。验收存储仅在单次检查的内存中存在，最多 256 个键和 1 MiB JSON 值；重新加载与持久化行为另由浏览器状态 fixture 验证。

## 证据与复测

- [最终汇总与产物哈希](coding-agent-app-evaluation-results-final.json)、[最终首帧证据](coding-agent-app-evaluation-smoke-final.json)。
- [读书清单源码](coding-agent-app-evaluation-final-artifacts/private-reading-list/controller.js)、[任务看板源码](coding-agent-app-evaluation-final-artifacts/project-task-board/controller.js)、[服务状态页源码](coding-agent-app-evaluation-final-artifacts/service-status/controller.js)。
- [读书清单交互证据](coding-agent-app-evaluation-final-interactions/private-reading-list/interaction-result.json)、[任务看板交互证据](coding-agent-app-evaluation-final-interactions/project-task-board/interaction-result.json)、[服务状态页交互证据](coding-agent-app-evaluation-final-interactions/service-status/interaction-result.json)。
- [初次三案例结果](coding-agent-app-evaluation-results-initial.json)、[首次失败的读书清单源码](coding-agent-app-evaluation-artifacts/private-reading-list/controller.js)、[修复尝试一](coding-agent-app-evaluation-repair-attempt-1.json)、[修复尝试二](coding-agent-app-evaluation-repair-attempt-2.json)。

全量 Backend 回归 **2,015 项通过、16 项跳过**，有一条已有的 Starlette 弃用 warning。最终验收脚本与提示的定向测试另有 29 项通过。Widget Runtime 完整测试 **52/52 通过**，使用与 Compose 一致的 `--init`、资源限制、无网络配置和 Chromium 沙箱。测试容器遗漏 `--init` 时出现的累计 Chromium 多会话故障也已定位并修正测试启动方式。Ruff、文档链接、中英文配对、事件类型和 UML 合约检查通过。

列出案例不会调用模型：

```sh
python scripts/evaluate_coding_agent_apps.py --list
```

在已配置 Codex 登录和 ACP bridge 的 Backend 环境中运行 `--execute` 会实际调用模型。浏览器验证入口是 `scripts/verify_coding_agent_apps.mjs`，需可用的 Chromium 和 Widget Runtime 依赖。已有证据可以直接复核：

```sh
python scripts/merge_coding_agent_eval_results.py --batch docs/verification/coding-agent-app-evaluation-batch-final.json --artifact-root docs/verification/coding-agent-app-evaluation-final-artifacts --interaction-root docs/verification/coding-agent-app-evaluation-final-interactions --smoke docs/verification/coding-agent-app-evaluation-smoke-final.json --output /tmp/coding-agent-evaluation-confirmed.json
```

## 结论范围

浏览器使用生产原生 frame 和 SDK，文件、Graph、网络由确定性宿主 fixture 提供。网络源使用 `.invalid` 地址，不访问真实上游；这些结果不证明外部服务可用性。首帧检查也不证明持久化或暂停前刷盘。此次只有一个三案例批次与两次针对性修复，没有直接 Codex 对照组，不能据此宣称统计意义上的性能相当或优越性。
