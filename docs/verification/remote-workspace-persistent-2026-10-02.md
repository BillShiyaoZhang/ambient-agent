# 持久远程工作区验收记录

## 当前结论

本轮已实现并通过本地回归的“直到撤销”授权，实际本机 Docker 工作区已完成配对、批准和在线连接，授权期限为协议固定值 `9999-01-01T00:00:00Z`，范围仅为 `workspace.control`。本机网页的原生 `gpt-5.6-luna` 模型配置与模型测试已通过；云 Gateway 的能力入口、固定版本发布和私有路由负例已验证。

**本轮持续授权部署、本机五容器保留卷重建和单页真实远程聊天闭环均已通过。** 云建议分支的回放修复通过 105 项 Gateway/ingress 测试，固定 Linux 镜像的 91 项协议测试及生产替换已完成。生产现为 0187361，实时数据库、环境、其它服务与四份 nginx 文件保持。本机五个服务全部 healthy，原有 Hermes 仍运行；持续授权保持 paired、online、仅 `workspace.control` 与精确长期期限。

第二个版本的隔离 Chrome 测试实际从 `after_sequence=0` 完整恢复 4053 条历史事件后，才通过正常 UI 发送消息。chat、runs、run-live 三类 WebSocket 均以正确 Origin/Cookie 返回 101，连续 30009 毫秒未关闭。UI 创建新会话、选择 Luna，模型配置 PUT 返回 200、所属会话 GET 确认保存，菜单关闭后模型标签仍精确匹配。正常 UI 仅发送一次既定测试 prompt，实际回复 `PERSISTENT_REMOTE_OK`，该测试新 Run 为 `succeeded`，最终 canonical sequence 为 4066。

旧 a3540b1 曾因零游标回放耗尽共享额度而关闭 Tunnel；新镜像第一次冷浏览器测试则在创建会话后等待 `created.json()` 约 32 秒超时，未选择模型或发送消息。两次失败及 Linux 采证工具问题都保留为历史证据，不与第二次成功混淆。当前通过范围限于保留卷重建、上述单页连接和一次聊天；长期空闲、多页面并发与全部 App 流程仍未验证。

本页保留真实通过、失败与待验证项，不能代替 [2026-10-01 公网验收](remote-workspace-production-2026-10-01.md) 中的撤销与安全证据，也不把旧轮次结果套用到本轮持续授权。

## 实现与部署范围

Ambient 为本机执行端，云项目提供身份、授权和访问入口。Ambient 不加入 submodule；云项目建议保留在 [codex/ambient-until-revoked-20261002](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/codex/ambient-until-revoked-20261002)，云源码 `main` 未改写。本轮没有执行架构迁移，整体简化与替代传输方案仍待评估。

