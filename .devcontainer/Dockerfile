# ---------- Stage 1: fetch latest opencode binary via official installer ----------
FROM debian:bookworm-slim AS opencode
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tar \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# The official installer writes the binary to $HOME/.opencode/bin/opencode.
RUN curl -fsSL https://opencode.ai/install | bash

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

# Install Node.js (v22.14.0 LTS) and npm directly from pre-compiled binaries
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then NODE_ARCH="x64"; else NODE_ARCH="arm64"; fi && \
    curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-${NODE_ARCH}.tar.xz -o node.tar.xz && \
    tar -xJf node.tar.xz -C /usr/local --strip-components=1 && \
    rm node.tar.xz

# Install uv globally
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Pull in opencode binary from the builder stage so it is available on PATH.
COPY --from=opencode /root/.opencode/bin/opencode /usr/local/bin/opencode