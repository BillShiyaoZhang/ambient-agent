# Agent Collaboration 云入口建议包

此目录是旧首版建议的历史快照。当前 Ambient 接入码适配使用 [2026-10-01 云交接分支](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/codex/ambient-workspace-review-20261001)，固定 Deploy `d6dd823f1b379440616a2dc2e866ce9d9bac7729`、Web `fa55096cc66f88f17f1b6191a5bc41d046cd08e8`。下面的旧分支及 bundle 仅供审阅首版历史；当前客户端与云端联调见 Ambient `docs/guide/remote-workspace.md`。

此目录保存独立建议分支的改动，供原项目侧审阅、决定是否合并。Ambient 不使用 submodule，也不在运行时依赖此源代码。云端建议分支已发布到 GitHub，尚未合入云项目 `main`，也未部署；原 `agent-collaboration-deploy` 工作目录未修改。

## 改动内容

- Web 子项目：复用账户登录，增加本地工作区领取、在线状态、打开与撤销；服务端从会话派生账户，账户删除先撤销 Gateway。
- Deploy 项目：新增独立、有界 HTTP/WebSocket Workspace Gateway、可选 Compose overlay、运维说明和测试。
- Platform 和 SDK：保持原固定版本。原聊天 RPC 与新工作区通道的政策边界分别说明。

基础 Deploy commit：`e607a3ea72657b7d0eb861a04e4a09f1421f7a8b`。
基础 Web commit：`21693d1556111a94de7c09abd614d91398f82c03`。
建议分支均为 `codex/ambient-workspace-proposal`。[metadata.json](metadata.json) 记录基础与建议 commit，[verification.json](verification.json) 记录测试和恢复验证，[SHA256SUMS](SHA256SUMS) 用于核对导出文件。

已发布的建议分支：[Deploy](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/codex/ambient-workspace-proposal)、[Web](https://github.com/BillShiyaoZhang/agent-collaboration-web/tree/codex/ambient-workspace-proposal)。元数据和验证记录保留导出时的历史状态，其中 `pushed: false` 表示生成该快照时尚未推送；导出工件的 commit 与校验和保持不变。

随包 `.gitattributes` 保留导出文件的原始字节，避免平台换行转换改变校验和。patch 作为导出工件保存，可直接打开审阅或使用 Git 应用。

## 获取已发布建议分支

在新的工作目录中克隆建议分支及其固定子项目版本，即可审阅或按 Gateway/Web 文档本地启动：

```powershell
git clone --branch codex/ambient-workspace-proposal --recurse-submodules https://github.com/BillShiyaoZhang/agent-collaboration-deploy.git cloud-proposal-review
```

## 审阅与快照恢复

`deploy.patch` 与 `web.patch` 用于代码审阅或择取实现。`deploy.bundle` 与 `web.bundle` 保留准确 commit，便于重现 Deploy 中的新 Web gitlink；bundle 只包含相对上述基础版本的新提交，原基础对象需要先取得。

两份 bundle 已从仅含上述基础版本的隔离 Git 检出完成离线恢复验证，父项目 Web gitlink 与恢复后的 Web HEAD 一致，Platform/SDK 固定版本未变；云端建议源检出和恢复检出均无待提交变更。

以下是从原基线恢复导出快照的 bundle 路径。在新的临时检出中操作：先 clone 原 Deploy 并初始化原 submodules，切到上述基础 commit；然后在其 `agent-collaboration-web` 内 fetch 本包 `web.bundle` 的 `refs/heads/codex/ambient-workspace-proposal`，创建并切换建议分支；再在 Deploy 根 fetch `deploy.bundle` 的同名分支并切换。该路径先取得子项目对象，再切换父项目引用，可复核导出时的精确 commit。日常获取已发布建议分支可使用上面的直接克隆命令。

```powershell
# Review in a new checkout. Replace <package> with this directory's absolute path.
git clone --recurse-submodules https://github.com/BillShiyaoZhang/agent-collaboration-deploy.git cloud-proposal-review
git -C cloud-proposal-review checkout e607a3ea72657b7d0eb861a04e4a09f1421f7a8b
git -C cloud-proposal-review submodule update --init --recursive
git -C cloud-proposal-review/agent-collaboration-web fetch '<package>/web.bundle' refs/heads/codex/ambient-workspace-proposal
git -C cloud-proposal-review/agent-collaboration-web switch -c codex/ambient-workspace-proposal FETCH_HEAD
git -C cloud-proposal-review fetch '<package>/deploy.bundle' refs/heads/codex/ambient-workspace-proposal
git -C cloud-proposal-review switch -c codex/ambient-workspace-proposal FETCH_HEAD
git -C cloud-proposal-review submodule status --recursive
```

若只采用 patch，可在相应基础版本检出中用 `git apply --index` 应用，审阅后自行提交。新提交会产生不同的 commit ID，需要同步更新父项目 Web gitlink。没有自动修改原项目的应用脚本。

## 本地验证与采用边界

Ambient 侧说明与截图见仓库内 `docs/guide/remote-workspace.md`、`docs/verification/remote-workspace.md`。Gateway 的设计、启动、配额、测试和运维采用要求位于恢复后的 `workspace-gateway/README.md` 与 `docs/architecture/WORKSPACE_GATEWAY_PROPOSAL.md`。Web 建议文档随其 patch 提供。

包中不含数据库、设备凭据、服务密钥、联调账号或 node_modules。生产采用还需单独配置 wildcard TLS、入口日志脱敏、内部服务认证隔离、服务政策、全局配额与数据保留。当前只支持单 Gateway 实例；本次目标是完成本地交互验收，没有线上发布。
