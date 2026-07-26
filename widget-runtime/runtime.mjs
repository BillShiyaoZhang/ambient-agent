import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

import { screencastDimensions } from "./frame_geometry.mjs";
import {
  normalizePresentationContext,
  presentationMedia,
} from "./presentation_context.mjs";
import { SerialTaskQueue } from "./serial_task_queue.mjs";


const PROTOCOL_VERSION = 1;
const SOCKET_PATH =
  process.env.WIDGET_RUNTIME_SOCKET_PATH ??
  "/run/ambient-widget-runtime/runtime.sock";
const CHROMIUM_PATH =
  process.env.CHROMIUM_EXECUTABLE_PATH ?? "/usr/bin/chromium";
export const DEFAULT_MAX_CONTEXTS = 4;
export const DEFAULT_BROWSER_IDLE_TIMEOUT_MS = 60_000;
const MAX_CONTEXTS = boundedInteger(
  process.env.WIDGET_RUNTIME_MAX_CONTEXTS,
  DEFAULT_MAX_CONTEXTS,
  1,
  64,
);
const BROWSER_IDLE_TIMEOUT_MS = boundedInteger(
  process.env.WIDGET_RUNTIME_IDLE_TIMEOUT_MS,
  DEFAULT_BROWSER_IDLE_TIMEOUT_MS,
  0,
  60 * 60 * 1000,
);
const MAX_MESSAGE_BYTES = 4 * 1024 * 1024;
const RUNTIME_MODULE_PATH = fileURLToPath(import.meta.url);
const CURRENT_DIRECTORY = path.dirname(RUNTIME_MODULE_PATH);
const HTM_PREACT_PATH = path.join(
  CURRENT_DIRECTORY,
  "node_modules",
  "htm",
  "preact",
  "standalone.umd.js",
);

const sessions = new Map();
const openingSessionIds = new Set();
let shuttingDown = false;
let server;


function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, parsed));
}


