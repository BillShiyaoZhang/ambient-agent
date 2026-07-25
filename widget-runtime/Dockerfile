FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    HOME=/home/widget \
    CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    WIDGET_RUNTIME_SOCKET_PATH=/run/ambient-widget-runtime/runtime.sock

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && printf '%s\n' 'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
      > /etc/apt/apt.conf.d/keep-cache \
    && apt-get -o Acquire::Retries=5 update \
    && widget_apt_ok=0 \
    && for widget_apt_attempt in 1 2 3 4 5; do \
      if apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        chromium \
        chromium-sandbox \
        fonts-noto-color-emoji \
        fonts-noto-cjk; then \
        widget_apt_ok=1; \
        break; \
      fi; \
      sleep 2; \
    done \
    && test "$widget_apt_ok" = 1 \
    && groupadd --gid 10001 widget \
    && useradd --uid 10001 --gid 10001 --home-dir /home/widget --shell /usr/sbin/nologin widget \
    && mkdir -p /app /home/widget /run/ambient-widget-runtime \
    && chown -R 10001:10001 /app /home/widget /run/ambient-widget-runtime

WORKDIR /app
COPY --chown=10001:10001 widget-runtime/package.json widget-runtime/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY --chown=10001:10001 \
  widget-runtime/runtime.mjs \
  widget-runtime/serial_task_queue.mjs \
  ./

USER 10001:10001
CMD ["node", "runtime.mjs"]
