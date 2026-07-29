# Quick Start

## Prerequisites

- Docker Desktop; or local Python 3.11–3.13, `uv`, Node.js, and npm.
- VS Code with the Dev Containers extension when using the development container.

## Option 1: Docker Compose (Recommended)

```bash
git clone <repository-url>
cd ambient-agent
cp .env.example .env
docker compose up --build -d
```

Open `http://localhost:5173`. The backend API is available at `http://localhost:8000`.

The root `docker-compose.yml` is the production stack for everyday use. The frontend is compiled during the image build and served by a small Nginx container; running containers do not mount frontend/backend source, tests, or `node_modules`. The backend still mounts `workspace/` to persist sessions, Apps, configuration, and local credentials. Neo4j and managed coding-agent installations use named volumes.

Every port published by the production stack binds to `127.0.0.1` by default,
including the Frontend, Backend, Widget Frame, and the Neo4j Browser/Bolt ports
retained for local debugging. Other devices on the LAN cannot connect
directly. The Backend is currently a trusted-local control plane and does not
provide login or multi-user authentication, so do not use a Compose override
to bind these ports to `0.0.0.0`, and do not expose them directly through a
public or LAN reverse proxy.

For temporary access from another trusted computer, use authenticated SSH port
forwarding while keeping every Docker port on loopback:

```bash
ssh -N \
  -L 5173:127.0.0.1:5173 \
  -L 8000:127.0.0.1:8000 \
  -L 8001:127.0.0.1:8001 \
  user@docker-host
```

Open `http://localhost:5173` on the tunneled computer. Never forward or publish
Neo4j ports `7474` or `7687`. An SSH tunnel protects the network entry point;
it does not turn Ambient Agent into a service safe to share with untrusted
users.

The defaults establish explicit resource boundaries for an 8GB home computer:

| Service | Memory / CPU / PID limit |
| --- | --- |
| Neo4j | 1250MiB / 1 CPU / 256 PIDs |
| Backend (including on-demand coding-agent children) | 1GiB / 1.5 CPUs / 256 PIDs |
| Widget Runtime | 768MiB / 1 CPU / 192 PIDs |
| Widget Frame | 192MiB / 0.25 CPU / 32 PIDs |
| Frontend | 128MiB / 0.25 CPU / 32 PIDs |

Every container has equal memory and swap limits so sustained swapping cannot hide memory pressure, and container logs rotate automatically. The Backend uses a container init process to reap exited children created by coding agents and MCP servers. Neo4j uses a 512MiB maximum heap and a 256MiB page cache. Background Runs default to one global concurrent execution. The default iframe Workspace retains only the Active Widget plus at most one Warm Widget at steady state, with one additional Suspending Widget allowed during a transition. Only the pixel rollback path uses server-side Chromium, which keeps at most four contexts by default. Check that the host has enough memory before raising any of these values.

When upgrading, inspect the existing `.env`: an explicit
`WIDGET_RUNTIME_MAX_CONTEXTS=16` overrides the new Compose default. Change it
to `4` unless the Runtime's 768MiB memory and 192-PID limits were raised and
load-tested at the same time.

`VITE_WIDGET_UI_TRANSPORT` is embedded in the frontend build output.
`VITE_API_BASE_URL` currently controls only the isolated Widget client-runtime
ticket/socket address; the main workspace API still uses port `8000` on the
browser's current hostname. After changing either build variable, run
`docker compose up --build -d` instead of only restarting the container.

Docker Compose also starts Neo4j for the canonical knowledge graph; its Browser is available at `http://localhost:7474`. Change `NEO4J_PASSWORD` before exposing the stack beyond local development. To import an existing `workspace/graph.db`, set `GRAPH_MIGRATE_SQLITE=1` for one startup and then set it back to `0`.

`.env` contains process-level settings such as coding-agent commands and timeouts. Configure LLM providers, credentials, default models, and the OpenCode/Codex choice in the app's “Models & Providers” UI. Provider credentials are stored in the Git-ignored `workspace/llm/secrets.json`, not in `.env`.

