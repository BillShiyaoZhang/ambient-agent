# Security Policy / 安全策略

## Supported versions / 支持版本

| Version | Support |
| --- | --- |
| `0.1.x` | Security fixes for the current public alpha / 当前公开 Alpha 的安全修复 |
| `< 0.1.0` | Not supported / 不支持 |

## Report a vulnerability / 报告漏洞

Please do not disclose a vulnerability, exploit, credential, or private user data
in a public issue, discussion, or pull request.

请勿在公开 Issue、Discussion 或 Pull Request 中披露漏洞细节、利用代码、凭据
或用户隐私数据。

1. Use **Security → Report a vulnerability** in this GitHub repository when
   that option is available.
2. If the private form is unavailable, open a public issue titled
   `Security contact request` with no vulnerability details. A maintainer can
   then arrange a private reporting channel.

1. 如果仓库的 **Security → Report a vulnerability** 可用，请通过该表单私密报告。
2. 如果私密表单不可用，请只创建标题为 `Security contact request` 的公开 Issue，
   不要填写任何漏洞细节；维护者随后会提供私密沟通方式。

A useful report includes the affected version or commit, deployment mode,
reproduction steps, expected impact, and any suggested mitigation. Remove
real credentials and personal data from examples. Reports are handled on a
best-effort basis; this project does not currently operate a bug bounty.

有效报告应包含受影响版本或提交、部署方式、复现步骤、预期影响和可选的缓解建议。
请从示例中移除真实凭据和个人数据。项目将尽力处理报告，但目前不提供漏洞赏金计划。

## Security boundary / 安全边界

Ambient Agent v0.1.x is designed for one user on a trusted local machine.
Published Docker Compose ports bind to loopback by default. The Backend does
not provide account authentication or multi-user isolation, so direct LAN or
Internet exposure is unsupported.

Ambient Agent v0.1.x 面向单用户、可信本机使用；Docker Compose 默认只将端口绑定到
loopback。Backend 尚未提供账号认证或多用户隔离，因此不支持直接暴露到局域网或公网。

That deployment boundary does not make all security reports out of scope.
Examples worth reporting include bypasses of Widget capability checks or
browser isolation, cross-origin access, path traversal, secret disclosure,
unsafe migration or deletion, and vulnerabilities reachable through the
documented trusted-local configuration.

这一部署边界不代表安全问题均不在范围内。值得报告的例子包括：绕过 Widget 能力检查
或浏览器隔离、跨源访问、路径穿越、密钥泄露、不安全的数据迁移或删除，以及在文档所述
可信本机配置中仍可触达的漏洞。