export class BrowserLifecycle {
  constructor({
    launchBrowser,
    idleTimeoutMs = DEFAULT_BROWSER_IDLE_TIMEOUT_MS,
    onFatal = () => {},
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    if (typeof launchBrowser !== "function") {
      throw new TypeError("BrowserLifecycle requires launchBrowser");
    }
    this.launchBrowser = launchBrowser;
    this.idleTimeoutMs = idleTimeoutMs;
    this.onFatal = onFatal;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.browser = null;
    this.launchPromise = null;
    this.closingPromise = null;
    this.idleTimer = null;
    this.activeLeases = 0;
    this.intentionalClosures = new WeakSet();
    this.shuttingDown = false;
    this.fatalError = null;
  }

  assertOperational() {
    if (this.shuttingDown) {
      throw new Error("Widget Runtime is shutting down");
    }
    if (this.fatalError) {
      throw this.fatalError;
    }
  }

  enterFatal(code, message, cause) {
    if (this.shuttingDown || this.fatalError) return;
    const error = new Error(
      `${message}; Widget Runtime restart is required`,
      { cause },
    );
    error.code = code;
    this.fatalError = error;
    this.cancelIdleClose();
    void Promise.resolve()
      .then(() => this.onFatal(error))
      .catch(() => {});
  }

  async acquire() {
    this.assertOperational();
    this.cancelIdleClose();
    this.activeLeases += 1;
    try {
      const browser = await this.ensureBrowser();
      this.assertOperational();
      let released = false;
      return {
        browser,
        release: () => {
          if (released) return;
          released = true;
          this.activeLeases = Math.max(0, this.activeLeases - 1);
          if (this.activeLeases === 0) this.scheduleIdleClose();
        },
      };
    } catch (error) {
      this.activeLeases = Math.max(0, this.activeLeases - 1);
      if (this.activeLeases === 0) this.scheduleIdleClose();
      throw error;
    }
  }

  async ensureBrowser() {
    const closingPromise = this.closingPromise;
    if (closingPromise) await closingPromise;
    this.assertOperational();
    if (this.browser) {
      if (
        typeof this.browser.isConnected !== "function"
        || this.browser.isConnected()
      ) {
        return this.browser;
      }
      const disconnectedBrowser = this.browser;
      this.browser = null;
      this.enterFatal(
        "chromium_disconnected",
        "The managed Chromium process exited unexpectedly",
        disconnectedBrowser,
      );
      this.assertOperational();
    }
    if (!this.launchPromise) {
      const launchPromise = this.launchOnce();
      this.launchPromise = launchPromise;
      void launchPromise.finally(() => {
        if (this.launchPromise === launchPromise) {
          this.launchPromise = null;
        }
      }).catch(() => {});
    }
    return this.launchPromise;
  }

  async launchOnce() {
    const browser = await this.launchBrowser();
    this.browser = browser;
    browser.on("disconnected", () => {
      this.handleDisconnect(browser);
    });
    if (this.shuttingDown) {
      await this.closeIntentionally(browser);
      throw new Error("Widget Runtime is shutting down");
    }
    this.assertOperational();
    return browser;
  }

  handleDisconnect(browser) {
    if (this.intentionalClosures.has(browser)) return;
    if (this.browser !== browser) return;
    this.browser = null;
    if (this.shuttingDown) return;
    this.enterFatal(
      "chromium_disconnected",
      "The managed Chromium process exited unexpectedly",
      browser,
    );
  }

  cancelIdleClose() {
    if (!this.idleTimer) return;
    this.clearTimer(this.idleTimer);
    this.idleTimer = null;
  }

  scheduleIdleClose() {
    if (
      this.shuttingDown
      || this.fatalError
      || this.idleTimer
      || !this.browser
    ) {
      return;
    }
    this.idleTimer = this.setTimer(() => {
      this.idleTimer = null;
      return this.closeIfIdle();
    }, this.idleTimeoutMs);
  }

  async closeIfIdle() {
    if (this.shuttingDown || this.activeLeases !== 0 || !this.browser) return;
    await this.closeIntentionally(this.browser);
  }

  async closeIntentionally(browser) {
    if (!browser) {
      await this.closingPromise;
      return;
    }
    if (this.closingPromise) {
      await this.closingPromise;
      return;
    }
    if (this.browser === browser) this.browser = null;
    this.intentionalClosures.add(browser);
    let resolveClosing;
    const closingPromise = new Promise((resolve) => {
      resolveClosing = resolve;
    });
    this.closingPromise = closingPromise;
    try {
      await browser.close();
    } catch (error) {
      const stillConnected =
        typeof browser.isConnected !== "function"
        || browser.isConnected();
      if (stillConnected && !this.shuttingDown) {
        if (!this.browser) this.browser = browser;
        this.enterFatal(
          "chromium_close_failed",
          "The managed Chromium process failed to close",
          error,
        );
      }
    } finally {
      resolveClosing();
      if (this.closingPromise === closingPromise) {
        this.closingPromise = null;
      }
    }
  }

  async shutdown() {
    if (this.shuttingDown) return;
    this.shuttingDown = true;
    this.cancelIdleClose();
    await this.launchPromise?.catch(() => {});
    await this.closeIntentionally(this.browser);
  }
}


export function createControllerTransformer({
  loadBabel = () => import("@babel/standalone"),
} = {}) {
  let babelPromise;
  return async (source) => {
    if (!babelPromise) {
      babelPromise = Promise.resolve().then(loadBabel);
      void babelPromise.catch(() => {
        babelPromise = undefined;
      });
    }
    const namespace = await babelPromise;
    const babel =
      typeof namespace?.transform === "function"
        ? namespace
        : namespace?.default;
    if (typeof babel?.transform !== "function") {
      throw new Error("Babel standalone does not expose transform()");
    }
    return babel.transform(source, {
      presets: [["react", { runtime: "classic" }]],
      plugins: ["transform-modules-commonjs"],
      filename: "controller.js",
      sourceMaps: "inline",
    }).code;
  };
}


const transformController = createControllerTransformer();


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


async function launchManagedBrowser() {
  return chromium.launch({
    executablePath: CHROMIUM_PATH,
    headless: true,
    chromiumSandbox: true,
    args: browserArguments(),
  });
}


const browserLifecycle = new BrowserLifecycle({
  idleTimeoutMs: BROWSER_IDLE_TIMEOUT_MS,
  launchBrowser: launchManagedBrowser,
  onFatal: (error) => {
    if (shuttingDown) return;
    for (const session of [...sessions.values()]) {
      send(
        session.socket,
        runtimeError(
          error.code ?? "chromium_fatal",
          error.message,
          "operator",
        ),
      );
      void closeSession(session);
    }
    // An unexpected Chromium exit may have left renderer state inconsistent.
    // Exit so Docker's bounded restart policy can restore the complete runtime.
    // Intentional idle and shutdown closes are suppressed by BrowserLifecycle.
    setTimeout(() => process.exit(1), 250);
  },
});


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
      :root[data-theme="light"] {
        color-scheme: light;
        --widget-text: rgba(15,23,42,.92);
        --widget-muted: rgba(15,23,42,.6);
        --widget-border: rgba(15,23,42,.12);
        --widget-surface: rgba(255,255,255,.72);
        --widget-surface-soft: rgba(15,23,42,.045);
        --widget-control: #64748b;
        --widget-input: rgba(255,255,255,.78);
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
      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
          scroll-behavior: auto !important;
          animation-duration: .01ms !important;
          animation-iteration-count: 1 !important;
          transition-duration: .01ms !important;
        }
      }
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
    ({ code, capabilityIds, presentationContext }) => {
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
      const presentationListeners = new Set();
      const themeListeners = new Set();
      let subscriptionSequence = 0;
      let currentPresentation = {
        theme: {
          preference: presentationContext.theme.preference,
          effective: presentationContext.theme.effective,
        },
        locale: presentationContext.locale,
        reducedMotion: Boolean(presentationContext.reduced_motion),
      };

      const themeSnapshot = () =>
        Object.freeze({
          preference: currentPresentation.theme.preference,
          effective: currentPresentation.theme.effective,
        });
      const presentationSnapshot = () =>
        Object.freeze({
          theme: themeSnapshot(),
          locale: currentPresentation.locale,
          reducedMotion: currentPresentation.reducedMotion,
        });
      const applyPresentation = (next, notify = true) => {
        currentPresentation = {
          theme: {
            preference: next.theme.preference,
            effective: next.theme.effective,
          },
          locale: next.locale,
          reducedMotion: Boolean(next.reduced_motion),
        };
        const documentRoot = document.documentElement;
        documentRoot.dataset.theme = currentPresentation.theme.effective;
        documentRoot.dataset.themePreference = currentPresentation.theme.preference;
        documentRoot.lang = currentPresentation.locale;
        documentRoot.style.colorScheme = currentPresentation.theme.effective;
        if (!notify) return;
        const nextTheme = themeSnapshot();
        const nextPresentation = presentationSnapshot();
        themeListeners.forEach((listener) => listener(nextTheme));
        presentationListeners.forEach((listener) => listener(nextPresentation));
      };
      window.__ambientUpdatePresentation = (next) => applyPresentation(next);
      applyPresentation(presentationContext, false);

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
            onInput: (event) =>
              onChange?.(String(event?.currentTarget?.value ?? "")),
            onKeyDown: (event) => {
              if (event.key === "Enter") {
                onEnter?.(String(event?.currentTarget?.value ?? ""));
              }
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

      const themeApi = Object.freeze({
        get preference() {
          return currentPresentation.theme.preference;
        },
        get effective() {
          return currentPresentation.theme.effective;
        },
        getSnapshot: themeSnapshot,
        subscribe(listener) {
          if (typeof listener !== "function") {
            throw new TypeError("ambient.theme.subscribe requires a listener");
          }
          themeListeners.add(listener);
          return () => themeListeners.delete(listener);
        },
      });
      const presentationApi = Object.freeze({
        getSnapshot: presentationSnapshot,
        subscribe(listener) {
          if (typeof listener !== "function") {
            throw new TypeError("ambient.presentation.subscribe requires a listener");
          }
          presentationListeners.add(listener);
          return () => presentationListeners.delete(listener);
        },
      });
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
        theme: themeApi,
        presentation: presentationApi,
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
              params: { actions },
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
      presentationContext: session.presentationContext,
    },
  );
}


