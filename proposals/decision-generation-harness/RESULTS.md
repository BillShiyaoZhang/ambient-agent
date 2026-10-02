# 决策与生成拆分：验证结果

日期：2026-10-02。实现契约见 [DESIGN.md](DESIGN.md)。仅发送仓库合成案例，不创建 Run、不执行 Graph/App 副作用；固定 `jev-1.13.0`，概率门槛 0.95、差值门槛 0.15。公开文件不包含 API key。

## 路由上下文

使用相同的 48 个 model-origin 案例，其中 40 个明确标签、8 个保留政策歧义。v1 为保存的同日基线，v2 为本次运行，两轮默认请求超时均为 1.5 秒。

| 指标 | v1 | v2 |
| --- | ---: | ---: |
| 请求数 | 48 | 48 |
| 合法返回 | 46 | 45 |
| 超时 / 返回校验失败 | 2 / 0 | 2 / 1 |
| 已知输入 token（含已收费非法返回） | 158,817 | 61,204 |
| 已知费用估算 / USD | 0.006670314 | 0.002570568 |
| 明确标签达到 kind 门槛 | 31 / 40 | 31 / 40 |
| 达到门槛的 kind 匹配标签 | 31 / 31 | 31 / 31 |
| 请求 p50 / ms | 782.30 | 717.91 |
| 请求经验 p95 / ms | 1048.76 | 1122.59 |

已知输入总量减少 **61.46%**。两轮都有已知费用的 44 个相同案例，输入 151,904 → 58,532，配对减少 **61.47%**。这支持投影节省输入的结论；延迟尾部没有改善，不能声称整体更快。网络超时的账单未知，已知费用不是完整账单。经验 p95 为排序索引 `floor(0.95*(n-1))`。

v2 不重复 capability catalog 和完整 manifest，保留路由相关候选、历史/摘要与 Graph 类型计数。完整输入过限回退，候选不截断为 top-k。`seed-039` 单次非法返回的重试合法；未放宽 Choice 校验。这里只测量 kind，未证明 App 目标、自由参数、生成费用、最终成功率或生产正确率。

安全原始预测：[v2](routing-v2-predictions-2026-10-02.jsonl)、[v1](../jev-intent-router/live-predictions-2026-10-02.jsonl)。

## 混合决策

20 个新合成场景运行四个真实 helper，涵盖中英文、条件查询、聚合、遗漏目标、错序、额外写入、Schema 描述污染、歧义与无 Graph 请求。脚本显式清空用途覆盖并开启实验 cascade。

最终 **19 / 20 合法返回**、1 次超时；19 次已知输入 **23,711 token**，已知费用估算 **$0.000995862**，超时费用未知。

| 用途 | 场景 | 合法返回 | 捷径采用 | 结果 |
| --- | ---: | ---: | ---: | --- |
| Graph 查询模板 | 6 | 6 | 0 | 3 个负例拒绝；简单正例也未满足所有 Noul 门槛 |
| 复合计划复核 | 6 | 6 | 0 | 4 个负例拒绝；2 个正例仍需原 refiner |
| 开发计划证据 | 3 | 3 | 不适用 | 始终 advisory，部分相关性/范围问题仍不确定 |
| Schema 选择 | 5 | 4 | 0 | 歧义负例拒绝；正例仍完整生成，1 次超时 |

这些结果验证接口和回退，**不支持大范围用这些阶段节省生成调用**。回退后的真实任务完成率尚未测量；mock 工作流测试只验证控制边界。保留门槛，新增 `stage_modes`，可仅开启 `intent_parameters`，其余用途保持 shadow/off。shadow 会增加费用和延迟，应分别统计。

额外比较了 6 个 Schema/计划简短问题及投影变体：输入 7,895 → 5,248（减少 33.53%），Schema 接受率仍为 0 / 4，计划证据仍有不确定判断。**没有将该实验措辞作为质量优化落入生产**。查询原子化小试同样未让简单正例越过门槛。记录见 [compact experiment](compact-rubric-experiment-2026-10-02.jsonl)。

最终记录：[harness predictions](harness-predictions-2026-10-02.jsonl)；修复前记录：[before rounding fix](harness-before-rounding-fix-2026-10-02.jsonl)。

## Score 数值兼容

首轮 20 场景中，4 个混合返回被严格均值等式拒绝。额外 4 次诊断确认实际 Score 和概率均为两位小数：`p={0:0.19,1:0.40,2:0.41}` 的公开均值为 1.22，供应商返回 1.23；另一次公开均值 1.99、返回 1.98。官方说明 Score 是概率加权位置，没有给出序列化精度保证：[Score](https://docs.typesafe.ai/primitives/score)。

保留类型、有限数值、范围、完整概率键、总和 1、legend 校验。精确均值直接接受；非精确返回只对两位小数网格允许有限舍入兼容：潜在真概率在公布值 ±0.005 内，约束真概率总和 1，计算最小/最大加权位置，要求 Score ±0.005 与其相交。其他精度偏差、超出区间仍拒绝。

这是基于现场数值的**工程推断**，并非供应商精度承诺。保留原始 Score 和分布，不重算成另一个评分，不降低 Choice/Noul 门槛。Score 不批准执行权限或硬校验。

## 回归验证

本地最终后端：**1,648 passed、16 skipped**；前端 **358 passed**，lint 与 production build 通过；Widget Runtime **36 passed**。Ruff、格式、35 对中英文文档、UML 契约和生成 Run event 类型检查通过。真实 Neo4j 检查由 GitHub Actions 专用步骤执行，本地相关项未配置而跳过。

新增覆盖 HTTP/畸形/大响应/解析超时/取消、完整候选与输入限额、共享模型/token/费用/时间预算、已收费但预算耗尽的审计、生成字段类型/kind/目标/hash 漂移、新 App ID 冲突、完整复合目标与顺序、Schema 精确选集与修复回退、计划反馈/缓存失效、旧 Run 恢复、真实 durable 审批及 effect 边界。

另修复已有 Schema fallback 的 clean 误判：完整 findings 校验后一次构造 diff；任何 unknown property/type/mismatch 都不能提升为已验证产物，畸形返回不能当作空 findings。

## 重现

从仓库根目录运行。命令调用付费 API，认证只读本地 `.env` 或进程环境；输出排他创建、权限 0600。

```sh
.venv/bin/python -B -m scripts.evaluate_jev_router --limit 48 --context-version routing-context-v2 --output /private/tmp/jev-routing-new.jsonl
.venv/bin/python -B -m scripts.evaluate_harness_decisions --limit 20 --output /private/tmp/jev-harness-new.jsonl
```

下一次语义验收需人工复核的真实留出集，分别报告采用率、采用正确率、回退率、最终完成率、全流程费用与 p95；合成样本不能代替生产 QoS 结论。
