import { normalizePresentationContext } from "./presentation_context.mjs";


export function transformController(controllerSource, babel) {
  if (!babel || typeof babel.transform !== "function") {
    throw new Error("The Widget controller compiler failed to initialize");
  }
  return babel.transform(controllerSource, {
    presets: [["react", { runtime: "classic" }]],
    plugins: ["transform-modules-commonjs"],
    filename: "controller.js",
    sourceMaps: "inline",
  }).code;
}


export function createStorageApi(storageRequest) {
  if (typeof storageRequest !== "function") {
    throw new TypeError("Widget storage requires a request transport");
  }
  return Object.freeze({
    get: (key) => storageRequest("get", { key }),
    set: (key, value) => storageRequest("set", { key, value }),
    delete: (key) => storageRequest("delete", { key }),
    clear: () => storageRequest("clear", {}),
    list: () => storageRequest("list", {}),
  });
}


function mergeStyle(base, custom) {
  if (typeof custom === "string") {
    const baseText = Object.entries(base)
      .map(([key, value]) => {
        const cssKey = key.replace(
          /[A-Z]/g,
          (letter) => `-${letter.toLowerCase()}`,
        );
        return `${cssKey}:${value}`;
      })
      .join(";");
    return `${baseText};${custom}`;
  }
  return {
    ...base,
    ...(custom && typeof custom === "object" ? custom : {}),
  };
}


function createComponents(h) {
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

  const Row = ({
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
          if (event.key === "Enter") {
            onEnter?.(event.currentTarget.value);
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

  const List = ({
    items = [],
    itemStyle,
    onItemClick,
    style,
    ...rest
  }) =>
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

  const Table = ({
    columns = [],
    rows = [],
    onRowClick,
    style,
    ...rest
  }) =>
    h(
      "div",
      {
        ...rest,
        style: mergeStyle({ overflowX: "auto", width: "100%" }, style),
      },
      h(
        "table",
        {
          style: {
            width: "100%",
            borderCollapse: "collapse",
            textAlign: "left",
          },
        },
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
                  h(
                    "td",
                    { key: cellIndex, style: { padding: "8px" } },
                    String(cell),
                  ),
              ),
            ),
          ),
        ),
      ),
    );

  return Object.freeze({
    Button,
    Card,
    Checkbox,
    Column,
    List,
    Row,
    Table,
    Text,
    TextField,
  });
}


export function mountController({
  babel,
  runtime,
  root,
  controllerSource,
  capabilityIds,
  presentationContext,
  transport,
}) {
  if (!runtime || typeof runtime.render !== "function") {
    throw new Error("The Widget renderer failed to initialize");
  }
  if (!root) {
    throw new Error("The Widget frame root is unavailable");
  }
  if (
    !transport
    || typeof transport.rpc !== "function"
    || typeof transport.storageRequest !== "function"
    || typeof transport.hostEvent !== "function"
  ) {
    throw new Error("The Widget host transport failed to initialize");
  }

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
  const allowed = new Set(
    Array.isArray(capabilityIds)
      ? capabilityIds.filter((item) => typeof item === "string")
      : [],
  );
  const subscriptions = new Map();
  const presentationListeners = new Set();
  const themeListeners = new Set();
  let subscriptionSequence = 0;
  let disposed = false;
  let currentPresentation;

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

  const applyPresentation = (value, notify = true) => {
    const next = normalizePresentationContext(value);
    currentPresentation = {
      theme: {
        preference: next.theme.preference,
        effective: next.theme.effective,
      },
      locale: next.locale,
      reducedMotion: Boolean(next.reduced_motion),
    };
    const documentRoot = root.ownerDocument?.documentElement;
    if (documentRoot) {
      documentRoot.dataset.theme = currentPresentation.theme.effective;
      documentRoot.dataset.themePreference =
        currentPresentation.theme.preference;
      documentRoot.lang = currentPresentation.locale;
      documentRoot.style.colorScheme = currentPresentation.theme.effective;
    }
    if (!notify) return;
    const nextTheme = themeSnapshot();
    const nextPresentation = presentationSnapshot();
    themeListeners.forEach((listener) => listener(nextTheme));
    presentationListeners.forEach((listener) => listener(nextPresentation));
  };
  applyPresentation(presentationContext, false);

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
    components: createComponents(h),
    theme: themeApi,
    presentation: presentationApi,
    storage: createStorageApi(transport.storageRequest),
    sendMessage: (text) =>
      transport.hostEvent({ event: "send_message", text }),
    fullscreen: () => transport.hostEvent({ event: "fullscreen" }),
    minimize: () => transport.hostEvent({ event: "minimize" }),
  };

  if (allowed.has("graph.query") || allowed.has("graph.mutate")) {
    const graph = {};
    if (allowed.has("graph.query")) {
      graph.subscribe = (query, callback) => {
        if (typeof callback !== "function") {
          throw new TypeError("ambient.graph.subscribe requires a callback");
        }
        const subscriptionId = `sub-${++subscriptionSequence}`;
        subscriptions.set(subscriptionId, callback);
        transport
          .rpc("graph.subscribe", {
            subscription_id: subscriptionId,
            query,
          })
          .then((initial) => callback(initial))
          .catch((error) => {
            queueMicrotask(() => {
              throw error;
            });
          });
        return () => {
          subscriptions.delete(subscriptionId);
          void transport.rpc("graph.unsubscribe", {
            subscription_id: subscriptionId,
          });
        };
      };
    }
    if (allowed.has("graph.mutate")) {
      graph.mutate = (actions) =>
        transport.rpc("graph.mutate", { actions });
    }
    ambient.graph = Object.freeze(graph);
  }

  if (allowed.has("network.request")) {
    ambient.net = Object.freeze({
      request: (sourceId, request) =>
        transport.rpc("net.request", {
          source_id: sourceId,
          request: request ?? {},
        }),
    });
  }

  if (
    allowed.has("file.read")
    || allowed.has("file.write")
    || allowed.has("file.delete")
  ) {
    const files = {};
    if (allowed.has("file.read")) {
      files.read = (filePath) =>
        transport.rpc("files.read", { path: filePath });
      files.list = (filePath) =>
        transport.rpc("files.list", { path: filePath });
    }
    if (allowed.has("file.write")) {
      files.write = (filePath, text) =>
        transport.rpc("files.write", { path: filePath, text });
    }
    if (allowed.has("file.delete")) {
      files.delete = (filePath) =>
        transport.rpc("files.delete", { path: filePath });
    }
    ambient.files = Object.freeze(files);
  }

  if (allowed.has("capability.invoke")) {
    ambient.capabilities = Object.freeze({
      invoke: (catalogId, input, actionId) =>
        transport.rpc("capabilities.invoke", {
          catalog_id: catalogId,
          input: input ?? {},
          action_id: actionId,
        }),
    });
  }

  Object.freeze(ambient);
  const exportsObject = {};
  const React = { createElement: h, Fragment };
  const code = transformController(controllerSource, babel);
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

  return Object.freeze({
    updatePresentation(next) {
      if (!disposed) applyPresentation(next);
    },
    deliverSubscription(subscriptionId, data, error) {
      if (disposed) return;
      const callback = subscriptions.get(subscriptionId);
      if (!callback) return;
      if (error) {
        subscriptions.delete(subscriptionId);
        queueMicrotask(() => {
          throw Object.assign(
            new Error(error.message ?? "Subscription failed"),
            error,
          );
        });
        return;
      }
      callback(data);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      subscriptions.clear();
      presentationListeners.clear();
      themeListeners.clear();
      render(null, root);
    },
  });
}
