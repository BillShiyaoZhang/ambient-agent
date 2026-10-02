# LLM Providers and Model Switching

Ambient Agent uses a workspace-scoped Provider Registry for model connections. The legacy
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_URL` environment variables are no longer read.

## Configuration and secrets

- Non-secret configuration is stored in `workspace/llm/config.json`.
- UI credentials are stored in `workspace/llm/secrets.json`: POSIX uses mode `0600`, while Windows
  uses a protected DACL allowing only the file owner, with inheritance disabled. Temporary files
  receive these permissions before secrets are written; replacements and existing files are secured
  again. Permission failures abort saving rather than silently broadening access. REST
  responses expose only `configured` and a mask, never the secret value.
- A credential may reference an environment variable. Only the variable name is persisted.
- Multiple profiles may use the same preset, such as personal OpenAI and company Azure accounts.

A Provider Profile contains its id, display name, preset, connection fields, credential references,
and models. A model is identified by `provider_id/model_id` and records API mode, tool use, vision,
reasoning, context window, and verification state. Discovery prefers the live provider API and then
LiteLLM metadata; manual model ids are always supported.

API-provider rediscovery merges by model ID without replacing existing API modes, manual display names,
sources, or verified capabilities. Duplicate IDs yield one entry; existing models absent from
discovery remain available to default and session references. The merge uses the latest saved
configuration after the network request, preserving edits made while discovery was pending.
If the provider connection, preset, enabled state, or credentials change during the request,
the stale response and metadata are discarded and the current model list is returned.

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

### Shared connections and independent roles

Configuration has four layers: connections/accounts (API credentials or the managed Codex login),
model catalogs/transports, primary/fast/coding model bindings, and the ACP coding executor. Consumers
share the login while each adapter verifies its own catalog and capabilities. This borrows centralized
connection management from CC Switch without copying OAuth tokens or rewriting desktop CLI files.

`POST /api/llm/connections/codex/sync` projects an installed, authenticated managed Codex connection
into the Provider Registry. It reuses the first enabled `codex_native` profile, or creates
`ambient-codex` with a numeric suffix on ID collision. Disabled-only native profiles return
`llm_provider_unavailable` and require explicit enabling. The response is a sanitized profile.
Sync and native-provider discovery use the coding CLI's visible catalog for current model IDs, names,
and ordering, so primary/fast and coding selectors show the same current entries. Optional model
`availability` contains `native_inference` and `coding` compatibility flags and a bounded `reason`:
`native_catalog_missing`, `native_profile_unsupported`, `coding_catalog_missing`, or null.
Primary/fast compatibility still requires the pinned primary CLI's catalog and public model metadata;
new coding entries do not broaden native inference support. These flags establish neither entitlement
nor tool verification. API providers and legacy models without the field retain existing behavior.
Known incompatible native entries remain visible but disabled with a reason in primary/fast selectors;
backend resolution also rejects them. Existing bindings remain displayed without automatic replacement.

Merging follows current coding order, refreshes discovered names, and preserves manual names and
verified capabilities. Previously saved models absent from the coding catalog remain at the end for
reference preservation, with both roles unavailable and `coding_catalog_missing`. Each discovery
refreshes availability rather than retaining old compatibility. Missing installation/login,
unsupported profiles/platforms, empty or malformed catalogs, and logout/disable/delete during discovery
fail or discard stale results without creating an empty profile or changing role bindings. Concurrent
and repeated syncs reuse one profile. This mutation requires `workspace.manage`, performs no inference,
and does not establish entitlement or tool compatibility; use the existing model test for that.
An in-memory connection lifecycle generation detects delete/recreate and disable/reenable while
preserving concurrent name/model edits. Authentication operation IDs separately detect logout/relogin.
Generations coordinate requests within the same Store/service process, without a disk-schema change
or a claim of cross-process write coordination.
API sync, native discovery, and connection-test discovery share the managed Runtime used for actual
login/logout; a newly constructed instance with empty generations cannot provide this protection.
Connection tests with no explicit model or saved binding prefer models compatible with primary/fast
inference. A catalog containing only coding-only entries returns `ok=false` and
`llm_capability_unsupported`, explaining that no compatible primary/fast model is available rather
than asking users to add an already listed model. Explicit selections and saved bindings do not fall back.
Logout immediately suspends new syncs: runtime status reports unauthenticated while logout is pending,
and the UI disables sync. Completion, failure, and cancellation clear the pending marker, after which
actual CLI authentication probes determine the state; a still-active old login cannot register a connection.

Settings exposes “Sync primary/fast models” and invokes it once after a successful login. Existing
logins can use the same action without manually adding a provider. Opening settings alone never
recreates a deleted connection. Successful sync also refreshes the coding catalog; manual coding
refresh discovers existing enabled native providers and reloads primary/fast choices. Closed dialogs and
logout/relogin invalidate old UI responses; failures preserve selections and offer retry without a
request loop. Sync never selects primary, fast, or coding models automatically.

Acceptance covers login-to-model availability, idempotency/concurrency, preservation of disabled
profiles/API connections/role and session bindings, and failure/staleness without credential copying.
Common models are selectable, coding-only models are visible with an explicit disabled reason, missing
historical selections remain referenced, API behavior remains compatible, and refresh updates both catalogs.

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
| POST | `/api/llm/connections/codex/sync` | Sync the managed Codex login into primary/fast model connections without changing bindings |
| GET/PATCH | `/api/llm/settings` | Read or update global default/fast models |
| PUT | `/api/sessions/{session_id}/model` | Change the session model and broadcast it |

Deleting a referenced provider or model returns `409`. An LLM request without a usable default returns
`llm_configuration_required`, prompting the client to open provider settings.

## Automatic Codex model catalog refresh

Coding model selectors use persisted `settings.agent_models` as their display source. Successful save responses immediately update selection; refreshes started before a save cannot later overwrite it. Disable repeated submissions from the same selector while saving, retain the original selection on failure, and show the error. `GET /api/coding-agents` reads model bindings after status probes finish, preventing `agents[].model_config` and `settings.agent_models` in one response from representing opposite sides of a switch. Controlled request-order acceptance covers an old refresh completing after save, a switch during status probes, and persistence after reopening settings.

The model list comes from `model/list` on the Codex `app-server` actually used by the Ambient backend. The UI does not hard-code model names or substitute the general OpenAI API model catalog. Connections send `initialize`, then `initialized`, then paginated `model/list`, displaying visible entries and the returned default. Ambient owns this Codex login and state directory; container deployments read their managed directory rather than the Windows desktop Codex account or cache.

With Codex installed and signed in, opening model settings, completing installation or login, and changing the installed version refresh the coding agent's native catalog. Enabled Codex Native Providers also sync through the existing `discover-models` endpoint, then reload default/fast model choices. Newly created native Providers trigger discovery as well. API Providers retain manual discovery, and model merging follows the reference and capability preservation rules above. Automatic refresh keeps default, fast, and coding-agent bindings unchanged and retains manual refresh controls.

After closing settings or signing out, old coding-agent catalog requests cannot overwrite a later open or login. Signing out clears the UI's coding-agent catalog. Refresh failures display an error and retain existing displayed data; the next open or manual refresh can retry without an automatic request loop. Mocked acceptance covers newly listed models after reopening or signing in, and ignores obsolete results after closing or signing out during a refresh.

Closing settings does not cancel an active login: its device-code result remains available when settings reopen, while cancellation, sign-out, a new login, or an account-state change invalidates obsolete results. Parent configuration updates reporting an active login preserve the device code and polling, so closing or synchronizing state does not strand the login UI.

`model/list` may use Codex's own cached or bundled catalog, so it is not a live entitlement check. Listing a model does not prove it satisfies Ambient native inference's version, platform, and capability checks; actual calls follow the native contract below and require testing the selected model.

## Managed Codex installation verification

The coding CLI target is official `0.159.3`, with `@agentclientprotocol/codex-acp@2.1.1` pinned inside its declared Codex `^0.159.1` compatibility range. Newer official releases require compatibility verification before updating this project pin; catalog entries remain dynamically discovered.

Coding installation must include the matching `codex-code-mode-host`. The installer checks pinned SHA256 hashes, archive members, and sizes for both CLI and host; Update completes an existing CLI installation that lacks the host. Launch checks incomplete installations and returns `coding_agent_code_mode_unavailable`, requiring environment repair instead of repeated model calls for a missing artifact. Primary/fast inference continues to disable the external CodeMode host.

File edit permissions follow the negotiated ACP initialization response. Exact `codex-acp@2.1.1` uses its advertised `diffPatch` extension to check a single tool call's paths, Git patch, and current file revision; standard `oldText` hunks are no longer mistaken for complete files. Unknown contracts fail safely, and approval forbids moves, extra paths, and expanded capabilities.

This bridge limits each `diffPatch` to 1MiB. Updates with missing evidence after exceeding that limit cannot be approved from paths alone; the general controller verifier's 2MiB limit does not establish support for a full Codex rewrite of that size in one operation.

See the [coding recovery acceptance record](../verification/codex-coding-recovery-2026-10-02.md) for actual Docker installation completion, Codex file creation/local updates, and first-frame results.

The coding CLI uses a separate versioned `CODEX_HOME` for its model cache, sessions, and configuration. Only `auth.json` is a trusted symbolic link to the original Ambient managed login file; no credential contents are read or copied. Login and logout remain owned by the CLI in the original managed directory, so removing that source login also invalidates coding authentication. An existing wrong link or regular authentication file is rejected instead of silently using a second account. The CLI's in-place login-file save behavior must be reverified when upgrading.

Coding catalog discovery and ACP execution use a separately pinned newer CLI. Native primary/fast inference reuses the adapted `0.159.3` binary once installed, while installations without the upgrade retain the `0.145.0` profile. Their state directories remain separate. Server catalogs depend on client version, so refreshing an older CLI does not guarantee models shown by a newer desktop client. The display catalog therefore uses the coding CLI, with separate primary/fast compatibility checks. Settings show the actual coding CLI version and offer Update for an existing older installation; completion automatically rediscovers models. Names continue to come from the execution CLI's `model/list`, without copying the desktop catalog or adding names not returned by that CLI.

The newer coding CLI installs into its own version directory, retaining the original `bin/codex`, managed login, primary settings, and model bindings. First installation provides the pinned login CLI then the coding CLI; an existing installation only adds the coding CLI. Both purposes share the CLI-owned Ambient login, without reading or copying desktop authentication. The CLI may refresh its public model cache; primary inference checks exact adapted versions, configuration, and model metadata without automatically accepting future coding CLI versions. Explicit `CODEX_COMMAND` remains operator-managed and cannot be replaced by the UI. Download, archive member, exact size, or version failures retain the existing CLI and report a retryable failed operation; concurrent updates reuse one operation.

Web installation pins the official `0.145.0` CLI, verifies the platform-specific archive SHA-256, and copies only its exact named regular-file member. Compressed downloads retain the 160MiB bound and streamed byte accounting; the expanded file must match an independently recorded exact size for each pinned release artifact. The compressed bound must not be reused for the expanded CLI. The verified Linux x86_64 artifact is 113,724,150 compressed bytes and 310,730,800 CLI bytes, so legitimate installation must not be rejected by a 160MiB expanded-size threshold.

Size mismatches, including one byte, incorrect paths, symbolic or hard links, excessive downloads, checksum mismatches, and incorrect probed versions fail and clean this operation's staging directory. Validated installation retains `0700` permissions, the managed destination, and pinned version probing, without extracting other members, changing versions/models, or reading/writing native authentication. Acceptance first uses isolated synthetic archives for the valid expanded size and negative cases, followed by normal web installation into the real Docker persistent volume.

## Native Codex primary-model integration contract

### Native inference adaptation for 0.159.3

Modern primary/fast inference reuses the pinned `0.159.3` coding binary with a separate
`agents/codex/inference/0.159.3/state` home for model caches, configuration, and sessions.
`CodingAgentRuntime.inference_command`, `inference_state_dir`, and `inference_environment` provide
the trusted launch boundary. Only a trusted `auth.json` symlink shares the original managed login;
credential contents are never read or copied. Managed modern commands and homes use absolute paths even with relative
workspace/runtime roots, so inference can start outside the project. Directory guards reject
symlinks/non-directories, independent auth files, and wrong links. Login/logout remain in the original
managed home. Without the newer managed binary, retain the legacy `0.145.0` profile. Explicit
`CODEX_COMMAND` retains operator commands/state, with exact supported-version checks in transport.
Failure after selecting a modern profile does not fall back to another CLI, model, or API.

Exact CLI and initialize versions choose separate safety profiles. Both retain Linux-only execution,
temporary cwd outside projects, ephemeral threads, read-only/no-network sandboxing, empty environments,
dynamic tools/capability roots/MCP, owned process groups, deadline/message/byte budgets, effective config
checks, and strict final JSON. The modern profile disables cloud skills, sleep, background/UI and new
external capabilities, explicitly uses file auth storage, and checks typed config plus raw sessionFlags.
Legacy configuration and assertions remain separate.

Modern `code_mode_host=false` keeps the host disabled, without starting an external computation
process or claiming the legacy embedded V8 still executes. Models can return strict text/JSON directly;
CodeMode attempts fail safely. Accept only two fixed official `warning` messages for the owned thread:
the exact `skip_host_skill_discovery` notice using this operation's captured home/config.toml, and
the host-disabled notice that states CodeMode will fail closed. Require exactly message/threadId fields
without displaying their contents; reject other warnings, direct-tool fallback, and unknown paths.

Modern public metadata permits `code_mode_only` and `multi_agent_version=v1|v2` while agents remain
disabled. Experimental tools are limited to audited `clock`, `request_user_input_async`, and the catalog's
legacy alias `send_user_message_async`; reject other tools or metadata shapes. Clock only reads time. Model-driven
async user messages remain in this ephemeral thread's bounded buffer, without forwarding them to users,
interrupting Runs, or creating Ambient tool calls. Validate the public fields of modern
`agentMessage.delivery=async`, retain the same type and delivery for each item ID, and exclude these messages from final output; the legacy profile rejects
this extension. Accept exactly one ordinary final message and validate its complete JSON and all
registered Ambient tool arguments before returning. Unknown delivery, native effect items, notifications,
callbacks, identities, or policies still fail closed; compatibility never ignores unknown messages.

Modern `model/list` can emit the public `account/updated` status notification. Accept exactly the
`authMode` and `planType` fields: auth is `chatgpt` or null, and plan is a pinned public protocol enum or
null. Do not persist account data, substitute this notification for `account/read` login validation,
or initiate login/retry. Reject other auth modes, unknown fields/enums, and this notification in the
legacy profile.

Acceptance uses official pinned source/protocol fixtures for Red→Green GPT-6 text, Ambient JSON tool
selection, async filtering, configuration/metadata/identity/native-effect rejection, preserving legacy
regression. An isolated unauthenticated Linux probe checks the actual CLI/effective config. Then bounded
exact-model GPT-6 text and tool-selection probes use the existing managed login with no workspace data,
recording version/model/results and leaving role bindings unchanged. This scope establishes transport
compatibility rather than full App, public-access, or recovery acceptance.
See the [0.159.3 acceptance record](../verification/native-codex-0-159-3-2026-10-02.md) for actual results.

This integration and acceptance contract addresses the capability gap found during 2026-10-01 public acceptance and precedes product code. Each capability is supported only after implementation, Red/Green, and its corresponding real acceptance; see staged results in [public acceptance](../verification/remote-workspace-production-2026-10-01.md). Short Chat success does not replace full App or recovery acceptance. The local web UI creates a `codex_native` Provider, selects an exact model ID such as `gpt-5.6-luna`, and configures existing default/fast models and session overrides. It uses official Codex independent login/subscription; Coding Agent native-model settings and Ambient primary selection do not overwrite one another.

| Configuration or API | Required native behavior |
| --- | --- |
| Preset and model mode | Preset `codex_native` and model `api_mode="codex_native"`; API Providers retain `chat_completions`/`responses`, without mixing modes |
| Declarative fields | `fields=[]`, `advanced_fields=[]`, Profile `connection={}`, `credential_refs={}`, and empty submitted credentials. No API key is required or accepted; endpoint, headers, profile, command, launch arguments, auth paths, and environment variables are rejected |
| Creation, update, and resolution | Existing `/api/llm/providers` and configuration loading validate native constraints before saving unknown connection/credential input. `ResolvedModel.credentials={}` and exact native IDs have no LiteLLM prefix or cross-provider fallback |
| Discovery | Existing `discover-models` requires stable managed login and uses the coding app-server catalog for unified display, with separate primary catalog/metadata compatibility checks and no LiteLLM substitution. Low-level `NativeCodexTransport.discover_models` still discovers the primary catalog independently of login. Cache/catalog entries do not prove entitlement or verification. Missing login remains explicit; only a real exact-model test marks verification passed |
| Tests and management | Existing `/api/llm/providers/{id}/test` makes a bounded call through the same native transport. Existing Coding Agent login operations retain auth outside Provider secrets. Remote `workspace.manage` gates apply; control-only cannot change Providers, default/fast models, or native login configuration |

Native Provider creation/editing displays only name and ID, without endpoint, credentials, or advanced connection fields, explaining: “Uses Ambient-managed Codex login; sign in under Coding Agent, no API key required.” OpenCode shared-binding choices exclude native Codex Providers. When Ambient primary is native, “inherit Ambient primary” is disabled with a clear hint to choose a separate API Provider; explicit API model bindings remain available. Codex's native model dropdown stays independent, supporting exact Luna without silently changing Ambient primary/fast selections.

`NativeCodexTransport` reuses `CodingAgentRuntime` trusted commands and managed state directories. Its public field is `runtime`, with methods `generate(selection: ResolvedModel, messages, tools) -> LLMResult` , `discover_models() -> list[dict]`, and `model_availability() -> list[dict]`. Web UI, Profiles, remote messages, and model output cannot specify executables, shells, auth locations, environments, or CLI flags. API Provider credentials do not enter native processes. Pin verified Linux execution profiles with separate adapters for the official `0.145.0` and `0.159.3` app-server protocols. Windows and other execution platforms fail safely before spawning native transport processes. The independent Windows Host 0.159.2 CLI capability probe and Coding Agent model save do not establish Ambient native primary-model support or expand version/platform coverage. Missing installation/login, unavailable models, and unsupported version/config/model fail safely, without CLI exec, API proxy, or model fallback.

The pinned 0.145.0 CLI does not support `--ignore-user-config`; native transport must neither pass that flag nor ignore CLI argument errors. Equivalent isolation requires managed Codex state `config.toml` to be absent. Any file, directory, or symlink is rejected before reading or overwriting it. Native auth remains owned by existing CLI login operations without reading, copying, or rewriting its contents. Each generation creates an ephemeral thread/turn in a fresh temporary inference cwd outside project ancestry, using read-only policy, `project_doc_max_bytes=0`, all mandatory trusted config overrides, and explicit `environments=[]`, `dynamicTools=[]`, and `selectedCapabilityRoots=[]`. Before threads or model inference, `config/read` validates actual effective configuration and empty MCP. Unsupported restrictions, mismatched configuration, or launch argument errors fail closed without falling back to user configuration or ignoring unknown flags.

Before `turn/start`, validate the actual `thread/start` response's required safety fields: model is the exact selection, modelProvider is `openai`, resolved cwd equals this operation's owned temporary directory, approvalPolicy is `never`, and sandbox is a read-only policy. Missing required fields or mismatching policies prevent inference; nonempty returned `runtimeWorkspaceRoots` or `instructionSources` also fail. Handle omitted optional fields according to the corresponding pinned protocol rather than inventing mandatory unknown/optional properties. Successful fake-process replies must include actual required fields; a thread/model-only fixture cannot establish effective safety settings.

The Provider name alone does not prove the official inference endpoint. Pinned 0.145 [config validation](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/config/src/config_toml.rs) rejects a reserved `model_providers.openai` definition, and [Provider merging](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/model-provider-info/src/lib.rs) preserves built-ins. However, top-level `openai_base_url` overrides the built-in endpoint even with ChatGPT login. Before any account/thread/turn request, native mode inspects every raw `config/read` layer, requiring each layer and config to be objects. Any explicit non-null `openai_base_url` or `chatgpt_base_url` value is rejected, including an official address, an empty string, an incorrect type, or a lower-precedence or disabled layer; only omission or null preserves builtin defaults. Typed config, Provider name, and login type cannot substitute for routing validation; no UI/API override, silent ignoring, or fallback is offered. Process environments retain the Runtime safety whitelist without describing environment variables as official endpoint overrides unless pinned-version source proves support. Native login remains CLI-owned without credential copying. Synthetic-layer regression tests reject these routes before any account/thread/turn request.

Do not assume the `turn/start` RPC reply always precedes notifications. Official 0.145 [turn processing](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/app-server/src/request_processors/turn_processor.rs) submits core input before constructing its reply, while [event processing](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/app-server/src/bespoke_event_handling.rs) independently emits `turn/started`. This is an ordering risk supported by source, not a real-model race reproduced in this run. Only while this operation's `turn/start` awaits a reply and its thread is already fixed may the first own-thread `turn/started` bind a turn ID once; the subsequent RPC reply must match. When the reply arrives first, it binds normally and later notifications must match. Different threads, turn rebinding, conflicting reply IDs, unknown turns without a pending request, and items before turn binding fail safely. Actual `thread/start` source replies before emitting `thread/started`; turn-order compatibility does not relax other identity checks.

Include complete history envelopes: system/user/assistant/tool messages, previous function choices, and tool results. Do not rely on resumable native threads, old prompts, or project instructions. Existing `ModelSelection`/`ResolvedModel` snapshots retain default/fast/session choices, preventing later settings edits from changing running Runs. Flags accepted by the independent Windows 0.159.2 exec probe do not establish 0.145.0 app-server support.

This native contract currently transports text-only JSON history. It does not convert images or other multimodal content into native input or claim vision support. Unknown catalog vision capabilities remain unknown; a model name, catalog entry, or independent CLI probe does not establish support.

Before launch, disable Web/search, apps, skills, MCP, hooks, notify, memory, goals, multi-agent, interactive user input, and filesystem/terminal workspace side effects, verifying that the pinned profile supports the restrictions. Legacy exact Luna metadata may require `code_mode_only`. The legacy profile allows internal in-memory Plan and verified CodeMode exec/wait exposing only the Plan facade; it uses bare V8, not Node/Deno, without imports, IO, or file/network/process capabilities. This computation neither executes Ambient tools nor produces workspace side effects or user-visible tool calls. Acceptance requires zero workspace side effects, not zero native tool items. The modern profile keeps its external CodeMode host disabled as specified above. Unknown native tool requests, side-effect approvals, disallowed execution items, and versions/config/models whose restrictions cannot be confirmed terminate and clean up without approval or speculative continuation.

Only native turn outputSchema uses the JSON `{text, tool_calls:[{name, arguments}]}` envelope. Text is a string, arguments is a JSON-containing string, and both the outer and per-call strict schemas use `additionalProperties=false`. Names belong to registered tools supplied for this request. The adapter parses each arguments string exactly once into an object, then validates all calls against the complete matching `tool.function.parameters` JSON Schema before returning any call; required, type, enum, and other constraints cannot be skipped. Never resolve external/network `$ref` or access the network for validation; internal defs are allowed only after local validation. This unpublished native preset retains no object-arguments wire compatibility.

The adapter serializes validated objects into existing `LLMResult.tool_calls[*].function.arguments` JSON strings. Ambient's public ToolGateway logical arguments remain objects, and generic API Provider wire behavior remains unchanged. Execution stays solely in Ambient's registered tool loop, `ToolGateway`, Capability authorization, Runs, approvals, idempotency keys, and audit. Without supplied tools, tool_calls must be empty. Invalid outer/argument JSON, non-object arguments, unknown tools, argument-schema violations, oversized output, and missing final schema replies return redacted safe errors without partial calls, automatic repair, another turn, replay, or executing commands from text. A native internal turn is not one underlying model request; the whole Agent flow must not be claimed to make only one model request.

Use a fixed server-side default 90-second deadline, injectable in tests, and bounded stdio. Success, failure, timeout, and cancellation await owned Linux app-server process-group closure and temporary-directory removal without orphans, preserving cancellation upward. An exited parent cannot skip group cleanup; descendants ignoring SIGTERM still require bounded forced termination. Repeated cancellation cannot abandon process creation or cleanup ownership. Operate only on this inference's created group, stop processes before deleting temporary cwd, and verify descendant exit with owned parent/child regression processes without models. Windows does not infer zero orphans from taskkill or parent wait; native transport rejects that platform. Errors contain bounded codes/messages only, without stdout/stderr, auth, history prompts, paths, device codes, or reply bodies. Normalize usage only when upstream supplies it; do not invent missing statistics.

Failure diagnostics use fixed, allowlisted reason/category values to distinguish configuration, version, model metadata, input/output budgets, thread policy, identity, native items, notifications, callbacks, protocol, and final-output rejection. Known but disallowed items/methods in the pinned schema may map to predefined safe enums; unknown methods/types map only to a fixed unsupported category. Never reflect raw names, parameters, upstream errors, content, or paths into reasons/messages/audit. Existing auditing preserves the allowlisted reason from `LLMConfigError`, distinguishing profile rejection from a real tool-capability test failure; generic `llm_capability_unsupported` alone does not prove missing tool support. Diagnostics preserve strict failure closing, the safety profile, allowed internal Plan/CodeMode, and no automatic retry; they do not replay failed model requests. Injected-protocol Red/Green first verifies reasons, unknown-method/type and private-content redaction, fixed-reason audit retention, owned-process cleanup, and zero extra inference, before diagnostics are used in a separate user-authorized runtime attempt.

First write deterministic failing tests with injected process/protocol boundaries: preset/credential rejection, independent discovery and login, complete history, text/registered function choices, exactly-once native arguments-string parsing, rejection of non-object/bad JSON/type/required/enum violations, no partial calls before all validate, no external-ref resolution with local-defs validation, strict outer/per-call fields, unknown tools/unsupported versions or non-Linux platforms, config.toml file/directory/symlink rejection without reads/writes, project-ancestry isolation, actual config/read and empty-MCP validation, thread replies with real required fields and rejection of wrong model/provider/cwd/approval/sandbox, rejection of nonempty optional roots/instructionSources while accepting normal omission, notification-first/reply-first turns with pending own-thread first binding, rejection of wrong-thread/rebinding/conflicting replies/no-pending/unbound items, restricted configuration and allowed Plan-only internal items, timeout/cancellation/during-spawn/repeated-cancellation cleanup, selection snapshots, existing API compatibility, and no automatic retry. Also use owned Linux parent/child processes without models to verify zero orphans when the parent exits first or descendants ignore SIGTERM. Unit tests do not call real models. Separately create the Provider through the web UI with exact `gpt-5.6-luna` default/fast selections, run bounded Chat, durable Run, and App flows, then test remote recovery/revocation. See the public subset in [backend UML](/en/architecture/uml.md). The independent CLI probe in [public acceptance](../verification/remote-workspace-production-2026-10-01.md) does not replace primary Provider acceptance.

For `llm_capability_unsupported`, the frontend displays a general localized message: Chinese “所选模型或运行配置不支持这次 Agent 请求。” and English “The selected model or runtime configuration does not support this agent request.” Existing error codes, event handling, and dialog flows remain. Native profile or protocol rejection is no longer described as tool-only incompatibility. Frontend localization regression tests first verify both languages and compatibility for other Provider errors, followed by broader regression. The pure helper is called by App's existing event handler; helper tests do not establish real-event or public acceptance.

Handle ordinary pinned 0.145 `error` notifications using the generated protocol: required `error`, `threadId`, `turnId`, and boolean `willRetry`, matching this operation's already-bound own thread/turn. `TurnError` requires a string `message`; optional `additionalDetails` is string/null, and `codexErrorInfo` is a fixed enum object/string or null. Validate content types without extracting, displaying, or auditing raw `message`/`additionalDetails`. Unknown shapes, incorrect types, and unknown enum strings still fail closed. `willRetry=true` reports an official upstream retry inside the current turn. Continue waiting within the existing 90-second deadline and byte/message budgets without adding RPCs, threads, Runs, model-request replays, or Workspace tool execution. `willRetry=false` is terminal: classify only fixed `codexErrorInfo`/known HTTP statuses into redacted provider, rate, or timeout errors, then clean up the owned process; never infer causes from error content.

Fixed `CodexErrorInfo` strings are `contextWindowExceeded`, `sessionBudgetExceeded`, `usageLimitExceeded`, `serverOverloaded`, `cyberPolicy`, `internalServerError`, `unauthorized`, `badRequest`, `threadRollbackFailed`, `sandboxError`, and `other`. Known single-key object variants are `httpConnectionFailed`, `responseStreamConnectionFailed`, `responseStreamDisconnected`, and `responseTooManyFailedAttempts` (optional inner uint16/null `httpStatusCode`), or `activeTurnNotSteerable` (required inner `turnKind=review|compact`). Regression fixtures use actual required fields and verify no new RPCs for same-turn retry status, sanitized terminal mapping, rejection of wrong thread/turn or unknown shapes/strings, no raw error content in audit, and owned cleanup. MCP, dynamic, shell, and other disallowed tools remain rejected. This separate protocol-compatibility fix does not establish the actual AlignData failure cause; a new user-authorized runtime attempt must confirm it separately.

Terminal normalization additionally uses existing `llm_auth_failed` for fixed `unauthorized` or known HTTP 401/403, matching native independent-login errors without interpreting error content. `usageLimitExceeded`/`sessionBudgetExceeded` or known HTTP 429 map to rate; known HTTP 408/504 map to timeout. Other known enums/variants, including `serverOverloaded`, and valid omitted/null info map to provider. Auth is only fixed metadata classification into an existing error code, without added tools, login actions, model fallback, or retries.

## Coverage

Friendly presets cover OpenAI, Anthropic, Google, xAI, Mistral, Cohere, DeepSeek, OpenRouter, Groq,
Together, Fireworks, Cerebras, Perplexity, NVIDIA, Hugging Face, Vercel, MiniMax Global, MiniMax China, Kimi, Qwen, Doubao,
GLM, SiliconFlow, Azure, Bedrock, Vertex AI, Databricks, watsonx, Cloudflare, Ollama, LM Studio, vLLM,
llama.cpp, TGI, and Xinference. Generic entries support OpenAI Chat, OpenAI Responses,
Anthropic-compatible endpoints, LiteLLM Proxy, and custom LiteLLM providers.

Local settings retain the single-user trust model. Remote clients through Cloud nodes also require
the appropriate authorization scope; control-only cannot modify Providers. A custom endpoint makes the backend access the supplied HTTP(S) address, so expose
the settings UI only on trusted networks.