Ambient 源码已发布至 `main` 提交 [6ff18cca0e12e41e85f86ab02094d57451360f74](https://github.com/BillShiyaoZhang/ambient-agent/commit/6ff18cca0e12e41e85f86ab02094d57451360f74)，精确核对该 head 的 [CI Pipeline 36913640171](https://github.com/BillShiyaoZhang/ambient-agent/actions/runs/36913640171) 整轮及四个 job 全部成功，文档 [Pages 36913640205](https://github.com/BillShiyaoZhang/ambient-agent/actions/runs/36913640205) 也成功。本次验收文档发布的整轮状态以其 main 提交关联的 [checks](https://github.com/BillShiyaoZhang/ambient-agent/actions/workflows/ci.yml?query=branch%3Amain) 为准。

- 只有显式 `until_revoked: true` 才启用持续授权，布尔值严格校验；有限期限仍为 300 秒至 30 天，旧 payload 保持兼容。
- Ambient 先以无凭据 GET `/v1/connector/capabilities` 验证平台支持，再提交持续授权。能力 DTO 为 `{"supported_grant_modes":["bounded","until_revoked"]}`；公开 `/health` 仍关闭。
- 持续授权返回必须精确匹配固定 sentinel；截短期限、其它等价长日期和错误响应被拒绝。已有有限授权不迁移，持久字段仍为 ISO 日期，不使用 Infinity 或 null。
- 范围、同账户领取、当前授权身份和本机批准、撤销与过期检查保留。长期授权不延长 launch ticket 或浏览器 Cookie 会话；会话到期后需从门户重新打开工作区。
- 初始持续授权发布为云 revision `a3540b17c651c832ce9b1373cde7656e9c1ef7b3`，镜像 `sha256:0b99be25fe5b2c64ec4e3eefd6111f8e0ce1ebd677e0298ab57e354cef84680c`；上述历史回放失败属于这个旧版本。
- 历史回放修复已推送同一云建议分支提交 [0187361bb482d7614eb2079aa30c06a312694600](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/commit/0187361bb482d7614eb2079aa30c06a312694600)，修改九个路径并通过 105 项 Gateway/ingress 测试。该 revision 的真实 Linux/amd64 生产镜像 `sha256:fb0e62c585b8084186331307f17e78f1e5e9b13596f2e3e69de663b3026cc9d8` 已替换现网 Gateway。
- 新发布仍使用在线 SQLite backup API，保持实时卷与授权数据库、Gateway 环境和其它服务容器身份；四份 nginx 文件逐字节不变，没有写入或 reload nginx。公开能力 GET 和私有路由 404 通过，失败回滚基准为旧 a354 镜像，不用数据库快照覆盖实时授权。单页冷启动、30 秒连接和一次聊天闭环复测已通过。

## 回归与当前实测

| 项目 | 已获得的证据 | 当前状态与边界 |
| --- | --- | --- |
| 后端与授权契约 | 后端全量 1041 通过、22 跳过；持续授权严格布尔值、能力预检、旧 bounded payload、精确 sentinel、日期边界与撤销负例通过 | 本地回归通过；跳过项不作为实际运行证明 |
| 前端、构建与文档 | 38 个前端文件、305 个测试通过；TypeScript/Vite 构建、oxlint、UML 与当时 33 对文档校验通过；新增本页后 34 对校验通过 | 本地通过；后续文档修改仍须重新校验 |
| 本机原生模型 | 网页配置与原生 Luna 模型测试通过，Codex 安装器针对固定官方包展开大小完成修复 | 模型网页测试通过；未声称内部请求次数或全部远程模型流程通过 |
| 实际持续授权 | 新真实 Docker 工作区配对、批准并在线，当前 grant 为直到撤销、control-only | 配对与保留卷重建已通过；跨空闲时段恢复待测，不把期限字符串当作持久性实证 |
| 初始云发布和访问边界 | a3540b1 固定镜像部署；公开能力 GET 正确，私有 health、metrics、accounts 返回 404 | 初始持续授权发布通过；后续 0187361 替换的证据另列，旧浏览器失败仍保留 |
| 云回放修复源码 | 建议分支 0187361 的九个修改路径通过 91 项 Gateway 与 14 项 ingress 测试；控制/数据计量分离，有界异步 lane 队列与隔离 | 源码回归通过且已推送建议分支，固定镜像已部署；本轮单页浏览器闭环通过 |
| 固定 Linux 镜像与生产替换 | 实际 Linux 镜像 91 项协议测试通过，0 失败、0 跳过；源码与 safeguards 对应固定 archive；生产 deploy 收据确认精确镜像、Gateway 环境、实时数据库、其它容器身份和四份 nginx 保持，能力 GET 与私有路由负例通过 | Linux 验证及生产替换通过；不代替完整浏览器回归 |
| 旧浏览器协议短握手 | 三个 WebSocket 从最新游标的短握手通过；旧公共 probe 的 `/ws/runs` 使用 `after_sequence=4053` 跳过历史，只验证 101 与 ready frame | 当时没有覆盖从零历史回放，旧 a354 的内置及隔离 Chrome 完整页面均失败；没有证据将内置浏览器握手差异认定为独立问题或根因 |
| 旧版本新页面失败 | 旧 a3540b1 上零游标回放 4053 条历史事件，触发共享 Tunnel 限流并断开节点，之后浏览器 WebSocket 403；有界 Red 中 1199 条数据与 1 条 accept 耗尽 1200 条额度，随后全 Tunnel 以 1012 关闭，背压计数为 0 | 旧版本失败证据保留；不能因新镜像测试而删除 |
| 新镜像首次冷浏览器复测 | 真实 `after_sequence=0` 完整收到 4053 条历史事件，三类 WebSocket 均 101 且无 403；UI 创建一个新会话，随后测试等待 `created.json()` 约 32 秒超时；模型未选择、消息未发送 | 该次未完成交互，保留失败证据；不将测试中断归为新增产品失败根因已确定 |
| 第二次真实浏览器连接 | 冷启动从零游标完整回放 4053 条事件且在发送消息前完成；chat、runs、run-live 均正确 Origin/Cookie、101，并连续 30009 毫秒未关闭 | 实际通过；覆盖单页与这段观察窗口，不推断长期空闲或多页容量 |
| 第二次正常 UI 模型与聊天 | UI 新会话；Luna 配置 PUT 200、所属会话 GET 确认保存及菜单关闭后的精确模型标签；只发送一次既定 prompt，实际回复 `PERSISTENT_REMOTE_OK`，新测试 Run `succeeded`，最终 canonical sequence 4066 | 实际通过；一次 UI prompt 不等于已直接计数所有模型内部请求，也不代表全部 App 交互通过 |
| 第二次收尾数据与清理 | 只读 SQLite metadata 确认仅一个新 Run、13 条所属事件，Run 来源匹配测试新会话且持久状态 `succeeded`；历史权限决策 0、原 pending 数量 1 保留；同一精确 9999/control 授权仍 paired/online；context/browser 已关闭，自有子进程已终止 | 实际通过；不批准、拒绝或清理原有业务权限，不移除持续运行的产品容器 |
| 云资源快照 | 19:15:20 UTC 的只读 DTO：Gateway 49.38 MiB、CPU 0.34%；Web 167.4 MiB、nginx 8.629 MiB、Platform 21.82 MiB；宿主为 2 CPU、可见内存 1.827 GiB | 仅单点运行态，不代表压力容量、长时占用或账单归因 |
| 历史权限与撤销提示 | 前端先读取当前 Run，再恢复匹配会话的真实 pending interaction；终态、resolved 和迟到查询不再重新打开旧弹窗；预审批与过期历史变更不产生撤销操作 | 305 测试中包含回归，正常远程 UI 新会话/聊天已完成；测试不批准、拒绝或清理原业务 Run，全部权限分支不作为已实测 |
| 持久重建 | 19:19:06 UTC 的严格比较通过：五个容器 ID 全部更换且全部 healthy；镜像、持久挂载、健康/重启/loopback 端口、授权身份/范围/精确长期期限、模型偏好、Coding Agent 偏好、迁移标记与 SQLite 来源保持；Codex authenticated 状态前后均为真 | 实际通过；批准保留由 paired 与 authorized online Tunnel 推断，没有直接读取认证文件，也没有调用 Run 或 permission API |
| 发布与 CI | Ambient `main` 6ff18cca 已推送，精确 head 的整轮 CI 36913640171 及四个 job 成功，Pages 36913640205 成功；云建议分支已推送 0187361 | 已记录源码发布；本次验收文档发布的整轮状态以其 main 提交关联的 [checks](https://github.com/BillShiyaoZhang/ambient-agent/actions/workflows/ci.yml?query=branch%3Amain) 为准 |

## 已定位的问题与修复边界

历史 UI 问题来自把 durable Run payload 直接当作当前命令。现在弹窗以当前 Run 的 `waiting_user` 与仍为 `pending` 的 interaction 为依据；切换会话或处理完成只清理前端显示，不修改持久 Run。断线保留有效请求，查询失败不能建立新授权或丢弃已有请求。变更提示使用原事件时间的剩余 soft window，不因重放而重置倒计时。

现有实现仍可能为同一 Run 的历史 permission payload 与 `interaction_requested` 发起重复 GET；版本检查仅丢弃过时响应，未合并已经发出的 HTTP。该成本边界已静态确认，但没有据此归因为高账单来源。合并请求属于待评估优化，尚未改动冻结的 Ambient stream 契约。

旧 a3540b1 的实际断连问题来自云端在解析消息类型前，对整个节点的控制帧、WebSocket 数据和 HTTP 响应共享消息次数限制。有界 Red 已复现 1199 条数据与 1 条 accept 消费整个额度，随后全 Tunnel 以 1012 关闭且没有背压。单页合法历史回放因而可以关闭其它连接。

新镜像首次浏览器测试的正文等待超时单独保留：第二版本在创建会话采证中移除测试工具额外的 `pageResponse.json()` 读取，产品创建会话流程不需要这次额外读取。产品模型更新的 `jsonRequest` 仍读取 `res.json()`；第二版本改用请求 DTO 与保存后的 GET 核对模型，只是验收采证方式变化，没有修改产品 HTTP 实现。首次 `loadingFinished` 未被观察到，正文等待卡住的原因没有最终归因；后续 UI 成功不作为已找到并修复另一个 HTTP 产品问题的证明。

建议分支 0187361 已把控制额度与数据额度分开：控制额度保持 1200，已知和迟到数据帧仍计入节点数据额度 60000 帧与 64 MiB 编码后消息大小。节点额度超限仍关闭 Tunnel；慢浏览器或单 lane 背压只隔离相关 lane，不关闭其它连接。队列与迟到 lane 的 tombstone 均有界，tombstone 上限为 256 并受 TTL 窗口约束。105 项源码测试与固定 Linux 镜像验证已通过，生产镜像已部署，Ambient 事件协议保持不变。第二次冷 Chrome 已完成零游标完整回放、三类连接 30 秒稳定和一次正常 UI 聊天闭环；这个观察证明本次回放问题的单页恢复，不代表长期和多用户容量。

Linux 证据保留首次执行与重试：首次协议测试报告 91 项通过，但 tmpfs 中的日志/证明采集失败；调整测试工具后，第二次最终运行取得有效证明，仍为 91 项通过、0 失败、0 跳过。这是采证工具失败，不把它隐去，也不归为已观察到的产品失败。最终测试在无网络、只读 rootfs/fixture、无发布端口的容器中执行，无模型或生产云调用，测试容器已移除；真实生产 app 与 safeguards 均匹配固定源码 archive。

公开制品校验值：源码 archive SHA-256 为 `a1251068a1eeddf66ca0ea7a59726779e2223fc9c680733d6a16baa46adc7124`，镜像 gzip archive 为 `d0ed16502694bf05f38720e713c89495084fabd9776ccea118ba84f04c0c18d7`；生产 app 与 safeguards 源码分别为 `5fae8ec4a489e4b02656aaa2a82c4d422789b75af0751b64b0c44ecfc2a21791` 和 `6a98480dccb2ee3559814cdab0559b9ff2714b457073b6924bacd8d37bab91f4`。

## 证明边界与隐私范围

五容器重建的证据为 ignored `persistent-after.json` 与 `persistent-recreate-verified.json`，严格比较已通过，无需重新配对。五个服务均运行并采用 `unless-stopped`；持续访问仍要求自己的电脑在线、Docker 已启动且容器未被手动停止。该策略不能保证电脑关机期间可访问。当前 TLS 证书到 2026-12-30，尚无自动续期，必须在到期前手工更新。

首次冷浏览器测试只新增一个 UI 会话，未选择模型或发送消息，新增 Run、事件、历史权限决策均为零，原有待处理权限保留。第二次测试则在自己的新会话发送一次消息；最终只读 metadata 确认仅一个新 Run、13 条所属事件、来源匹配测试会话且 `succeeded`，历史权限决策仍为零、原 pending 数量 1 保留，不能把第一次的零新增计数套用于第二次。同一精确长期/control 授权仍 paired/online。第二次测试 context/browser 的关闭和父进程所属子进程终止均已验证；本机五个产品容器持续健康运行。

直接浏览器证据与只读收尾 metadata 保存在 ignored 的第二版本 `actual-ui.jsonl` 和 `final-own-ui-metadata.json`；聊天截图只用于本机核对，不进入公开仓库。本次覆盖冷浏览器、完整历史恢复、30 秒连接、模型配置保存和一次原生 Luna 回复；长期空闲恢复、多个页面或账户并发、全部 App/Frame 资源与权限流程未在本轮完整验收。一次正常 UI prompt、产品方法调用、网络帧与模型内部请求属于不同计数，本轮未直接捕获所有模型内部请求次数。

测试只对自有新会话和新测试 Run 操作，不对原业务 Run 或待处理权限作批准、拒绝或清理。证据只提交合成测试和有界元数据，不提交邮箱、节点标识、license、接入码、token、Cookie、认证文件或用户私人状态截图。ignored 观察目录中的原始数据不进入发布文档。既有单页/单 App 容量限制、多页面并发尚未验收，以及 TLS 到 2026-12-30 且未配置自动续期的边界仍保留。