async function startScreencast(session) {
  if (!session.cdp) {
    session.cdp = await session.context.newCDPSession(session.page);
    session.cdp.on("Page.screencastFrame", async (event) => {
      try {
        if (!session.visible || session.captureInFlight || session.closed) return;
        session.captureInFlight = true;
        const dimensions = screencastDimensions(session.viewport);
        const deviceScaleFactor = Math.max(
          1,
          Number(session.viewport.deviceScaleFactor) || 1,
        );
        const captureScale = Math.min(
          dimensions.maxWidth / (session.viewport.width * deviceScaleFactor),
          dimensions.maxHeight / (session.viewport.height * deviceScaleFactor),
        );
        const screenshot = await session.cdp.send("Page.captureScreenshot", {
          format: "jpeg",
          quality: 88,
          fromSurface: true,
          captureBeyondViewport: false,
          optimizeForSpeed: true,
          clip: {
            x: 0,
            y: 0,
            width: session.viewport.width,
            height: session.viewport.height,
            scale: captureScale,
          },
        });
        send(
          session.socket,
          {
            type: "frame",
            session_id: session.id,
            format: "jpeg",
            data: screenshot.data,
            width: dimensions.maxWidth,
            height: dimensions.maxHeight,
          },
          { droppable: true },
        );
      } catch (error) {
        if (!session.closed) {
          send(
            session.socket,
            runtimeError(
              "frame_capture_failed",
              error?.message ?? error,
              "operator",
            ),
          );
        }
      } finally {
        session.captureInFlight = false;
        await session.cdp
          .send("Page.screencastFrameAck", { sessionId: event.sessionId })
          .catch(() => {});
      }
    });
  }
  await session.cdp.send("Emulation.setDeviceMetricsOverride", {
    width: session.viewport.width,
    height: session.viewport.height,
    deviceScaleFactor: session.viewport.deviceScaleFactor,
    mobile: false,
    screenWidth: session.viewport.width,
    screenHeight: session.viewport.height,
  });
  await session.cdp.send("Page.startScreencast", {
    format: "jpeg",
    quality: 35,
    maxWidth: session.viewport.width,
    maxHeight: session.viewport.height,
    everyNthFrame: 1,
  });
  session.screencastActive = true;
}


