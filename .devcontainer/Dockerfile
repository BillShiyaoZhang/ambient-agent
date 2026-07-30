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

# ---------- Stage 2: dev environment ----------
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/root/.local/bin:${PATH}"

# Install SQLite, curl, wget, git, build-essential, xz-utils, and procps (for ps command)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    sqlite3 \
    libsqlite3-dev \
    build-essential \
    ca-certificates \
    xz-utils \
    procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Node.js (v22.18.0 LTS, the minimum accepted by the locked Babel runtime)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then NODE_ARCH="x64"; else NODE_ARCH="arm64"; fi && \
    curl -fsSL https://nodejs.org/dist/v22.18.0/node-v22.18.0-linux-${NODE_ARCH}.tar.xz -o node.tar.xz && \
    tar -xJf node.tar.xz -C /usr/local --strip-components=1 && \
    rm node.tar.xz

# Keep the development execution boundary identical to the production image.
# Codex itself remains managed/on-demand; this package only bridges its
# app-server protocol to ACP at the path resolved by CodingAgentRuntime.
WORKDIR /opt/coding-agent-acp
COPY coding-agent-acp/package.json coding-agent-acp/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
WORKDIR /

# Install the same pinned uv release used by CI and the production image.
RUN pip install --no-cache-dir uv==0.9.15

# Pull in opencode binary from the builder stage so it is available on PATH.
COPY --from=opencode /usr/local/bin/opencode /usr/local/bin/opencode
