# Quick Start

## Prerequisites

- Docker Desktop; or local Python 3.11–3.13, `uv`, Node.js, and npm. The host Codex bridge also requires local Python and `uv`.
- VS Code with the Dev Containers extension when using the development container.

## Option 1: Docker Compose

```bash
git clone <repository-url>
cd ambient-agent
cp .env.example .env
docker compose up --build
```

Open `http://localhost:5173`. The backend API is available at `http://localhost:8000`.

Docker Compose also starts Neo4j for the canonical knowledge graph; its Browser is available at `http://localhost:7474`. Change `NEO4J_PASSWORD` before exposing the stack beyond local development. To import an existing `workspace/graph.db`, set `GRAPH_MIGRATE_SQLITE=1` for one startup and then set it back to `0`.

`.env` contains process-level settings such as coding-agent commands and timeouts. Configure LLM providers, credentials, default models, and the OpenCode/Codex choice in the app's “Models & Providers” UI. Provider credentials are stored in the Git-ignored `workspace/llm/secrets.json`, not in `.env`.

Codex is not installed in the Docker container. To reuse the host Codex login and ChatGPT subscription:

1. Confirm that `codex login status` succeeds on the host.
2. Run `openssl rand -hex 32` and put the result in `.env` as `CODEX_HOST_BRIDGE_TOKEN`.
3. From the repository root, start `uv run python -m scripts.codex_host_bridge` and keep it running.
4. Start Docker Compose and select Codex in the UI; its card reports the host bridge and login status.

The bridge listens only on `127.0.0.1:8765` by default, requires a bearer token, and accepts only randomized staging directories created by the backend under the shared `workspace/apps` directory. No Codex credentials are stored in the container.

## Option 2: Dev Container

1. Open the repository in VS Code and run **Dev Containers: Reopen in Container**.
2. Dev Containers starts both the development workspace and a Neo4j sidecar from `.devcontainer/docker-compose.yml`; the `postCreateCommand` runs `uv sync` and installs npm dependencies for `frontend/` and `docs/`.
3. Start the backend and frontend separately in development-container terminals:

```bash
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm run dev
```

The workspace already sets `GRAPH_DATABASE_BACKEND=neo4j` and reaches the sidecar at the container-network address `bolt://neo4j:7687`. Neo4j uses the development-only credentials `neo4j` / `ambient-agent-dev` and persists data in a dedicated Compose volume.

The Dev Container forwards workspace ports 8000, 5173, and 5174 plus Neo4j Browser/Bolt ports 7474 and 7687. The Browser is available at `http://localhost:7474`; add a port in the Ports panel if the IDE does not forward it automatically. Closing the Dev Container stops this Compose stack without deleting the Neo4j data volume.

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
