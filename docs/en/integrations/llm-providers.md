# LLM Providers and Model Switching

Ambient Agent uses a workspace-scoped Provider Registry for model connections. The legacy
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_URL` environment variables are no longer read.

## Configuration and secrets

- Non-secret configuration is stored in `workspace/llm/config.json`.
- UI credentials are stored in `workspace/llm/secrets.json`. The file must use mode `0600`; REST
  responses expose only `configured` and a mask, never the secret value.
- A credential may reference an environment variable. Only the variable name is persisted.
- Multiple profiles may use the same preset, such as personal OpenAI and company Azure accounts.

A Provider Profile contains its id, display name, preset, connection fields, credential references,
and models. A model is identified by `provider_id/model_id` and records API mode, tool use, vision,
reasoning, context window, and verification state. Discovery prefers the live provider API and then
LiteLLM metadata; manual model ids are always supported.

### MiniMax regions

MiniMax Global and MiniMax China are separate provider presets. Their API keys and hosts are not
interchangeable:

- `MiniMax Global` uses `https://api.minimax.io/v1`.
- `MiniMax China (minimaxi.com)` uses `https://api.minimaxi.com/v1`.

Both presets use the OpenAI-compatible text API and accept Token Plan keys from their respective
regions. Before the split, the old `minimax` preset represented China. Existing profiles without an
explicit `api.minimax.io` host are therefore migrated to `minimaxi`; new Global profiles use
`minimax`. Discovery keeps Agent-compatible `MiniMax-M*` chat models and excludes speech, music,
and video models from the chat model picker.

## Model selection

- Global settings contain a default model and an optional fast model.
- A session may override the default. Changes affect the next request; an in-flight request retains
  the selection snapshot captured at its start.
- The fast model is used only for intent routing and session titles. If unset, the session model is used.
- Planning, schema alignment, verification, conversation, and widget generation use the session model.
- There is no automatic cross-provider failover. Authentication, rate-limit, timeout, missing-model,
  and tool-compatibility failures are returned as structured errors.
- Both plain completions and tool-enabled LiteLLM calls are backend runtime capabilities. Deployment
  dependencies must include the JSON codec used by the tool path so a working title request cannot
  mask an Agent-routing startup failure.

## REST API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/llm/catalog` | Return provider presets and declarative fields |
| GET/POST | `/api/llm/providers` | List redacted profiles or create one |
| PATCH/DELETE | `/api/llm/providers/{id}` | Update or delete a profile |
| POST | `/api/llm/providers/{id}/discover-models` | Refresh and persist models |
| POST | `/api/llm/providers/{id}/test` | Test the connection or a model |
| GET/PATCH | `/api/llm/settings` | Read or update global default/fast models |
| PUT | `/api/sessions/{session_id}/model` | Change the session model and broadcast it |

Deleting a referenced provider or model returns `409`. An LLM request without a usable default returns
`llm_configuration_required`, prompting the client to open provider settings.

## Coverage

Friendly presets cover OpenAI, Anthropic, Google, xAI, Mistral, Cohere, DeepSeek, OpenRouter, Groq,
Together, Fireworks, Cerebras, Perplexity, NVIDIA, Hugging Face, Vercel, MiniMax Global, MiniMax China, Kimi, Qwen, Doubao,
GLM, SiliconFlow, Azure, Bedrock, Vertex AI, Databricks, watsonx, Cloudflare, Ollama, LM Studio, vLLM,
llama.cpp, TGI, and Xinference. Generic entries support OpenAI Chat, OpenAI Responses,
Anthropic-compatible endpoints, LiteLLM Proxy, and custom LiteLLM providers.

The current product keeps its single-user trust model: every client that can reach Ambient Agent may
modify providers. A custom endpoint makes the backend access the supplied HTTP(S) address, so expose
the settings UI only on trusted networks.
