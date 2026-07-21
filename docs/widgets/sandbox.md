# Widget 运行边界与动态编译

`SandboxWidget` 是动态 React Controller 的宿主。新版本把 capability 安全边界定义为“发布时静态验证 + 最小 SDK membrane + 后端逐次授权”；React ErrorBoundary 和组件名中的 Sandbox 只表示故障隔离，不表示可以安全执行任意第三方 JavaScript。

## 1. 加载前条件

宿主只加载 Manifest V2 App。返回给前端的 App snapshot 包含规范化 grants 与 revision；缺少 Manifest、版本错误、grants 不合法或 Controller hash 与发布记录不一致时，API 不返回可执行 Controller。

发布 verifier 拒绝：

- import、dynamic import、`window`、`document`、`globalThis`、storage、`fetch`、XHR、WebSocket、Worker、`eval` 和 `Function`；
- 不能静态解析的 capability/source/catalog/action ID；
- 没有批准 grant 的 `ambient` namespace 或超出 scope 的 resource；
- staging Manifest grants 与批准 Runtime Contract 不相等的产物。

## 2. SDK Membrane

宿主从 grants 构造一个新的冻结对象，不把完整内部 SDK 传给 Controller：

```javascript
const ambient = Object.freeze({
  ...hostFeatures,
  ...(canGraphQuery ? { graph: Object.freeze({ subscribe }) } : {}),
  ...(canUseNetwork ? { net: Object.freeze({ request }) } : {}),
  ...(canUseFiles ? { files: Object.freeze(fileMethods) } : {}),
  ...(canInvoke ? { capabilities: Object.freeze({ invoke }) } : {})
});
```

SDK 自动绑定当前 App ID；Controller 不能传入其他 App 身份。每个调用携带 manifest revision/grants digest，后端仍以持久 Manifest 为准重新授权。

## 3. 后端是最终边界

所有外部访问进入统一 `CapabilityAuthorizer.authorize(app_id, capability, operation, resource)`。Graph、HTTP、File 和 Capability adapter 在 I/O 或副作用之前调用它；route 与 WebSocket handler 不复制判断。授权失败返回结构化错误并进入审计。

前端隐藏方法、冻结对象和静态 verifier 都是纵深防御，不能单独作为授权依据。用户批准 grant 也不能放宽 Graph schema、网络 SSRF、文件路径、Capability input/output、MCP spawn、Run interaction、幂等或恢复 policy。

## 4. 动态编译与故障隔离

Controller 仍由 `@babel/standalone` 转译，并通过受限 module wrapper 在宿主页 realm 中执行。React `ErrorBoundary` 隔离编译/渲染失败，cleanup 回收 Widget 注册的 Graph/Run listeners。

由于同 realm 动态执行并非 hostile-code 隔离，发布路径只接受系统 verifier 通过、由用户批准生成的本地代码。CPU 密集循环等可用性风险仍需要后续独立 origin iframe/Worker 和资源预算解决；capability policy 解决的是宿主 I/O 授权，不应被描述为通用 JavaScript sandbox。

## 5. 验证要求

- 前端测试验证不同 grants 产生不同且冻结的 SDK surface。
- verifier 测试验证禁止 global、动态 ID 和未批准 API。
- 后端契约测试直接构造越权 HTTP/WebSocket 请求，证明不依赖前端。
- 修改/撤销 Manifest grant 后，现有页面的下一次调用也必须失败。

完整类目与 scope 见 [Widget 能力安全架构](/architecture/capability-security.md)。
