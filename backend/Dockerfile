# ---------- Stage 1: fetch latest opencode binary via official installer ----------
FROM debian:bookworm-slim AS opencode
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tar \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# The official installer writes the binary to $HOME/.opencode/bin/opencode.
RUN curl -fsSL https://opencode.ai/install | bash

# ---------- Stage 2: build the locked Widget verifier runtime ----------
FROM node:22-bookworm-slim AS widget-verifier
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

# ---------- Stage 3: Python runtime ----------
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libstdc++6 \
    sqlite3 \
    tar \
    xz-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Pull in opencode binary from the builder stage so the runtime image stays slim.
COPY --from=opencode /root/.opencode/bin/opencode /usr/local/bin/opencode

# The staging verifier uses Node's VM plus Babel standalone. Copy only the
# runtime pieces from the lockfile-backed build stage, not all frontend deps.
COPY --from=widget-verifier /usr/local/bin/node /usr/local/bin/node
COPY --from=widget-verifier /build/frontend/node_modules/@babel/standalone /app/frontend/node_modules/@babel/standalone

# Copy only the necessary project configurations first to leverage layer caching
COPY pyproject.toml README.md /app/

# Install dependencies (only production packages)
RUN uv pip install --system .

# Copy backend codebase
COPY backend /app/backend/
COPY forward /app/forward/
COPY scripts /app/scripts/

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI application
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