function openingCancelled(socket) {
  return shuttingDown || socket.runtimeClosed || socket.destroyed;
}


async function openSession(socket, message) {
  if (openingCancelled(socket)) return;
  validateStart(message);
  if (socket.sessionId) {
    throw new Error("Widget Runtime socket already owns a session");
  }
  if (sessions.size + openingSessionIds.size >= MAX_CONTEXTS) {
    throw new Error("Widget Runtime context limit reached");
  }
  if (
    sessions.has(message.session_id)
    || openingSessionIds.has(message.session_id)
  ) {
    throw new Error("Widget Runtime session already exists");
  }
  openingSessionIds.add(message.session_id);
  let browserLease;
  let context;
  try {
    const viewport = {
      width: Math.trunc(message.viewport.width),
      height: Math.trunc(message.viewport.height),
      deviceScaleFactor: Number(message.viewport.device_scale_factor ?? 1),
    };
    const presentationContext = normalizePresentationContext(
      message.presentation_context
        ?? (message.theme ? { theme: message.theme } : undefined),
    );
    if (openingCancelled(socket)) return;
    browserLease = await browserLifecycle.acquire();
    if (openingCancelled(socket)) return;
    context = await browserLease.browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: viewport.deviceScaleFactor,
      locale: presentationContext.locale,
      ...presentationMedia(presentationContext),
      javaScriptEnabled: true,
      serviceWorkers: "block",
      acceptDownloads: false,
    });
    if (openingCancelled(socket)) return;
    await context.route("**/*", (route) => route.abort("blockedbyclient"));
    if (openingCancelled(socket)) return;
    const page = await context.newPage();
    if (openingCancelled(socket)) return;
    const session = {
      id: message.session_id,
      appId: message.app_id,
      artifactDigest: message.artifact_digest,
      capabilityIds: Array.isArray(message.capability_ids)
        ? message.capability_ids.filter((item) => typeof item === "string")
        : [],
      presentationContext,
      socket,
      context,
      browserLease,
      page,
      cdp: null,
      viewport,
      pendingRpc: new Map(),
      rpcSequence: 0,
      visible: true,
      screencastActive: false,
      captureInFlight: false,
      closed: false,
    };
    sessions.set(session.id, session);
    openingSessionIds.delete(session.id);
    socket.sessionId = session.id;
    context = undefined;
    browserLease = undefined;
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
        runtimeError(
          "chromium_page_crashed",
          "The Widget page crashed",
          "operator",
        ),
      );
    });
    try {
      const transformed = await transformController(message.controller_source);
      if (openingCancelled(socket)) {
        await closeSession(session);
        return;
      }
      await installPageRuntime(page, session, transformed);
      if (openingCancelled(socket)) {
        await closeSession(session);
        return;
      }
      await startScreencast(session);
      if (openingCancelled(socket)) {
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
  } finally {
    if (context) await context.close().catch(() => {});
    openingSessionIds.delete(message.session_id);
    browserLease?.release();
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
      if (session.screencastActive) {
        await session.cdp.send("Page.stopScreencast").catch(() => {});
        session.screencastActive = false;
      }
      session.viewport.width = event.width;
      session.viewport.height = event.height;
      await session.page.setViewportSize({
        width: event.width,
        height: event.height,
      });
      if (session.visible) await startScreencast(session);
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
    case "presentation_context": {
      const nextPresentation = normalizePresentationContext(event);
      session.presentationContext = nextPresentation;
      await session.page.emulateMedia(presentationMedia(nextPresentation));
      await session.page.evaluate((next) => {
        window.__ambientUpdatePresentation?.(next);
      }, nextPresentation);
      break;
    }
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
  for (const pending of session.pendingRpc.values()) {
    pending.reject(new Error("Widget Runtime session closed"));
  }
  session.pendingRpc.clear();
  if (session.screencastActive && session.cdp) {
    await session.cdp.send("Page.stopScreencast").catch(() => {});
  }
  try {
    await session.context.close().catch(() => {});
  } finally {
    sessions.delete(session.id);
    session.browserLease.release();
    if (session.socket.sessionId === session.id) {
      session.socket.sessionId = undefined;
    }
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


export function startRuntime() {
  if (server) return server;
  server = createSocketServer();
  return server;
}


export async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  server?.close();
  for (const session of [...sessions.values()]) {
    await closeSession(session);
  }
  await browserLifecycle.shutdown();
  try {
    fs.unlinkSync(SOCKET_PATH);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}


const isMainModule =
  typeof process.argv[1] === "string"
  && path.resolve(process.argv[1]) === RUNTIME_MODULE_PATH;
if (isMainModule) {
  startRuntime();
  process.on("SIGTERM", () => void shutdown().finally(() => process.exit(0)));
  process.on("SIGINT", () => void shutdown().finally(() => process.exit(0)));
}
