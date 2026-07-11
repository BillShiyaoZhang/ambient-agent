# Contributing to Ambient Agent

感谢您对 Ambient Agent 的关注！🎉 无论是修复 Bug、提出新功能，还是改进文档，我们都非常欢迎您的贡献。

本文档将指引您完成从 Fork 到 PR 合并的完整流程。

---

## 📋 目录

- [行为准则](#行为准则)
- [贡献前的准备](#贡献前的准备)
- [开发工作流（Fork & PR）](#开发工作流fork--pr)
- [分支命名规范](#分支命名规范)
- [代码规范与质量校验](#代码规范与质量校验)
- [提交 Pull Request](#提交-pull-request)
- [Review 流程](#review-流程)

---

## 行为准则

请保持友善、尊重和建设性的沟通态度。我们致力于为所有人创造一个开放、包容的协作环境。

---

## 贡献前的准备

### 环境要求

本项目完全容器化，您只需要在本地安装以下工具：

| 工具 | 用途 | 下载地址 |
|---|---|---|
| **Docker Desktop** | 运行开发容器 | https://www.docker.com/products/docker-desktop/ |
| **VS Code** | 推荐的开发 IDE | https://code.visualstudio.com/ |
| **Dev Containers 插件** | VS Code 连接容器 | VS Code 扩展商店搜索 `Dev Containers` |

> **注意**：您**不需要**在本地手动安装 Python、Node.js 或任何其他依赖。所有环境均在容器内自动配置。

### 先搜索，再贡献

在开始开发之前，请先：

1. 检查 [Issues](https://github.com/BillShiyaoZhang/ambient-agent/issues) 中是否已有相关的讨论或正在进行的工作
2. 如果是较大的功能改动，建议先开一个 Issue 描述您的想法，与维护者确认方向后再开始开发

---

## 开发工作流（Fork & PR）

### 第 1 步：Fork 仓库

点击仓库页面右上角的 **Fork** 按钮，将仓库复制到您自己的 GitHub 账号下。

### 第 2 步：Clone 您的 Fork

```bash
git clone https://github.com/您的用户名/ambient-agent.git
cd ambient-agent
```

### 第 3 步：添加上游仓库（保持同步用）

```bash
git remote add upstream https://github.com/BillShiyaoZhang/ambient-agent.git
```

验证配置：
```bash
git remote -v
# origin    https://github.com/您的用户名/ambient-agent.git (fetch)
# upstream  https://github.com/BillShiyaoZhang/ambient-agent.git (fetch)
```

### 第 4 步：启动开发容器

用 VS Code 打开项目目录，按下 `Cmd+Shift+P`（Windows/Linux 上是 `Ctrl+Shift+P`），选择 **`Dev Containers: Reopen in Container`**。

容器启动后，Python 3.11、Node.js 22、`uv`、以及所有前后端依赖将**自动安装完毕**，无需任何手动操作。

### 第 5 步：从最新的 main 创建新分支

```bash
# 先同步上游最新代码
git fetch upstream
git checkout main
git merge upstream/main

# 创建您的功能分支（参考下方命名规范）
git checkout -b feature/your-feature-name
```

### 第 6 步：开发并验证

在容器内的终端中启动服务：

```bash
# 终端 1：启动后端
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 终端 2：启动前端
cd frontend && npm run dev
```

> **重要**：如果 IDE 未自动转发 8000 端口，请在 **Ports（端口）** 标签页手动点击 **Add Port** 添加 `8000`。

在浏览器中访问 [http://localhost:5173/](http://localhost:5173/) 验证您的修改。

### 第 7 步：运行代码校验（提交前必做）

```bash
# 后端：格式化与静态分析
uv run ruff format .
uv run ruff check . --fix

# 后端：运行单元测试
PYTHONPATH=. uv run pytest

# 前端：进入 frontend 目录运行
cd frontend
npm run lint
npm run test
```

所有检查必须通过，否则 PR 会被 CI 自动拒绝。

### 第 8 步：提交并推送到您的 Fork

```bash
git add .
git commit -m "feat: 简短描述您的改动"
git push origin feature/your-feature-name
```

### 第 9 步：发起 Pull Request

前往您的 Fork 页面（`https://github.com/您的用户名/ambient-agent`），GitHub 会自动提示您发起 PR。

确认 PR 的目标是：**`BillShiyaoZhang/ambient-agent:main`**。

---

## 分支命名规范

| 类型 | 命名格式 | 示例 |
|---|---|---|
| 新功能 | `feature/简短描述` | `feature/widget-weather-card` |
| Bug 修复 | `bugfix/简短描述` | `bugfix/websocket-reconnect` |
| 文档改进 | `docs/简短描述` | `docs/update-contributing` |
| 代码重构 | `refactor/简短描述` | `refactor/graph-db-service` |

---

## 代码规范与质量校验

### 后端（Python）

- **包管理**：使用 `uv`，禁止使用 `pip` 直接安装
- **代码风格**：使用 **Ruff** 作为 Linter 和 Formatter（配置见 `pyproject.toml`）
- **类型注解**：所有公共函数必须添加类型注解
- **测试**：新增功能需附带对应的 Pytest 单元测试，测试文件位于 `tests/backend/`

### 前端（TypeScript / React）

- **代码风格**：使用 **Oxlint** 进行静态分析（`npm run lint`）
- **类型安全**：避免使用 `any` 类型
- **Widget API**：必须使用 `ambient.graph.subscribe` 和 `ambient.graph.mutate`
- **测试**：组件和 SDK 测试使用 **Vitest**，测试文件位于 `tests/frontend/`

### 提交信息规范（Commit Message）

请遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<类型>: <简短描述>

[可选的详细说明]
```

常用类型：`feat`、`fix`、`docs`、`refactor`、`test`、`chore`

---

## 提交 Pull Request

一个好的 PR 应该包含：

- **清晰的标题**：简洁描述改动内容（如 `feat: add weather widget`）
- **改动说明**：描述您做了什么、为什么这样做
- **测试说明**：说明如何在本地验证该改动
- **关联 Issue**：如果有对应的 Issue，请在描述中写 `Closes #123`

---

## Review 流程

1. 提交 PR 后，GitHub Actions 会自动运行 CI 检查（约 1 分钟）
2. CI 全绿后，维护者会进行代码 Review
3. Review 意见会以评论形式反馈，请根据意见修改并 Push 新提交
4. 获得 **1 名维护者 Approve** 且 CI 通过后，PR 将被合并到 `main`

感谢您的贡献！🚀
