# Agent Harness 评测

`backend.agent.evaluation` 提供离线、无网络依赖的 Agent 轨迹评测基元。评测场景同时提供：

- outcome scorer：判断最终结果是否满足任务；
- trajectory scorer：判断工具选择、副作用与恢复路径是否符合预期；
- runner：返回可序列化的 `EvaluationTrace`。确定性场景通常由 `ScriptedTape` 提供 runner。

报告聚合 `success_rate`、`unsafe_action_rate`、`tool_calls`、`tokens`、`cost_usd`、`latency_ms` 与 `recovery_rate`，并保留每次运行的双评分。一次运行只有在 outcome 和 trajectory 都达到各自阈值、没有未处理异常且没有 unsafe action 时才计为成功。

`EvaluationHarness.evaluate(..., enforce_ci_gate=True)` 会在任一确定性运行失败时抛出 `EvaluationGateError`，可直接作为 CI 门禁。`real_model` 场景要求 `repetitions >= 3`；测试和离线 CI 应使用 fake runner，不应隐式访问网络或调用真实模型。

`RunStoreTraceAdapter` 是生产轨迹入口：它按 `run_id` 合并 Run 终态、step attempts、canonical events 与 workspace LLM audit，优先使用逐次 audit 统计 token/cost，并用 checkpoint budget 补齐缺失记录。unsafe 不再只能由 tape 手工标注；unknown effect、明确 policy violation、unsafe event 或未获批的 effectful tool call 会从持久数据中自动推导。CI 中的 scripted Converse/Widget 场景应通过真实 `RunCoordinator + DurableAgentWorkflow` 运行后再交给该 adapter 评分。
