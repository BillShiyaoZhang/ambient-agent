import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as Babel from "@babel/standalone";
import { chromium } from "playwright-core";

import { SerialTaskQueue } from "./serial_task_queue.mjs";


const PROTOCOL_VERSION = 1;
const SOCKET_PATH =
  process.env.WIDGET_RUNTIME_SOCKET_PATH ??
  "/run/ambient-widget-runtime/runtime.sock";
const CHROMIUM_PATH =
  process.env.CHROMIUM_EXECUTABLE_PATH ?? "/usr/bin/chromium";
const MAX_CONTEXTS = boundedInteger(
  process.env.WIDGET_RUNTIME_MAX_CONTEXTS,
  16,
  1,
  64,
);
const MAX_MESSAGE_BYTES = 4 * 1024 * 1024;
const CURRENT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const HTM_PREACT_PATH = path.join(
  CURRENT_DIRECTORY,
  "node_modules",
  "htm",
  "preact",
  "standalone.umd.js",
);

const sessions = new Map();
let browser;
let shuttingDown = false;


function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, parsed));
}


function serialize(message) {
  const payload = Buffer.from(JSON.stringify(message), "utf8");
  if (payload.byteLength > MAX_MESSAGE_BYTES) {
    throw new Error("Widget Runtime message exceeds the byte limit");
  }
  return Buffer.concat([payload, Buffer.from("\n")]);
}


function send(socket, message, { droppable = false } = {}) {
  if (socket.destroyed || !socket.writable) return false;
  if (droppable && socket.writableNeedDrain) return false;
  socket.write(serialize(message));
  return true;
}


function runtimeError(code, message, classification = "operator") {
  return {
    type: "runtime_error",
    error: {
      code,
      message: String(message).slice(0, 4096),
      classification,
    },
  };
}


function browserArguments() {
  return [
    "--disable-background-networking",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-dev-shm-usage",
    "--disable-domain-reliability",
    "--disable-features=MediaRouter,OptimizationHints",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-default-browser-check",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
  ];
}


async function launchBrowser() {
  browser = await chromium.launch({
    executablePath: CHROMIUM_PATH,
    headless: true,
    chromiumSandbox: true,
    args: browserArguments(),
  });
  browser.on("disconnected", () => {
    if (shuttingDown) return;
    for (const session of sessions.values()) {
      send(
        session.socket,
        runtimeError(
          "chromium_disconnected",
          "The managed Chromium process exited",
          "operator",
        ),
      );
      void closeSession(session);
    }
    // The supervisor intentionally owns one pinned browser process. Once it is
    // gone, retaining a live socket server would make every later session fail
    // against a dead browser. Exit so Docker's bounded restart policy can
    // restore the complete runtime.
    setTimeout(() => process.exit(1), 250);
  });
}


function validateStart(message) {
  if (message.protocol_version !== PROTOCOL_VERSION) {
    throw new Error("Widget Runtime protocol version mismatch");
  }
  for (const field of [
    "session_id",
    "app_id",
    "manifest_revision",
    "grants_digest",
    "artifact_digest",
    "controller_source",
  ]) {
    if (typeof message[field] !== "string" || message[field].length === 0) {
      throw new Error(`Widget Runtime start.${field} is required`);
    }
  }
  const viewport = message.viewport;
  if (
    typeof viewport !== "object" ||
    viewport === null ||
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height)
  ) {
    throw new Error("Widget Runtime start.viewport is invalid");
  }
}


function transformController(source) {
  return Babel.transform(source, {
    presets: [["react", { runtime: "classic" }]],
    plugins: ["transform-modules-commonjs"],
    filename: "controller.js",
    sourceMaps: "inline",
  }).code;
}


