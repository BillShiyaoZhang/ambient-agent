# Quick Start

To keep development setups fully consistent and avoid package version conflicts, this project is fully containerized. You only need Docker installed to begin.

## 📦 Prerequisites

- **Docker Desktop** (to run containers)
- **VS Code** with the **Dev Containers** extension (recommended)

## 🚀 Step 1: Clone & Configure `.env`

Clone the repository to your local path:

```bash
git clone <repository-url>
cd ambient-agent
```

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Fill in your API configurations for Ollama, MiniMax, or OpenAI.

## 🛠️ Step 2: Develop using VS Code Dev Containers (Recommended)

This connects your IDE directly inside the container namespace:

1.  Open the project root folder in VS Code.
2.  Press `Cmd+Shift+P` (or `Ctrl+Shift+P`), select **`Dev Containers: Reopen in Container`**.
3.  The environment automatically installs Node.js 22, Python 3.11, `uv` sync, and package dependencies.
4.  **Start the Backend** (Terminal 1):
    ```bash
    uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
    ```
5.  **Start the Frontend** (Terminal 2):
    ```bash
    cd frontend
    npm run dev
    ```
6.  **Port Forwarding**: Ensure port `8000` is forwarded in VS Code.
7.  Browse `http://localhost:5173/` to start using the app.

## 🐳 Step 3: Run with Docker Compose

To launch the system instantly:

```bash
docker compose up --build
```

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:5173`

## 🧪 Tests Verification

Run tests inside the container terminal:

### Backend Checks

```bash
# Style check & auto-fix (Ruff)
uv run ruff check . --fix
uv run ruff format .

# Unit test suites (Pytest)
PYTHONPATH=. uv run pytest

# Check class diagram verification
uv run python scripts/verify_uml.py
```

### Frontend Checks

```bash
cd frontend
npm run lint
npm run test
```
