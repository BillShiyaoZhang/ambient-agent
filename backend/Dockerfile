# ---------- Stage 1: fetch and verify the release-pinned OpenCode binary ----------
FROM debian:bookworm-slim AS opencode
ARG OPENCODE_VERSION=1.17.18
ARG TARGETARCH
ARG OPENCODE_SHA256_AMD64=e149d32ee5667c0cd5fb84d0bf8393b312e93782eeb4d74d29bbb0392de7133c
ARG OPENCODE_SHA256_ARM64=db9b53eae485da969a0a855bca465f9901dd84676384f724f320e3ccc5a9b107
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tar \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH}" in \
      amd64) OPENCODE_ARCH="x64"; OPENCODE_SHA256="${OPENCODE_SHA256_AMD64}" ;; \
      arm64) OPENCODE_ARCH="arm64"; OPENCODE_SHA256="${OPENCODE_SHA256_ARM64}" ;; \
      *) echo "Unsupported OpenCode architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL \
      "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${OPENCODE_ARCH}.tar.gz" \
      -o /tmp/opencode.tar.gz \
    && echo "${OPENCODE_SHA256}  /tmp/opencode.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin opencode \
    && test "$(/usr/local/bin/opencode --version)" = "${OPENCODE_VERSION}" \
    && rm /tmp/opencode.tar.gz

# ---------- Stage 2: build the locked Widget verifier runtime ----------
FROM node:22-bookworm-slim AS widget-verifier
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

# ---------- Stage 3: build the pinned Codex-to-ACP bridge ----------
FROM node:22-bookworm-slim AS coding-agent-acp
WORKDIR /opt/coding-agent-acp
COPY coding-agent-acp/package.json coding-agent-acp/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

# ---------- Stage 4: Python runtime ----------
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

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
    && pip install --no-cache-dir uv==0.9.15

# Pull in opencode binary from the builder stage so the runtime image stays slim.
COPY --from=opencode /usr/local/bin/opencode /usr/local/bin/opencode

# The staging verifier uses Node's VM plus Babel standalone. Copy only the
# runtime pieces from the lockfile-backed build stage, not all frontend deps.
COPY --from=widget-verifier /usr/local/bin/node /usr/local/bin/node
COPY --from=widget-verifier /build/frontend/node_modules/@babel/standalone /app/frontend/node_modules/@babel/standalone
# Codex itself stays managed/on-demand. This pinned bridge only maps its
# app-server protocol into the same ACP boundary used by native ACP agents.
COPY --from=coding-agent-acp /opt/coding-agent-acp /opt/coding-agent-acp

# Copy only dependency metadata first to leverage layer caching.
COPY pyproject.toml uv.lock README.md LICENSE /app/

# Install the exact locked production dependency set. The repository is an
# application rather than a Python distribution, so its source is copied below
# and is intentionally not installed as a package.
RUN uv sync --locked --no-dev --no-install-project

# Copy backend codebase
COPY backend /app/backend/
COPY scripts /app/scripts/

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI application
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
