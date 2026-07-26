FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    WIDGET_FRAME_HOST=0.0.0.0 \
    WIDGET_FRAME_PORT=8001

WORKDIR /app
COPY widget-runtime/package.json widget-runtime/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY --chown=node:node \
  widget-runtime/controller_facade.mjs \
  widget-runtime/frame_server.mjs \
  widget-runtime/frame_shell.css \
  widget-runtime/frame_shell.html \
  widget-runtime/frame_shell.mjs \
  widget-runtime/presentation_context.mjs \
  ./

USER node
EXPOSE 8001
CMD ["node", "frame_server.mjs"]
