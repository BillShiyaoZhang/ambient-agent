# Widget 运行边界与动态编译

`SandboxWidget.tsx` 在浏览器中编译并加载动态 Widget。这里的 “Sandbox” 是产品组件名和 API 边界，不代表 hostile JavaScript 的强安全隔离。

## 1. 当前加载机制

Widget controller 由 `@babel/standalone` 转译后，通过以下模块 wrapper 执行：

```javascript
const runScript = new Function("exports", "React", "ambient", transpileScript);
runScript(exportsObj, React, ambientProps);
```

- `exports` 提供模块导出容器；
- `React` 提供 React API；
- `ambient` 提供 Graph、Run、Capability、MCP 等宿主接口；
- 组件渲染放在 React `ErrorBoundary` 内，语法/渲染异常会显示在 Widget 区域。

这些参数减少了 Widget 对内部实现的直接依赖，但 `new Function` 仍运行在页面 JavaScript realm 中。它不会阻止代码访问 `window`、DOM、storage 或同 origin 网络能力，ErrorBoundary 也只处理 React 错误，不是权限边界。

## 2. 宿主 API 的真正 enforcement

涉及宿主状态的操作必须在后端重新校验，不能信任 Widget 传入的 `app_id`、tool name、schema 或 permission 声明：

- capability 与 MCP tool 通过持久 Run 执行；
- Graph action 在后端预检并用 SQLite transaction 提交；
- 模型本地 tools 经过 Tool Gateway；
- MCP/OpenCode 使用各自的 permission、path、argv、environment 和 lifecycle policy。

前端隐藏或不注入某个 `ambient` 方法只能改善 API 表面，不能替代后端授权。

## 3. 动态编译与故障隔离

`@babel/standalone` 在挂载时转译 controller。编译或组件渲染失败由 Widget 局部 ErrorBoundary 展示，从而降低单个 Widget crash 对 Canvas React tree 的影响。

这属于可用性隔离，而不是保密性或完整性隔离。CPU 密集循环、直接 DOM 操作和同源资源访问仍可能影响宿主页。

## 4. 若需要运行不受信任代码

强隔离需要独立安全边界，例如 sandboxed iframe + 独立 origin、受限 Worker，或后端/OS 容器，并配合 CSP、消息 schema、网络 allowlist 和资源预算。完成该迁移前，只应运行用户信任的本地 workspace Widget；不要把当前组件描述为安全执行任意第三方代码的 sandbox。
