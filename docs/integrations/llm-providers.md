# LLM Provider 与模型切换

Ambient Agent 使用工作区级 Provider Registry 管理大模型连接。Provider 配置不再读取旧的
`LLM_PROVIDER`、`LLM_MODEL`、`LLM_API_KEY` 或 `LLM_API_URL` 环境变量。

## 配置与秘密

- 非秘密配置写入 `workspace/llm/config.json`。
- UI 输入的凭据写入 `workspace/llm/secrets.json`；文件权限必须为 `0600`，REST API 只返回
  `configured` 与掩码，不返回秘密值。
- 凭据也可以引用环境变量。配置只保存变量名，运行时由服务端解析。
- 同一预设可以创建多个 Provider Profile，例如个人 OpenAI 与公司 Azure。

Provider Profile 包含 `id`、显示名、预设、连接参数、凭据引用与模型列表。模型使用
`provider_id/model_id` 作为稳定身份，并记录 API 模式、工具调用、图像、推理、上下文窗口及
验证状态。模型发现优先请求实时 provider API，其次使用 LiteLLM 元数据；用户始终可以手工
添加模型 ID。

### MiniMax 区域

MiniMax 的国际站和中国站是两个独立 Provider 预设，API Key 与请求域名不能混用：

- `MiniMax Global` 使用 `https://api.minimax.io/v1`。
- `MiniMax 中国（minimaxi.com）` 使用 `https://api.minimaxi.com/v1`。

两个预设均通过 OpenAI-compatible 文本接口调用，支持各自区域的 Token Plan Key。区域拆分前
创建的旧 `minimax` Profile 原本代表中国站；若它没有明确配置 `api.minimax.io`，配置升级时会
自动迁移为 `minimaxi`。新建国际站配置使用 `minimax`。模型发现只保留适用于 Agent 对话的
`MiniMax-M*` 模型，不将语音、音乐或视频模型混入聊天模型选择器。

## 模型选择规则

- 全局设置包含默认模型与可选快速模型。
- 每个会话可以覆盖默认模型；切换在下一次请求生效，已经运行的请求继续使用启动时快照。
- 快速模型仅用于意图路由和会话标题；未配置时回退到会话主模型。
- 规划、Schema 对齐、校验、普通对话与 Widget 生成使用会话主模型。
- 不执行跨 Provider 自动故障转移。鉴权、限流、超时、模型缺失与工具不兼容作为结构化错误返回。
- LiteLLM 的普通补全与工具调用路径都属于后端运行时能力；部署依赖必须同时包含工具调用路径所需的
  JSON 编解码组件，避免出现标题可生成但 Agent 对话在路由阶段失败的情况。

## REST API

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/llm/catalog` | 返回 Provider 预设及其声明式字段 |
| GET/POST | `/api/llm/providers` | 列出脱敏配置或创建 Profile |
| PATCH/DELETE | `/api/llm/providers/{id}` | 更新或删除 Profile |
| POST | `/api/llm/providers/{id}/discover-models` | 刷新并保存模型列表 |
| POST | `/api/llm/providers/{id}/test` | 测试连接或指定模型 |
| GET/PATCH | `/api/llm/settings` | 读取或更新全局默认/快速模型 |
| PUT | `/api/sessions/{session_id}/model` | 更新会话主模型并广播变更 |

删除仍被默认设置、快速模型或会话引用的 Provider 或模型时返回 `409`。没有可用默认模型时，LLM
请求返回 `llm_configuration_required`，客户端应打开 Provider 设置页。

## 支持范围

预设覆盖 OpenAI、Anthropic、Google、xAI、Mistral、Cohere、DeepSeek、OpenRouter、Groq、
Together、Fireworks、Cerebras、Perplexity、NVIDIA、Hugging Face、Vercel、MiniMax Global、MiniMax 中国、Kimi、
Qwen、Doubao、GLM、SiliconFlow、Azure、Bedrock、Vertex AI、Databricks、watsonx、Cloudflare、
Ollama、LM Studio、vLLM、llama.cpp、TGI 与 Xinference。通用入口支持 OpenAI Chat、OpenAI
Responses、Anthropic-compatible、LiteLLM Proxy 和自定义 LiteLLM provider。

当前产品沿用单用户信任模型：任何能够访问 Ambient Agent 的客户端都能修改 Provider。
自定义 endpoint 会让后端访问用户填写的 HTTP(S) 地址，因此只能在受信网络中开放设置 UI。
