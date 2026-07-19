# Quick Start

## Prerequisites

- Docker Desktop; or local Python 3.11–3.13, `uv`, Node.js, and npm.
- VS Code with the Dev Containers extension when using the development container.

## Option 1: Docker Compose

```bash
git clone <repository-url>
cd ambient-agent
cp .env.example .env
docker compose up --build
```

Open `http://localhost:5173`. The backend API is available at `http://localhost:8000`.

`.env` contains process-level settings such as OpenCode configuration. Configure LLM providers, credentials, and default models in the app's “Models & Providers” UI. Credentials are stored in the Git-ignored `workspace/llm/secrets.json`, not in `.env`.

## Option 2: Dev Container

1. Open the repository in VS Code and run **Dev Containers: Reopen in Container**.
2. The `postCreateCommand` runs `uv sync` and installs npm dependencies for `frontend/` and `docs/`.
3. Start the backend and frontend separately:

```bash
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm run dev
```

The Dev Container declares ports 8000, 5173, and 5174 for forwarding. Add them in the Ports panel if the IDE does not forward them automatically.

## Option 3: Local Development

```bash
uv sync
npm --prefix frontend install
npm --prefix docs install
```

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