async function installPageRuntime(page, session, transformedController) {
  await page.setContent(`<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      :root {
        color-scheme: dark;
        --accent: #2563eb;
        --widget-text: rgba(255,255,255,.9);
        --widget-muted: rgba(255,255,255,.55);
        --widget-border: rgba(255,255,255,.09);
        --widget-surface: rgba(30,41,59,.45);
        --widget-surface-soft: rgba(255,255,255,.035);
        --widget-control: #475569;
        --widget-input: rgba(15,23,42,.55);
      }
      * { box-sizing: border-box; }
      html, body, #root { width: 100%; height: 100%; margin: 0; }
      body {
        overflow: auto;
        background: transparent;
        color: var(--widget-text);
        font: 13px/1.45 Inter, ui-sans-serif, system-ui, -apple-system,
          BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      button, input, textarea, select { font: inherit; }
      button { appearance: none; }
    </style>
  </head>
  <body><div id="root"></div></body>
</html>`);
  await page.addScriptTag({ path: HTM_PREACT_PATH });

  await page.exposeBinding("__ambientRpc", async (_source, request) => {
    if (
      typeof request !== "object" ||
      request === null ||
      typeof request.method !== "string" ||
      typeof request.params !== "object" ||
      request.params === null
    ) {
      throw new Error("Invalid ambient RPC request");
    }
    if (session.pendingRpc.size >= 32) {
      throw new Error("Widget Runtime RPC concurrency limit reached");
    }
    const requestId = `rpc-${++session.rpcSequence}`;
    const promise = new Promise((resolve, reject) => {
      session.pendingRpc.set(requestId, { resolve, reject });
    });
    send(session.socket, {
      type: "rpc_request",
      session_id: session.id,
      request_id: requestId,
      method: request.method,
      params: request.params,
    });
    return promise;
  });

  await page.exposeBinding("__ambientHostEvent", async (_source, event) => {
    if (typeof event !== "object" || event === null) return;
    const kind = event.event;
    if (!["fullscreen", "minimize", "send_message"].includes(kind)) return;
    send(session.socket, {
      type: "host_event",
      session_id: session.id,
      event: kind,
      ...(kind === "send_message"
        ? { text: String(event.text ?? "").slice(0, 16_384) }
        : {}),
    });
  });

  await page.evaluate(
    ({ code, capabilityIds, theme }) => {
      const runtime = window.htmPreact;
      if (!runtime) throw new Error("The Widget renderer failed to initialize");
      const {
        Component,
        Fragment,
        createContext,
        h,
        html,
        render,
        useCallback,
        useContext,
        useEffect,
        useMemo,
        useReducer,
        useRef,
        useState,
      } = runtime;
      const root = document.getElementById("root");
      const allowed = new Set(capabilityIds);
      const subscriptions = new Map();
      let subscriptionSequence = 0;

      const mergeStyle = (base, custom) => {
        if (typeof custom === "string") {
          const baseText = Object.entries(base)
            .map(([key, value]) => {
              const cssKey = key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
              return `${cssKey}:${value}`;
            })
            .join(";");
          return `${baseText};${custom}`;
        }
        return { ...base, ...(custom && typeof custom === "object" ? custom : {}) };
      };

      const Column = ({
        children,
        gap,
        padding,
        align,
        justify,
        wrap,
        style,
        ...rest
      }) =>
        h(
          "div",
          {
            ...rest,
            style: mergeStyle(
              {
                display: "flex",
                flexDirection: "column",
                gap,
                padding,
                alignItems:
                  align === "center"
                    ? "center"
                    : align === "end"
                      ? "flex-end"
                      : align === "start"
                        ? "flex-start"
                        : align,
                justifyContent: justify,
                flexWrap: wrap ? "wrap" : undefined,
              },
              style,
            ),
          },
          children,
        );
      const Row = ({ children, gap, padding, align, justify, wrap, style, ...rest }) =>
        h(
          "div",
          {
            ...rest,
            style: mergeStyle(
              {
                display: "flex",
                flexDirection: "row",
                gap,
                padding,
                alignItems:
                  align === "center"
                    ? "center"
                    : align === "end"
                      ? "flex-end"
                      : "flex-start",
                justifyContent: justify,
                flexWrap: wrap ? "wrap" : undefined,
              },
              style,
            ),
          },
          children,
        );
      const Card = ({ title, children, style, ...rest }) =>
        h(
          "div",
          {
            ...rest,
            style: mergeStyle(
              {
                border: "1px solid var(--widget-border)",
                borderRadius: "12px",
                padding: "16px",
                color: "var(--widget-text)",
                backgroundColor: "var(--widget-surface)",
              },
              style,
            ),
          },
          title
            ? h(
                "h3",
                {
                  style: {
                    fontSize: "14px",
                    fontWeight: 600,
                    margin: "0 0 12px",
                    borderBottom: "1px solid var(--widget-border)",
                    paddingBottom: "6px",
                  },
                },
                title,
              )
            : null,
          children,
        );
      const Text = ({ text, style, ...rest }) =>
        h("span", { ...rest, style }, text);
      const Button = ({ label, variant, style, ...rest }) =>
        h(
          "button",
          {
            ...rest,
            style: mergeStyle(
              {
                padding: "6px 14px",
                borderRadius: "6px",
                cursor: "pointer",
                border: "none",
                fontWeight: 600,
                fontSize: "13px",
                backgroundColor:
                  variant === "danger"
                    ? "#ef4444"
                    : variant === "secondary"
                      ? "var(--widget-control)"
                      : "var(--accent)",
                color: "#fff",
              },
              style,
            ),
          },
          label,
        );
      const TextField = ({
        label,
        placeholder,
        value,
        onChange,
        onEnter,
        style,
        ...rest
      }) =>
        h(
          "label",
          {
            style: mergeStyle(
              { display: "flex", flexDirection: "column", gap: "6px" },
              style,
            ),
          },
          label ? h("span", null, label) : null,
          h("input", {
            ...rest,
            type: "text",
            placeholder,
            value: value ?? "",
            onInput: onChange,
            onKeyDown: (event) => {
              if (event.key === "Enter") onEnter?.(event.currentTarget.value);
            },
            style: {
              padding: "8px 12px",
              borderRadius: "6px",
              backgroundColor: "var(--widget-input)",
              border: "1px solid var(--widget-border)",
              color: "var(--widget-text)",
              width: "100%",
            },
          }),
        );
      const Checkbox = ({ label, checked, onChange, style, ...rest }) =>
        h(
          "label",
          {
            style: mergeStyle(
              { display: "flex", alignItems: "center", gap: "8px" },
              style,
            ),
          },
          h("input", {
            ...rest,
            type: "checkbox",
            checked: Boolean(checked),
            onChange: (event) => onChange?.(event.currentTarget.checked),
          }),
          h("span", null, label),
        );
      const List = ({ items = [], itemStyle, onItemClick, style, ...rest }) =>
        h(
          "div",
          {
            ...rest,
            style: mergeStyle(
              { display: "flex", flexDirection: "column", gap: "6px" },
              style,
            ),
          },
          items.map((item, index) =>
            h(
              "div",
              {
                key: item?.id ?? index,
                onClick: () => onItemClick?.(item, index),
                style: mergeStyle(
                  {
                    padding: "8px 12px",
                    borderRadius: "6px",
                    backgroundColor: "var(--widget-surface-soft)",
                    border: "1px solid var(--widget-border)",
                  },
                  itemStyle,
                ),
              },
              typeof item === "object"
                ? item.label ?? item.name ?? JSON.stringify(item)
                : String(item),
            ),
          ),
        );
      const Table = ({ columns = [], rows = [], onRowClick, style, ...rest }) =>
        h(
          "div",
          { ...rest, style: mergeStyle({ overflowX: "auto", width: "100%" }, style) },
          h(
            "table",
            { style: { width: "100%", borderCollapse: "collapse", textAlign: "left" } },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                columns.map((column) =>
                  h("th", { key: column, style: { padding: "8px" } }, column),
                ),
              ),
            ),
            h(
              "tbody",
              null,
              rows.map((row, rowIndex) =>
                h(
                  "tr",
                  {
                    key: row?.id ?? rowIndex,
                    onClick: () => onRowClick?.(row, rowIndex),
                  },
                  (Array.isArray(row) ? row : Object.values(row)).map(
                    (cell, cellIndex) =>
                      h("td", { key: cellIndex, style: { padding: "8px" } }, String(cell)),
                  ),
                ),
              ),
            ),
          ),
        );

      const ambient = {
        html,
        react: Object.freeze({
          Component,
          Fragment,
          createContext,
          useCallback,
          useContext,
          useEffect,
          useMemo,
          useReducer,
          useRef,
          useState,
        }),
        components: Object.freeze({
          Button,
          Card,
          Checkbox,
          Column,
          List,
          Row,
          Table,
          Text,
          TextField,
        }),
        theme: Object.freeze({
          preference: theme.preference,
          effective: theme.effective,
        }),
        sendMessage: (text) =>
          window.__ambientHostEvent({ event: "send_message", text }),
        fullscreen: () => window.__ambientHostEvent({ event: "fullscreen" }),
        minimize: () => window.__ambientHostEvent({ event: "minimize" }),
      };

      if (allowed.has("graph.query") || allowed.has("graph.mutate")) {
        const graph = {};
        if (allowed.has("graph.query")) {
          graph.subscribe = (query, callback) => {
            const subscriptionId = `sub-${++subscriptionSequence}`;
            subscriptions.set(subscriptionId, callback);
            window
              .__ambientRpc({
                method: "graph.subscribe",
                params: { subscription_id: subscriptionId, query },
              })
              .then((initial) => callback(initial))
              .catch((error) => {
                queueMicrotask(() => {
                  throw error;
                });
              });
            return () => {
              subscriptions.delete(subscriptionId);
              void window.__ambientRpc({
                method: "graph.unsubscribe",
                params: { subscription_id: subscriptionId },
              });
            };
          };
        }
        if (allowed.has("graph.mutate")) {
          graph.mutate = (actions) =>
            window.__ambientRpc({
              method: "graph.mutate",
              params: { actions, invocation_id: crypto.randomUUID() },
            });
        }
        ambient.graph = Object.freeze(graph);
      }
      if (allowed.has("network.request")) {
        ambient.net = Object.freeze({
          request: (sourceId, request) =>
            window.__ambientRpc({
              method: "net.request",
              params: { source_id: sourceId, request: request ?? {} },
            }),
        });
      }
      if (
        allowed.has("file.read") ||
        allowed.has("file.write") ||
        allowed.has("file.delete")
      ) {
        const files = {};
        if (allowed.has("file.read")) {
          files.read = (filePath) =>
            window.__ambientRpc({
              method: "files.read",
              params: { path: filePath },
            });
          files.list = (filePath) =>
            window.__ambientRpc({
              method: "files.list",
              params: { path: filePath },
            });
        }
        if (allowed.has("file.write")) {
          files.write = (filePath, text) =>
            window.__ambientRpc({
              method: "files.write",
              params: { path: filePath, text },
            });
        }
        if (allowed.has("file.delete")) {
          files.delete = (filePath) =>
            window.__ambientRpc({
              method: "files.delete",
              params: { path: filePath },
            });
        }
        ambient.files = Object.freeze(files);
      }
      if (allowed.has("capability.invoke")) {
        ambient.capabilities = Object.freeze({
          invoke: (catalogId, input, actionId) =>
            window.__ambientRpc({
              method: "capabilities.invoke",
              params: {
                catalog_id: catalogId,
                input: input ?? {},
                action_id: actionId,
                invocation_id: crypto.randomUUID(),
              },
            }),
        });
      }

      window.__ambientDeliverSubscription = (subscriptionId, data, error) => {
        const callback = subscriptions.get(subscriptionId);
        if (!callback) return;
        if (error) {
          subscriptions.delete(subscriptionId);
          queueMicrotask(() => {
            throw Object.assign(new Error(error.message ?? "Subscription failed"), error);
          });
          return;
        }
        callback(data);
      };

      Object.freeze(ambient);
      const exportsObject = {};
      const React = { createElement: h, Fragment };
      const load = new Function(
        "exports",
        "React",
        "ambient",
        `"use strict";\n${code}\n//# sourceURL=controller.js`,
      );
      load(exportsObject, React, ambient);
      const Controller =
        exportsObject.default ?? Object.values(exportsObject)[0];
      if (typeof Controller !== "function") {
        throw new Error("controller.js does not export a renderable default component");
      }
      render(h(Controller, { ambient }), root);
    },
    {
      code: transformedController,
      capabilityIds: session.capabilityIds,
      theme: session.theme,
    },
  );
}


