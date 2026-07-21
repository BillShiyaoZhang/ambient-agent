# System and Request Flow

## 1. Runtime components

```mermaid
flowchart LR
    Browser[React workspace] -->|REST| API[FastAPI]
    Browser <-->|Chat / Graph / Run WebSockets| API
    API --> Workspace[Session, Canvas, audit files]
    API --> RunStore[.ambient/runs.db]
    API --> Graph[Neo4j canonical KG]
    API --> Apps[workspace/apps]
    API --> LLM[LLM provider]
    API --> External[MCP / coding agent / local tools]
```

`backend/main.py` is the assembly point. It creates `WorkspaceStorage`, `LLMConfigStore`, the configured graph adapter, `AppManager`, `AppStoreService`, `RunStore`, `RunCoordinator`, and `DurableAgentWorkflow`. During application startup it resumes runnable tasks and cleans up stale staging artifacts.

## 2. How a user request executes

1. The frontend sends a message through `/ws/chat`.
2. The backend stores a `ChatMessage`, resolves the current session language, model, and coding-agent snapshot, and submits an `internal_agent` Run to `RunCoordinator`.
3. The Coordinator persists the Run and manages the execution lane for that session.
4. `DurableAgentWorkflow` calls `IntentRouter` to create an `IntentPlan`, then advances explicit phases.
5. Read-only conversation or queries may finish directly. Graph mutations, composite tasks, and Widget create/modify flows pass through planning, required user interactions, preflight, execution, and verification.
6. Each step uses claims, lease epochs, and Run versions to reject stale worker commits. Visible events are stored in `run_events` and pushed through `/ws/runs`.
7. The frontend projects Run state into chat, the Task Drawer, App Center, and workspace.

The old in-memory Agent loop and Widget DAG are no longer production paths. `AgentOrchestrator` only provides routing and bounded read-only Converse helpers; the Run control plane owns execution.

## 3. Widget creation and loading

Widgets enter through two paths:

- Conversation returns `<ambient-widget>`: `AgentParser` extracts one `<js-script>`, and `AppManager` stores `controller.js` plus a manifest.
- App create or modify flow: the durable workflow asks the selected OpenCode or Codex backend to generate a controller in staging. It promotes the artifact to the live app only after syntax, safety-rule, and schema checks. Failure or missing approval does not overwrite the current artifact.

The frontend fetches an app from `/api/apps/{id}`. `SandboxWidget` transpiles the controller with Babel and injects React, the `ambient` API, and system components. This contains rendering failures but is not a security boundary for hostile JavaScript.

## 4. Data and communication responsibilities

| Channel/storage | Purpose |
| --- | --- |
| REST `/api/sessions`, `/api/canvas` | Session and Canvas CRUD |
| REST `/api/runs`, `/api/run-interactions` | Run listing, cancellation, retry, reconciliation, and user decisions |
| REST `/api/apps`, `/api/app-store` | App artifacts and unified capability catalog |
| REST `/api/coding-agents` | Coding-agent availability and default selection |
| REST `/api/graph/mutate` | Graph mutations after backend preflight |
| `/ws/chat` | Chat messages, compatibility projections, and Widget Graph subscriptions/commands |
| `/ws/runs` | Recoverable stream with sequence, event ID, and stream epoch |
| `workspace/sessions/*.json` | Sessions and messages |
| `workspace/.ambient/runs.db` | Runs, steps, interactions, and canonical events |
| Neo4j | Canonical ontology entities, context records, graph edges, effects, and mutation history |
| `workspace/graph.db` | Explicit SQLite test adapter and opt-in migration source only |

## 5. Security and consistency principles

- Provider secrets are not returned to the frontend and live in a Git-ignored workspace file.
- The Coding Agent Runtime uses trusted built-in adapters, installs CLIs on demand into a dedicated persistent volume, and normalizes installation, authentication, dynamic model discovery, model binding, and execution state. Codex uses a container device-code login and its own ChatGPT subscription, and obtains the models available to that account through the official app-server `model/list` method; OpenCode references a model binding from the central Provider Registry. The backend never passes Ambient provider secrets or bindings to native-mode Codex.
- Docker Compose relaxes the default seccomp filter for unprivileged user namespaces so Codex can retain its bubblewrap `workspace-write` sandbox inside the container boundary. It does not add `SYS_ADMIN` or switch Codex to `danger-full-access`.
- The backend image includes Node.js and the `@babel/standalone` verifier runtime pinned by the frontend lockfile. A coding agent's `controller.js` is promoted from staging only after syntax checks, forbidden host/network-global checks, and restricted-VM execution. A missing verifier fails closed instead of publishing unverified code.
- The coding-agent generation contract explicitly forbids `fetch`, browser host globals, and undeclared network access. On staging validation failure, the adapter returns the latest bounded diagnostic to the same agent for up to three in-place repair rounds, revalidating after every round. The Run fails only after the repair budget is exhausted, and invalid code is never published. This keeps independent manifest and controller failures that surface sequentially inside one repair loop.
- Graph mutations must pass canonical-ontology preflight and commit atomically in one Neo4j transaction.
- MCP, tool, and coding-agent authorization and sandbox policy are enforced by the backend. Omitting a frontend API is not authorization.
- Effectful durable steps use effect/idempotency records, interactions, and fencing to avoid duplicate commits during recovery or concurrency.
- Run events are a versioned contract; the frontend preserves unknown events for forward compatibility.

Continue with [Durable Runs](/en/architecture/runs.md), [Agent Harness](/en/agent/harness.md), [Widget Architecture](/en/architecture/apps.md), or [Graph Database](/en/architecture/graph-db.md).
