# Project Structure

This page explains the responsibility of each top-level directory and where a change normally belongs.

## Top-level directories

```text
ambient-agent/
├── .devcontainer/          # Development workspace image and Neo4j sidecar orchestration
├── backend/               # FastAPI composition root and infrastructure adapters
│   ├── agent/             # Routing, reducer, tools, providers, prompts, evaluation
│   └── capabilities/      # Capability ontology, grants, policy, system capability catalog
├── frontend/              # React 19 + TypeScript + Vite workspace
│   └── src/
│       ├── components/    # Workspace, chat, App Center, tasks, settings, Widget host
│       ├── lib/           # Pure window-state and message-merging logic
│       ├── services/      # HTTP, WebSocket, Run, LLM, theme, and i18n clients
│       └── types/         # Run event types generated from the backend contract
├── docs/                  # Docsify docs; Chinese at root, English under docs/en/
├── scripts/               # Contract generation, UML/docs/Widget checks, evaluation
├── tests/backend/         # Pytest backend tests
├── tests/frontend/        # Vitest + Testing Library frontend tests
├── workspace/             # Local runtime data, ignored by Git
├── docker-compose.yml     # Local Neo4j + backend + frontend orchestration
└── pyproject.toml         # Python versions, dependencies, and Ruff configuration
```

## Backend module map

| Module | Responsibility |
| --- | --- |
| `backend/main.py` | FastAPI lifecycle, REST/WebSocket routes, and service assembly |
| `backend/run_service.py` | `RunStore`, Run claims/fencing, interactions, event stream, adapter dispatch |
| `backend/agent/durable_workflow.py` | Explicit phase reducer for `internal_agent` Runs: planning, confirmation, execution, verification, publication |
| `backend/agent/router.py` | Classifies user input into an `IntentPlan` |
| `backend/agent/harness.py` | Routing and bounded read-only Converse helper used by the reducer; not a second execution loop |
| `backend/capabilities/ontology.py` | Stable Widget capability categories and scope contracts |
| `backend/capabilities/models.py` | Grant, alignment-proposal, and Runtime Contract value objects |
| `backend/capabilities/policy.py` | Default-deny unified App capability authorizer |
| `backend/capabilities/catalog.py` | Structured system capability projections for each Agent role |
| `backend/capabilities/files.py` | `app://data/` file adapter with path and atomic-write boundaries |
| `backend/graph_db.py` | SQLite storage for schemas, nodes, edges, effects, and mutation history |
| `backend/schema_*` | Produces schema + capability alignment proposals and verifies that staging cannot expand the approved contract |
| `backend/app_manager.py` | Artifact I/O and safe paths under `workspace/apps/<app-id>/` |
| `backend/app_store.py` | Unified catalog and layout for apps, skills, and MCP capabilities |
| `backend/opencode_service.py` | Generates Widgets through ACP in isolated staging, then verifies and promotes artifacts |
| `backend/llm_config.py` | Provider profiles, credentials, model catalog, and default/session selections |
| `backend/workspace_storage.py` | Workspace file storage for session messages, Canvas, and audit logs |

## Frontend module map

| Module | Responsibility |
| --- | --- |
| `App.tsx` | Top-level coordination for sessions, messages, Canvas, Widgets, and global dialogs |
| `AppWorkspace.tsx` | Desktop chrome, window move/resize/snap/maximize, and responsive modes |
| `AppCenter.tsx` | Unified capability catalog, search, folders, ordering, and UI generation entry point |
| `TaskDrawer.tsx` | Run history, pending interactions, and runtime controls |
| `SandboxWidget.tsx` | Compiles `controller.js`, constructs a minimal approved-grant `ambient` membrane, and renders React Widgets |
| `services/runs.ts` | Run REST client, versioned event stream, and cursor recovery after disconnects |
| `lib/windowManager.ts` | Canvas V3 migration, normalized window coordinates, and layout algorithms |

## Workspace data

```text
workspace/
├── sessions/<id>.json       # Session metadata and messages
├── canvas.json               # Canvas V3 window configuration
├── audit_logs.jsonl          # LLM audit records
├── graph.db                  # SQLite test adapter / opt-in Neo4j migration source
├── llm/config.json           # Provider profiles and selections, without secrets
├── llm/secrets.json          # Provider credentials
├── apps/<app-id>/            # manifest.json V2, controller.js, README.md
│   └── data/                 # Grant-constrained App-private Widget file root
├── apps/.ambient/app_records.db
└── .ambient/runs.db          # Runs, steps, events, interactions
```

`workspace/` is local state and must not be committed. The deployed canonical ontology and context graph live in Neo4j; App-private runtime data lives under `workspace/apps/<app-id>/data/`. The new version does not load V1 Apps or old three-file Widgets; migration explicitly repeats schema/capability alignment and publishes Manifest V2.

## Common change entry points

- Add or modify an API: update the relevant architecture/API docs and tests first, then change `backend/main.py` and the service layer.
- Change Agent behavior: consider `IntentPlan`, durable phases, and the Run event contract together.
- Change the Widget API: update `SandboxWidget.tsx`, backend enforcement, both SDK language pages, and tests.
- Change Widget external authority: update the Capability Ontology and security architecture first, then add policy/contract tests; never add route-local exceptions.
- Change a core class or model: update both UML language pages and run `scripts/verify_uml.py`.
- Change documentation pages: the Chinese path and its English path under `docs/en/` must correspond exactly; run `scripts/verify_docs.py`.