async function startScreencast(session) {
  session.cdp = await session.context.newCDPSession(session.page);
  session.cdp.on("Page.screencastFrame", async (event) => {
    try {
      if (session.visible) {
        send(
          session.socket,
          {
            type: "frame",
            session_id: session.id,
            format: "jpeg",
            data: event.data,
            width: session.viewport.width,
            height: session.viewport.height,
          },
          { droppable: true },
        );
      }
    } finally {
      await session.cdp
        .send("Page.screencastFrameAck", { sessionId: event.sessionId })
        .catch(() => {});
    }
  });
  await session.cdp.send("Page.startScreencast", {
    format: "jpeg",
    quality: 72,
    maxWidth: session.viewport.width,
    maxHeight: session.viewport.height,
    everyNthFrame: 1,
  });
  session.screencastActive = true;
}


async function openSession(socket, message) {
  validateStart(message);
  if (socket.runtimeClosed || socket.destroyed) return;
  if (sessions.size >= MAX_CONTEXTS) {
    throw new Error("Widget Runtime context limit reached");
  }
  if (sessions.has(message.session_id)) {
    throw new Error("Widget Runtime session already exists");
  }
  const viewport = {
    width: Math.trunc(message.viewport.width),
    height: Math.trunc(message.viewport.height),
    deviceScaleFactor: Number(message.viewport.device_scale_factor ?? 1),
  };
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: viewport.deviceScaleFactor,
    locale: "en-US",
    javaScriptEnabled: true,
    serviceWorkers: "block",
    acceptDownloads: false,
  });
  if (socket.runtimeClosed || socket.destroyed) {
    await context.close().catch(() => {});
    return;
  }
  await context.route("**/*", (route) => route.abort("blockedbyclient"));
  const page = await context.newPage();
  if (socket.runtimeClosed || socket.destroyed) {
    await context.close().catch(() => {});
    return;
  }
  const session = {
    id: message.session_id,
    appId: message.app_id,
    artifactDigest: message.artifact_digest,
    capabilityIds: Array.isArray(message.capability_ids)
      ? message.capability_ids.filter((item) => typeof item === "string")
      : [],
    theme:
      typeof message.theme === "object" && message.theme !== null
        ? message.theme
        : { preference: "system", effective: "dark" },
    socket,
    context,
    page,
    cdp: null,
    viewport,
    pendingRpc: new Map(),
    rpcSequence: 0,
    visible: true,
    screencastActive: false,
    closed: false,
  };
  sessions.set(session.id, session);
  socket.sessionId = session.id;
  page.on("pageerror", (error) => {
    send(
      socket,
      runtimeError(
        "controller_runtime_error",
        error?.message ?? error,
        "code_only",
      ),
    );
  });
  page.on("crash", () => {
    send(
      socket,
      runtimeError("chromium_page_crashed", "The Widget page crashed", "operator"),
    );
  });
  try {
    const transformed = transformController(message.controller_source);
    await installPageRuntime(page, session, transformed);
    if (socket.runtimeClosed || socket.destroyed) {
      await closeSession(session);
      return;
    }
    await startScreencast(session);
    if (socket.runtimeClosed || socket.destroyed) {
      await closeSession(session);
      return;
    }
    send(socket, { type: "ready", session_id: session.id });
  } catch (error) {
    send(
      socket,
      runtimeError(
        "controller_load_failed",
        error?.message ?? error,
        "code_only",
      ),
    );
    await closeSession(session);
  }
}