Coding-agent CLIs are not all preinstalled in the image. Open “Models & Providers” to install Codex on demand; binaries and native credentials persist in the `coding_agent_data` volume. After installation, select “Sign in with ChatGPT,” open the device-code page, and enter the one-time code. The UI then loads the models available to the signed-in Codex account dynamically. Removing the volume removes both the managed CLI and its container login state. Both the production image and Dev Container pin and preinstall the Codex ACP bridge; when running the backend directly on a host, install `@agentclientprotocol/codex-acp` and point `CODEX_ACP_COMMAND` at its executable command. Rebuild the Dev Container after changing bridge configuration; restarting alone does not refresh image layers.

Provider connections and credentials remain centralized, while model bindings are scoped to each consumer. Ambient uses primary and fast roles; OpenCode inherits the Ambient primary model by default or selects a dedicated provider model; Codex uses its own native login and optional native model and never receives Ambient provider credentials.

The chat composer shows the `Ambient` primary model used for request understanding, routing, and planning. Code generation uses the separately displayed `Coding Agent` and its model; seeing an Ambient model name does not mean Codex uses that provider.

### Developing with Docker Compose

For source hot reload, layer the development override on the production baseline:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The override switches the Backend to Uvicorn reload and the Frontend to Vite HMR, then restores source, test, and isolated `node_modules` mounts. Neo4j, Widget Runtime, ports, data volumes, and protections such as the no-swap boundary continue to come from the production baseline. Because Vite/HMR itself needs more resources, the development Frontend receives a 768MiB, 1 CPU, and 128 PID limit. Do not use this mode when measuring end-user idle resource usage.

## Option 2: Dev Container

1. Open the repository in VS Code and run **Dev Containers: Reopen in Container**.
2. Dev Containers starts the development workspace, Neo4j sidecar, server-side Widget Runtime, and browser Widget Frame from `.devcontainer/docker-compose.yml`; the `postCreateCommand` runs `uv sync` and installs npm dependencies for `frontend/` and `docs/`. The Python environment lives in a container-only `python_env` volume instead of the bind-mounted project `.venv`, preventing macOS and Linux interpreters from overwriting one another.
3. Start the backend and frontend separately in development-container terminals:

```bash
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm run dev
```

The workspace already sets `GRAPH_DATABASE_BACKEND=neo4j` and reaches the sidecar at the container-network address `bolt://neo4j:7687`. Neo4j uses the development-only credentials `neo4j` / `ambient-agent-dev` and persists data in a dedicated Compose volume.

The Dev Container forwards workspace ports 8000, 5173, and 5174, the browser Widget Frame on port 8001, and Neo4j Browser/Bolt ports 7474 and 7687. The Widget Frame must be browser-reachable at `http://localhost:8001`; otherwise the server-side Runtime smoke test can still pass during generation while opening the Widget fails with `runtime_handshake_timeout`. Neo4j Browser is available at `http://localhost:7474`; add a port in the Ports panel if the IDE does not forward it automatically. Closing the Dev Container stops this Compose stack without deleting the Neo4j data volume.

## Option 3: Local Development

```bash
uv sync
npm --prefix frontend install
npm --prefix docs install
```

Local tests use the explicit SQLite compatibility adapter. For a local production-like backend, start Neo4j and set `GRAPH_DATABASE_BACKEND=neo4j`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE` before running Uvicorn.

Then use the same backend and frontend commands as the Dev Container. To preview the documentation:

```bash
npm --prefix docs run dev
```

The documentation site runs at `http://localhost:5174`.

## Configure the First Model

1. Open “Models & Providers” from the top-right workspace controls.
2. Create a provider and enter its type, API base, and credentials.
3. Test the connection or discover models.
4. Select a default model; optionally override it for an individual session.

Requests that require a model return an actionable LLM configuration error when no valid default model is configured.

## Verification Commands

```bash
uv run ruff check .
PYTHONPATH=. uv run pytest
uv run python scripts/verify_uml.py
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
```

After changing documentation structure or links, also run the documentation verifier:

```bash
uv run python scripts/verify_docs.py
```
