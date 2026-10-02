# Codex 文件生成故障验收（2026-10-02）

## 故障与修复

天气 Widget 在 `stage_code` 报缺少 `controller.js`。保留草稿为空，生成器输出明确指出
`codex-code-mode-host` 不存在，导致文件写入工具两轮均无法启动。托管安装只有
`codex-cli 0.159.3`，缺少同版 companion；增加代码重试无法修复这个环境故障。

独立审查另发现固定 `codex-acp@2.1.1` 与旧审批解析不兼容：该版本的普通权限请求不提供
旧 `codex.params`，标准更新 diff 只包含局部 hunk。修复通过精确版本及能力协商使用
`diffPatch`，核对关联工具、完整路径、旧文件版本及 patch 行坐标，保留白名单与单次审批。

安装器补齐并严格校验 companion，已有 CLI 的部分安装可以重试。启动前对缺失组件返回
`coding_agent_code_mode_unavailable`；环境故障要求先修复安装。生成失败活动正确结束，
失败活动先于诊断回复提交；重新生成清除旧修复指令，仍保留跨 Run 的校验历史。

## 真实运行验收

在实际 Debian 12 aarch64 Docker 后端，通过正常安装 API 完成部分安装修复：
`available=false, update_available=true` 变为 `available=true, update_available=false`，
版本仍为 `0.159.3`。认证保持 CLI 管理，没有读取或复制认证内容。

使用实际 `gpt-6-luna`、生产 ACP 适配器及独立容器临时 staging 验证：

| 操作 | 结果 |
| --- | --- |
| 创建静态测试 Widget | 实际生成 `controller.js` 与 Manifest V2；自动修复 0 次 |
| 局部替换 controller 文案 | 实际文件更新；Manifest 字节保持相同；自动修复 0 次 |
| Widget Runtime 验收 | 生产运行协议收到 640×480 首帧 |
| 发布边界 | 两次生成均使用 `promote=false`，未发布测试 App |

这是文件工具及渲染验收，不代表天气应用已完成。原失败草稿和审批保留：天气方案没有
获批数据能力，实时天气来源仍需纳入方案审批，不能用测试数据或扩大 Manifest 替代。

## 回归边界

回归覆盖首次与部分安装、坏包与尺寸/链接/下载限制、失败安装重试、真实启动前错误、
新旧 ACP 格式、创建和局部更新、CRLF 与无终止换行、多个文件、越界/移动/过期证据、
单次消费、连续空产物停止、失败草稿保留、原子事件提交和中英文诊断。

固定 bridge 的 patch 证据上限为 1MiB，不能仅凭路径审批缺失的更新证据。
同步启动检查验证 companion 文件、尺寸与执行权限；功能性 `--help` 检查在安装和状态
流程执行。本次实际验收覆盖缺失组件修复，不宣称任意同尺寸损坏都能在同步检查中发现。