async function dispatchInput(session, event) {
  if (typeof event !== "object" || event === null) {
    throw new Error("Widget Runtime input event is invalid");
  }
  switch (event.type) {
    case "pointer":
      await session.cdp.send("Input.dispatchMouseEvent", {
        type: event.event,
        x: event.x,
        y: event.y,
        button: event.button,
        buttons: event.buttons,
        clickCount: event.click_count,
      });
      break;
    case "wheel":
      await session.cdp.send("Input.dispatchMouseEvent", {
        type: "mouseWheel",
        x: event.x,
        y: event.y,
        deltaX: event.delta_x,
        deltaY: event.delta_y,
      });
      break;
    case "key":
      await session.cdp.send("Input.dispatchKeyEvent", {
        type: event.event,
        key: event.key,
        code: event.code,
        text: event.text,
        modifiers: event.modifiers,
      });
      break;
    case "text":
      await session.cdp.send("Input.insertText", { text: event.text });
      break;
    case "viewport":
      session.viewport.width = event.width;
      session.viewport.height = event.height;
      await session.page.setViewportSize({
        width: event.width,
        height: event.height,
      });
      break;
    case "visibility":
      session.visible = Boolean(event.visible);
      if (!session.visible && session.screencastActive) {
        await session.cdp.send("Page.stopScreencast").catch(() => {});
        session.screencastActive = false;
      } else if (session.visible && !session.screencastActive) {
        await startScreencast(session);
      }
      break;
    case "focus":
      if (event.focused) await session.page.bringToFront();
      break;
    default:
      throw new Error("Unsupported Widget Runtime input event");
  }
}


async function handleSessionMessage(socket, message) {
  if (message.type === "start") {
    await openSession(socket, message);
    return;
  }
  const session = sessions.get(socket.sessionId);
  if (!session || message.session_id !== session.id) {
    throw new Error("Widget Runtime session identity mismatch");
  }
  if (message.type === "close") {
    await closeSession(session);
    return;
  }
  if (message.type === "input") {
    await dispatchInput(session, message.event);
    return;
  }
  if (message.type === "rpc_response") {
    const pending = session.pendingRpc.get(message.request_id);
    if (!pending) return;
    session.pendingRpc.delete(message.request_id);
    if (message.error) {
      pending.reject(
        Object.assign(
          new Error(message.error.message ?? "Widget capability request failed"),
          message.error,
        ),
      );
    } else {
      pending.resolve(message.result);
    }
    return;
  }
  if (message.type === "subscription_event") {
    await session.page.evaluate(
      ({ subscriptionId, data, error }) => {
        window.__ambientDeliverSubscription?.(subscriptionId, data, error);
      },
      {
        subscriptionId: message.subscription_id,
        data: message.data,
        error: message.error,
      },
    );
    return;
  }
  throw new Error("Unsupported Widget Runtime protocol message");
}


async function closeSession(session) {
  if (!session || session.closed) return;
  session.closed = true;
  sessions.delete(session.id);
  for (const pending of session.pendingRpc.values()) {
    pending.reject(new Error("Widget Runtime session closed"));
  }
  session.pendingRpc.clear();
  if (session.screencastActive && session.cdp) {
    await session.cdp.send("Page.stopScreencast").catch(() => {});
  }
  await session.context.close().catch(() => {});
  if (session.socket.sessionId === session.id) {
    session.socket.sessionId = undefined;
  }
}


function createSocketServer() {
  fs.mkdirSync(path.dirname(SOCKET_PATH), { recursive: true });
  try {
    const stat = fs.lstatSync(SOCKET_PATH);
    if (!stat.isSocket()) {
      throw new Error(`Refusing to replace non-socket path ${SOCKET_PATH}`);
    }
    fs.unlinkSync(SOCKET_PATH);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  const server = net.createServer((socket) => {
    socket.setNoDelay(true);
    socket.buffer = Buffer.alloc(0);
    socket.sessionId = undefined;
    socket.runtimeClosed = false;
    socket.taskQueue = new SerialTaskQueue();

    socket.on("data", (chunk) => {
      socket.buffer = Buffer.concat([socket.buffer, chunk]);
      if (socket.buffer.byteLength > MAX_MESSAGE_BYTES + 1) {
        send(
          socket,
          runtimeError(
            "runtime_message_too_large",
            "Widget Runtime message exceeds the byte limit",
            "abuse_or_budget",
          ),
        );
        socket.destroy();
        return;
      }
      let newline;
      while ((newline = socket.buffer.indexOf(0x0a)) >= 0) {
        const payload = socket.buffer.subarray(0, newline);
        socket.buffer = socket.buffer.subarray(newline + 1);
        if (payload.byteLength === 0) continue;
        let message;
        try {
          message = JSON.parse(payload.toString("utf8"));
        } catch {
          send(
            socket,
            runtimeError(
              "runtime_protocol_invalid",
              "Widget Runtime received invalid JSON",
              "abuse_or_budget",
            ),
          );
          socket.destroy();
          return;
        }
        void socket.taskQueue.enqueue(async () => {
          try {
            await handleSessionMessage(socket, message);
          } catch (error) {
            send(
              socket,
              runtimeError(
                "runtime_protocol_failed",
                error?.message ?? error,
                "operator",
              ),
            );
            if (socket.sessionId) {
              await closeSession(sessions.get(socket.sessionId));
            }
          }
        });
      }
    });
    socket.on("close", () => {
      socket.runtimeClosed = true;
      void socket.taskQueue.enqueue(async () => {
        if (socket.sessionId) {
          await closeSession(sessions.get(socket.sessionId));
        }
      }).catch(() => {});
    });
    socket.on("error", () => {});
  });
  server.listen(SOCKET_PATH, () => {
    fs.chmodSync(SOCKET_PATH, 0o660);
    process.stdout.write(`Widget Runtime listening on ${SOCKET_PATH}\n`);
  });
  return server;
}


await launchBrowser();
const server = createSocketServer();

async function shutdown() {
  shuttingDown = true;
  server.close();
  for (const session of [...sessions.values()]) {
    await closeSession(session);
  }
  await browser?.close().catch(() => {});
  try {
    fs.unlinkSync(SOCKET_PATH);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

process.on("SIGTERM", () => void shutdown().finally(() => process.exit(0)));
process.on("SIGINT", () => void shutdown().finally(() => process.exit(0)));
