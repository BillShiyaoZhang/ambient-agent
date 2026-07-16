import React, { useEffect, useRef, useState, useMemo } from "react";
import type { Widget } from "./DashboardCanvas";
import wsService from "../services/websocket";
import * as Babel from "@babel/standalone";
import { ErrorBoundary } from "./ErrorBoundary";

// --- SandboxWidget Prop Interface ---

interface SandboxWidgetProps {
  widget: Widget;
  onFullscreen?: (id: string) => void;
  onMinimize?: (id: string) => void;
}

// Global fetch cache to share API responses across all mounts and sandbox instances
const globalFetchCache = new Map<string, { data: any; timestamp: number }>();
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes TTL

// Scopes widget-defined CSS rules to prevent global layout pollution (for legacy widgets)
const scopeCss = (css: string, scopeId: string): string => {
  if (!css) return "";
  const cleanCss = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const prefix = `[data-widget-scope="${scopeId}"]`;
  
  return cleanCss.replace(/(^|}|;|{)\s*([^{}@]+)\s*(?={)/g, (match, p1, selector) => {
    const trimmed = selector.trim();
    if (!trimmed) return match;
    
    const scopedSelectors = trimmed.split(",").map((sel: string) => {
      const s = sel.trim();
      if (!s) return "";
      if (s === "from" || s === "to" || s.endsWith("%") || s.includes(prefix)) {
        return s;
      }
      if (s === "html" || s === "body" || s === ":root") {
        return prefix;
      }
      return `${prefix} ${s}`;
    });
    
    return p1 + " " + scopedSelectors.join(", ");
  });
};

// JSON Pointer helpers (RFC 6901) for A2UI state binding
const getValueByPointer = (obj: any, pointer: string): any => {
  if (!pointer || pointer === "/") return obj;
  const parts = pointer.split("/").slice(1);
  let curr = obj;
  for (const part of parts) {
    const key = part.replace(/~1/g, "/").replace(/~0/g, "~");
    if (curr === null || curr === undefined || typeof curr !== "object") return undefined;
    curr = curr[key];
  }
  return curr;
};

const setValueByPointer = (obj: any, pointer: string, value: any): any => {
  const newObj = { ...obj };
  if (!pointer || pointer === "/") return value;
  const parts = pointer.split("/").slice(1);
  let curr = newObj;
  for (let i = 0; i < parts.length; i++) {
    const key = parts[i].replace(/~1/g, "/").replace(/~0/g, "~");
    if (i === parts.length - 1) {
      curr[key] = value;
    } else {
      curr[key] = typeof curr[key] === "object" && curr[key] !== null ? { ...curr[key] } : {};
      curr = curr[key];
    }
  }
  return newObj;
};

const resolveProp = (propValue: any, localState: any) => {
  if (propValue && typeof propValue === "object" && "binding" in propValue) {
    return getValueByPointer(localState, propValue.binding);
  }
  return propValue;
};

export const SandboxWidget: React.FC<SandboxWidgetProps> = ({
  widget,
  onFullscreen,
  onMinimize,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const onFullscreenRef = useRef(onFullscreen);
  const onMinimizeRef = useRef(onMinimize);

  // --- A2UI Specific React State & Refs ---
  const [localState, setLocalState] = useState<any>({});
  const stateRef = useRef(localState);
  const listenersRef = useRef<{ [actionId: string]: Function }>({});

  useEffect(() => {
    onFullscreenRef.current = onFullscreen;
    onMinimizeRef.current = onMinimize;
  }, [onFullscreen, onMinimize]);

  useEffect(() => {
    stateRef.current = localState;
  }, [localState]);

  // --- Tailwind Play CDN Dynamic Injection ---
  useEffect(() => {
    if (!widget.jsx) return;
    if (!document.getElementById("tailwind-cdn")) {
      const script = document.createElement("script");
      script.id = "tailwind-cdn";
      script.src = "https://cdn.tailwindcss.com";
      document.head.appendChild(script);
    }
  }, [widget.jsx]);

  // --- React + Tailwind Dynamic Compilation ---
  const DynamicReactComponent = useMemo(() => {
    if (!widget.jsx) return null;

    try {
      // 1. Transpile controller hook
      const controllerJs = widget.js || "";
      const transpileController = Babel.transform(controllerJs, {
        presets: ["react"],
        filename: "controller.js"
      }).code;

      // 2. Transpile index.jsx component
      const jsxCode = widget.jsx || "";
      // Strip import useController from controller.js statement to avoid eval module errors
      const cleanJsxCode = jsxCode.replace(/import\s+\{\s*useController\s*\}\s+from\s+["']\.\/controller(?:\.js)?["'];?/g, "");
      const transpileJsx = Babel.transform(cleanJsxCode, {
        presets: ["react"],
        filename: "index.jsx"
      }).code;

      // 3. Compile in isolated mock CommonJS scope
      const controllerExports: any = {};
      const controllerFn = new Function("exports", "React", transpileController || "");
      controllerFn(controllerExports, React);
      
      const useController = controllerExports.useController;
      if (!useController) {
        throw new Error("controller.js does not export useController");
      }

      const componentExports: any = {};
      const jsxFn = new Function("exports", "React", "useController", transpileJsx || "");
      jsxFn(componentExports, React, useController);

      const WidgetComponent = componentExports.default || Object.values(componentExports)[0];
      if (!WidgetComponent) {
        throw new Error("index.jsx does not export a default component");
      }

      return WidgetComponent as React.ComponentType<{ ambient: any }>;
    } catch (err: any) {
      console.error("Compilation error in React widget:", err);
      return () => (
        <div className="p-4 bg-red-950/40 border border-red-500/20 text-red-400 rounded-xl text-xs font-mono">
          <strong className="block mb-1">React Compiling Error:</strong>
          {err.message}
        </div>
      );
    }
  }, [widget.jsx, widget.js]);

  const API_BASE = `http://${window.location.hostname}:8000`;
  const customListenersRef = useRef<{ event: string; handler: EventListener }[]>([]);

  const ambientProps = useMemo(() => {
    return {
      sendMessage: (text: string) => {
        wsService.sendMessage({
          sender: "user",
          content: text,
        });
      },
      fullscreen: () => {
        if (onFullscreenRef.current) {
          onFullscreenRef.current(widget.id);
        }
      },
      minimize: () => {
        if (onMinimizeRef.current) {
          onMinimizeRef.current(widget.id);
        }
      },
      graph: {
        subscribe: (query: any, callback: (data: any) => void) => {
          const subId = `sub-${Math.random().toString(36).substring(2, 11)}`;
          const handler = (e: Event) => {
            callback((e as CustomEvent).detail);
          };
          const eventName = `graph_query_update:${subId}`;
          window.addEventListener(eventName, handler);
          customListenersRef.current.push({ event: eventName, handler });

          wsService.sendMessage({
            type: "graph_subscribe",
            subscription_id: subId,
            query: query
          });

          return () => {
            window.removeEventListener(eventName, handler);
            const idx = customListenersRef.current.findIndex(l => l.event === eventName && l.handler === handler);
            if (idx !== -1) customListenersRef.current.splice(idx, 1);

            wsService.sendMessage({
              type: "graph_unsubscribe",
              subscription_id: subId
            });
          };
        },
        mutate: async (actions: any[]) => {
          const res = await fetch(`${API_BASE}/api/graph/mutate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ actions })
          });
          if (!res.ok) throw new Error("Failed to mutate graph");
          return res.json();
        }
      }
    };
  }, [widget.id]);

  useEffect(() => {
    return () => {
      customListenersRef.current.forEach(({ event, handler }) => {
        window.removeEventListener(event, handler);
      });
    };
  }, []);

  // --- Legacy Script Execution & Scope Setup ---
  useEffect(() => {
    if (widget.layout) return; // Skip DOM mounting logic for A2UI widgets

    if (!containerRef.current) return;

    // Clear previous contents
    containerRef.current.innerHTML = "";

    // 1. Setup scope attribute
    const scopeId = `widget-${widget.id}`;
    containerRef.current.setAttribute("data-widget-scope", scopeId);

    // 2. Append scoped CSS style block
    if (widget.css) {
      const styleEl = document.createElement("style");
      styleEl.textContent = scopeCss(widget.css, scopeId);
      containerRef.current.appendChild(styleEl);
    }

    // 3. Create HTML content container
    const contentEl = document.createElement("div");
    contentEl.className = "widget-root-content w-full h-full";
    contentEl.innerHTML = widget.html || "";
    containerRef.current.appendChild(contentEl);

    // Track event listeners created inside the SDK to clean them up on unmount
    const customListeners: { event: string; handler: EventListener }[] = [];

    const API_BASE = `http://${window.location.hostname}:8000`;

    const agentListeners: { [eventType: string]: Function[] } = {};
    const legacyState: any = {};

    // 4. Construct ambient SDK (excluding deprecated standard UI components)
    const ambient = {
      sendMessage: (text: string) => {
        wsService.sendMessage({
          sender: "user",
          content: text,
        });
      },
      fullscreen: () => {
        if (onFullscreenRef.current) {
          onFullscreenRef.current(widget.id);
        }
      },
      minimize: () => {
        if (onMinimizeRef.current) {
          onMinimizeRef.current(widget.id);
        }
      },
      graph: {
        subscribe: (query: any, callback: (data: any) => void) => {
          const subId = `sub-${Math.random().toString(36).substring(2, 11)}`;
          const handler = (e: Event) => {
            callback((e as CustomEvent).detail);
          };
          const eventName = `graph_query_update:${subId}`;
          window.addEventListener(eventName, handler);
          customListeners.push({ event: eventName, handler });

          wsService.sendMessage({
            type: "graph_subscribe",
            subscription_id: subId,
            query: query
          });

          return () => {
            window.removeEventListener(eventName, handler);
            const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
            if (idx !== -1) customListeners.splice(idx, 1);

            wsService.sendMessage({
              type: "graph_unsubscribe",
              subscription_id: subId
            });
          };
        },
        mutate: async (actions: any[]) => {
          const res = await fetch(`${API_BASE}/api/graph/mutate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ actions })
          });
          if (!res.ok) throw new Error("Failed to mutate graph");
          return res.json();
        }
      },
      agent: {
        connect: () => {
          const eventName = `ag_ui_event:${widget.id}`;
          const handler = (e: Event) => {
            const ev = (e as CustomEvent).detail;
            const type = ev.type;
            if (type === "STATE_SNAPSHOT") {
              Object.assign(legacyState, ev.state || {});
            } else if (type === "STATE_DELTA") {
              if (ev.state) {
                Object.assign(legacyState, ev.state);
              }
            }
            if (agentListeners[type]) {
              agentListeners[type].forEach((cb) => cb(ev));
            }
            if (agentListeners["*"]) {
              agentListeners["*"].forEach((cb) => cb(ev));
            }
          };
          window.addEventListener(eventName, handler);
          customListeners.push({ event: eventName, handler });

          wsService.sendMessage({
            type: "ag_ui_message",
            app_id: widget.id,
            message: { type: "connect" }
          });

          return () => {
            window.removeEventListener(eventName, handler);
            const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
            if (idx !== -1) customListeners.splice(idx, 1);
          };
        },
        send: (msg: any) => {
          wsService.sendMessage({
            type: "ag_ui_message",
            app_id: widget.id,
            message: msg
          });
        },
        on: (eventType: string, callback: Function) => {
          if (!agentListeners[eventType]) {
            agentListeners[eventType] = [];
          }
          agentListeners[eventType].push(callback);
          return () => {
            agentListeners[eventType] = agentListeners[eventType].filter((cb) => cb !== callback);
          };
        },
        state: {
          get: (pointer: string) => getValueByPointer(legacyState, pointer),
          set: (pointer: string, val: any) => {
            setValueByPointer(legacyState, pointer, val);
            wsService.sendMessage({
              type: "ag_ui_message",
              app_id: widget.id,
              message: {
                type: "STATE_DELTA",
                delta: { [pointer]: val }
              }
            });
          }
        }
      },
      mcp: {
        callTool: (name: string, args: any) => {
          return new Promise((resolve, reject) => {
            const callId = `call-${Math.random().toString(36).substring(2, 11)}`;
            const eventName = `mcp_call_response:${widget.id}:${callId}`;
            const handler = (e: Event) => {
              window.removeEventListener(eventName, handler);
              const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
              if (idx !== -1) customListeners.splice(idx, 1);
              
              const resData = (e as CustomEvent).detail;
              if (resData.error) {
                reject(new Error(resData.error));
              } else {
                resolve(resData.result);
              }
            };
            window.addEventListener(eventName, handler);
            customListeners.push({ event: eventName, handler });

            wsService.sendMessage({
              type: "mcp_call_tool",
              app_id: widget.id,
              call_id: callId,
              name: name,
              arguments: args
            });
          });
        },
        readResource: (uri: string) => {
          return new Promise((resolve, reject) => {
            const callId = `call-${Math.random().toString(36).substring(2, 11)}`;
            const eventName = `mcp_read_response:${widget.id}:${callId}`;
            const handler = (e: Event) => {
              window.removeEventListener(eventName, handler);
              const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
              if (idx !== -1) customListeners.splice(idx, 1);
              
              const resData = (e as CustomEvent).detail;
              if (resData.error) {
                reject(new Error(resData.error));
              } else {
                resolve(resData.result);
              }
            };
            window.addEventListener(eventName, handler);
            customListeners.push({ event: eventName, handler });

            wsService.sendMessage({
              type: "mcp_read_resource",
              app_id: widget.id,
              call_id: callId,
              uri: uri
            });
          });
        }
      }
    };

    // Custom fetch interceptor to cache external JSON API requests
    const customFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : (input as any).url || input.toString();
      const isGet = !init || !init.method || init.method.toUpperCase() === "GET";
      const isExternal = url.startsWith("http") && !url.includes(window.location.hostname);
      
      if (isGet && isExternal) {
        const cached = globalFetchCache.get(url);
        if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
          console.log(`[Sandbox Fetch Cache] Hit for ${url}`);
          return new Response(JSON.stringify(cached.data), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
      }
      
      const res = await window.fetch(input, init);
      
      if (isGet && isExternal && res.ok) {
        try {
          const clone = res.clone();
          const json = await clone.json();
          globalFetchCache.set(url, { data: json, timestamp: Date.now() });
        } catch (e) {
          // Ignore non-JSON or clone failures
        }
      }
      return res;
    };

    // 5. Run JS in isolated function scope passing root element, ambient SDK, and cached fetch
    if (widget.js) {
      try {
        const runScript = new Function("root", "ambient", "fetch", widget.js);
        runScript(contentEl, ambient, customFetch);
      } catch (err) {
        console.error(`JS error in widget ${widget.id}:`, err);
        const errorEl = document.createElement("div");
        errorEl.className = "text-red-400 text-xs p-2 mt-2 bg-red-950/20 border border-red-900/30 rounded";
        errorEl.textContent = `Widget JS Error: ${(err as Error).message}`;
        containerRef.current.appendChild(errorEl);
      }
    }

    // Clean up event listeners on unmount
    return () => {
      customListeners.forEach(({ event, handler }) => {
        window.removeEventListener(event, handler);
      });
    };
  }, [widget.id, widget.layout, widget.html, widget.css, widget.js]);

  // --- A2UI Sandbox Controller Lifecycle ---
  useEffect(() => {
    if (!widget.layout) return;

    const listeners: { [actionId: string]: Function } = {};
    listenersRef.current = listeners;

    const changeListeners: { [pointer: string]: Function[] } = {};

    const customListeners: { event: string; handler: EventListener }[] = [];
    const API_BASE = `http://${window.location.hostname}:8000`;

    // 1. Build Isolated State SDK
    const stateSDK = {
      get: (pointer: string) => {
        return getValueByPointer(stateRef.current, pointer);
      },
      set: (pointer: string, val: any) => {
        setLocalState((prev: any) => {
          const next = setValueByPointer(prev, pointer, val);
          stateRef.current = next;
          // Trigger listeners
          if (changeListeners[pointer]) {
            changeListeners[pointer].forEach((cb) => cb(val));
          }
          return next;
        });
      },
      onChange: (pointer: string, callback: Function) => {
        if (!changeListeners[pointer]) changeListeners[pointer] = [];
        changeListeners[pointer].push(callback);
        return () => {
          changeListeners[pointer] = changeListeners[pointer].filter((cb) => cb !== callback);
        };
      }
    };

    // 2. Build Isolated UI Events SDK
    const uiSDK = {
      on: (event: string, actionId: string, callback: Function) => {
        listeners[actionId] = callback;
      }
    };

    const agentListeners: { [eventType: string]: Function[] } = {};

    // 3. Build SDK Context
    const ambient = {
      sendMessage: (text: string) => {
        wsService.sendMessage({
          sender: "user",
          content: text,
        });
      },
      fullscreen: () => {
        if (onFullscreenRef.current) {
          onFullscreenRef.current(widget.id);
        }
      },
      minimize: () => {
        if (onMinimizeRef.current) {
          onMinimizeRef.current(widget.id);
        }
      },
      state: stateSDK,
      ui: uiSDK,
      graph: {
        subscribe: (query: any, callback: (data: any) => void) => {
          const subId = `sub-${Math.random().toString(36).substring(2, 11)}`;
          const handler = (e: Event) => {
            callback((e as CustomEvent).detail);
          };
          const eventName = `graph_query_update:${subId}`;
          window.addEventListener(eventName, handler);
          customListeners.push({ event: eventName, handler });

          wsService.sendMessage({
            type: "graph_subscribe",
            subscription_id: subId,
            query: query
          });

          return () => {
            window.removeEventListener(eventName, handler);
            const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
            if (idx !== -1) customListeners.splice(idx, 1);

            wsService.sendMessage({
              type: "graph_unsubscribe",
              subscription_id: subId
            });
          };
        },
        mutate: async (actions: any[]) => {
          const res = await fetch(`${API_BASE}/api/graph/mutate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ actions })
          });
          if (!res.ok) throw new Error("Failed to mutate graph");
          return res.json();
        }
      },
      agent: {
        connect: () => {
          const eventName = `ag_ui_event:${widget.id}`;
          const handler = (e: Event) => {
            const ev = (e as CustomEvent).detail;
            const type = ev.type;
            if (type === "STATE_SNAPSHOT") {
              setLocalState(ev.state || {});
            } else if (type === "STATE_DELTA") {
              if (ev.state) {
                setLocalState((prev: any) => ({ ...prev, ...ev.state }));
              }
            }
            if (agentListeners[type]) {
              agentListeners[type].forEach((cb) => cb(ev));
            }
            if (agentListeners["*"]) {
              agentListeners["*"].forEach((cb) => cb(ev));
            }
          };
          window.addEventListener(eventName, handler);
          customListeners.push({ event: eventName, handler });

          wsService.sendMessage({
            type: "ag_ui_message",
            app_id: widget.id,
            message: { type: "connect" }
          });

          return () => {
            window.removeEventListener(eventName, handler);
            const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
            if (idx !== -1) customListeners.splice(idx, 1);
          };
        },
        send: (msg: any) => {
          wsService.sendMessage({
            type: "ag_ui_message",
            app_id: widget.id,
            message: msg
          });
        },
        on: (eventType: string, callback: Function) => {
          if (!agentListeners[eventType]) {
            agentListeners[eventType] = [];
          }
          agentListeners[eventType].push(callback);
          return () => {
            agentListeners[eventType] = agentListeners[eventType].filter((cb) => cb !== callback);
          };
        },
        state: {
          get: (pointer: string) => stateSDK.get(pointer),
          set: (pointer: string, val: any) => {
            stateSDK.set(pointer, val);
            wsService.sendMessage({
              type: "ag_ui_message",
              app_id: widget.id,
              message: {
                type: "STATE_DELTA",
                delta: { [pointer]: val }
              }
            });
          }
        }
      },
      mcp: {
        callTool: (name: string, args: any) => {
          return new Promise((resolve, reject) => {
            const callId = `call-${Math.random().toString(36).substring(2, 11)}`;
            const eventName = `mcp_call_response:${widget.id}:${callId}`;
            const handler = (e: Event) => {
              window.removeEventListener(eventName, handler);
              const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
              if (idx !== -1) customListeners.splice(idx, 1);
              
              const resData = (e as CustomEvent).detail;
              if (resData.error) {
                reject(new Error(resData.error));
              } else {
                resolve(resData.result);
              }
            };
            window.addEventListener(eventName, handler);
            customListeners.push({ event: eventName, handler });

            wsService.sendMessage({
              type: "mcp_call_tool",
              app_id: widget.id,
              call_id: callId,
              name: name,
              arguments: args
            });
          });
        },
        readResource: (uri: string) => {
          return new Promise((resolve, reject) => {
            const callId = `call-${Math.random().toString(36).substring(2, 11)}`;
            const eventName = `mcp_read_response:${widget.id}:${callId}`;
            const handler = (e: Event) => {
              window.removeEventListener(eventName, handler);
              const idx = customListeners.findIndex(l => l.event === eventName && l.handler === handler);
              if (idx !== -1) customListeners.splice(idx, 1);
              
              const resData = (e as CustomEvent).detail;
              if (resData.error) {
                reject(new Error(resData.error));
              } else {
                resolve(resData.result);
              }
            };
            window.addEventListener(eventName, handler);
            customListeners.push({ event: eventName, handler });

            wsService.sendMessage({
              type: "mcp_read_resource",
              app_id: widget.id,
              call_id: callId,
              uri: uri
            });
          });
        }
      }
    };

    // 4. Run Controller in isolated function scope passing root, ambient, and native fetch
    if (widget.js) {
      try {
        const runScript = new Function("root", "ambient", "fetch", widget.js);
        runScript(containerRef.current, ambient, window.fetch);
      } catch (err) {
        console.error(`JS error in A2UI controller ${widget.id}:`, err);
      }
    }

    return () => {
      customListeners.forEach(({ event, handler }) => {
        window.removeEventListener(event, handler);
      });
    };
  }, [widget.id, widget.layout, widget.js]);

  // --- A2UI Adjacency List Parsing & Component Map ---
  const parsedComponents = useMemo(() => {
    try {
      return JSON.parse(widget.layout || "[]") as any[];
    } catch (e) {
      console.error("Failed to parse widget layout JSON:", e);
      return [];
    }
  }, [widget.layout]);

  const componentsMap = useMemo(() => {
    const map = new Map<string, any>();
    for (const comp of parsedComponents) {
      map.set(comp.id, comp);
    }
    return map;
  }, [parsedComponents]);

  // --- Recursive Render Function ---
  const renderComponent = (id: string): React.ReactNode => {
    const comp = componentsMap.get(id);
    if (!comp) return null;

    const props = comp.props || {};
    const children = comp.children || [];
    const { itemStyle, ...resolvedStyle } = props.style || {};

    switch (comp.type) {
      case "Column": {
        const actionId = comp.events?.onClick?.actionId;
        return (
          <div
            key={id}
            onClick={actionId ? () => listenersRef.current[actionId]?.() : undefined}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: props.gap,
              padding: props.padding,
              cursor: actionId ? "pointer" : undefined,
              ...resolvedStyle
            }}
          >
            {children.map(renderComponent)}
          </div>
        );
      }
      case "Row": {
        const actionId = comp.events?.onClick?.actionId;
        return (
          <div
            key={id}
            onClick={actionId ? () => listenersRef.current[actionId]?.() : undefined}
            style={{
              display: "flex",
              flexDirection: "row",
              gap: props.gap,
              padding: props.padding,
              alignItems: props.align === "center" ? "center" : props.align === "end" ? "flex-end" : "flex-start",
              cursor: actionId ? "pointer" : undefined,
              ...resolvedStyle
            }}
          >
            {children.map(renderComponent)}
          </div>
        );
      }
      case "Card": {
        const actionId = comp.events?.onClick?.actionId;
        return (
          <div
            key={id}
            onClick={actionId ? () => listenersRef.current[actionId]?.() : undefined}
            style={{
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: "8px",
              padding: "16px",
              backgroundColor: "rgba(30,41,59,0.3)",
              cursor: actionId ? "pointer" : undefined,
              ...resolvedStyle
            }}
          >
            {props.title && <h3 style={{ fontSize: "14px", fontWeight: "600", marginBottom: "12px", color: "rgba(255,255,255,0.9)", borderBottom: "1px solid rgba(255,255,255,0.06)", paddingBottom: "6px" }}>{props.title}</h3>}
            {children.map(renderComponent)}
          </div>
        );
      }
      case "Text": {
        const actionId = comp.events?.onClick?.actionId;
        return (
          <span
            key={id}
            onClick={actionId ? () => listenersRef.current[actionId]?.() : undefined}
            style={{
              cursor: actionId ? "pointer" : undefined,
              ...resolvedStyle
            }}
          >
            {resolveProp(props.text, localState)}
          </span>
        );
      }
      case "Button":
        return (
          <button
            key={id}
            onClick={() => {
              const actionId = comp.events?.onClick?.actionId;
              if (actionId && listenersRef.current[actionId]) {
                listenersRef.current[actionId]();
              }
            }}
            style={{
              padding: "6px 14px",
              borderRadius: "6px",
              cursor: "pointer",
              border: "none",
              fontWeight: "600",
              fontSize: "13px",
              backgroundColor: props.variant === "danger" ? "#ef4444" : props.variant === "secondary" ? "#475569" : "#2563eb",
              color: "#ffffff",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              ...resolvedStyle
            }}
          >
            {props.label}
          </button>
        );
      case "TextField":
        return (
          <div key={id} style={{ display: "flex", flexDirection: "column", gap: "6px", ...resolvedStyle }}>
            {props.label && <label style={{ fontSize: "12px", fontWeight: "500", color: "rgba(255,255,255,0.5)" }}>{props.label}</label>}
            <input
              type="text"
              placeholder={props.placeholder}
              value={resolveProp(props.value, localState) ?? ""}
              onChange={(e) => {
                const binding = props.value?.binding;
                if (binding) {
                  setLocalState((prev: any) => setValueByPointer(prev, binding, e.target.value));
                }
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const actionId = comp.events?.onEnter?.actionId || comp.events?.onSubmit?.actionId;
                  if (actionId && listenersRef.current[actionId]) {
                    listenersRef.current[actionId](e.currentTarget.value);
                  }
                }
              }}
              style={{
                padding: "8px 12px",
                borderRadius: "6px",
                backgroundColor: "rgba(15,23,42,0.4)",
                border: "1px solid rgba(255,255,255,0.08)",
                color: "#ffffff",
                fontSize: "13px",
                outline: "none",
                width: "100%"
              }}
            />
          </div>
        );
      case "Checkbox":
        return (
          <label key={id} style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", ...resolvedStyle }}>
            <input
              type="checkbox"
              checked={!!resolveProp(props.checked, localState)}
              onChange={(e) => {
                const binding = props.checked?.binding;
                if (binding) {
                  setLocalState((prev: any) => setValueByPointer(prev, binding, e.target.checked));
                }
                const actionId = comp.events?.onChange?.actionId;
                if (actionId && listenersRef.current[actionId]) {
                  listenersRef.current[actionId](e.target.checked);
                }
              }}
              style={{ cursor: "pointer" }}
            />
            <span style={{ fontSize: "13px", color: "rgba(255,255,255,0.8)" }}>{props.label}</span>
          </label>
        );
      case "List": {
        const actionId = comp.events?.onItemClick?.actionId || comp.events?.onClick?.actionId;
        return (
          <div key={id} style={{ display: "flex", flexDirection: "column", gap: "6px", ...resolvedStyle }}>
            {(resolveProp(props.items, localState) || []).map((item: any, idx: number) => {
              const handleItemClick = () => {
                if (actionId && listenersRef.current[actionId]) {
                  listenersRef.current[actionId](item, idx);
                }
              };
              
              const currentItemStyle = {
                padding: "8px 12px",
                borderRadius: "6px",
                backgroundColor: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.03)",
                fontSize: "13px",
                cursor: actionId ? "pointer" : "default",
                ...itemStyle
              };

              let itemContent;
              if (typeof item === "string" || typeof item === "number") {
                itemContent = item;
              } else if (item && typeof item === "object") {
                if ("label" in item) {
                  itemContent = <span>{String(item.label)}</span>;
                } else if ("name" in item) {
                  itemContent = <span>{String(item.name)}</span>;
                } else {
                  itemContent = (
                    <div style={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
                      {Object.entries(item).filter(([k]) => k !== "id" && k !== "type").map(([k, v]) => (
                        <span key={k} style={{ marginRight: "12px" }}><strong>{k}:</strong> {String(v)}</span>
                      ))}
                    </div>
                  );
                }
              }

              return (
                <div
                  key={idx}
                  onClick={handleItemClick}
                  style={currentItemStyle}
                >
                  {itemContent}
                </div>
              );
            })}
          </div>
        );
      }
      case "Table": {
        const rows = resolveProp(props.rows, localState) || [];
        const actionId = comp.events?.onRowClick?.actionId || comp.events?.onClick?.actionId;
        return (
          <div key={id} style={{ overflowX: "auto", width: "100%", ...resolvedStyle }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "13px" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.15)" }}>
                  {(props.columns || []).map((col: string, idx: number) => (
                    <th key={idx} style={{ padding: "8px", fontWeight: "600", color: "rgba(255,255,255,0.6)" }}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row: any, rIdx: number) => {
                  const cells = Array.isArray(row) ? row : Object.values(row);
                  const handleRowClick = () => {
                    if (actionId && listenersRef.current[actionId]) {
                      listenersRef.current[actionId](row, rIdx);
                    }
                  };
                  return (
                    <tr
                      key={rIdx}
                      onClick={handleRowClick}
                      style={{
                        borderBottom: "1px solid rgba(255,255,255,0.05)",
                        cursor: actionId ? "pointer" : "default",
                        transition: "background-color 0.2s"
                      }}
                      onMouseEnter={(e) => {
                        if (actionId) e.currentTarget.style.backgroundColor = "rgba(255,255,255,0.02)";
                      }}
                      onMouseLeave={(e) => {
                        if (actionId) e.currentTarget.style.backgroundColor = "";
                      }}
                    >
                      {cells.map((cell: any, cIdx: number) => (
                        <td key={cIdx} style={{ padding: "8px", color: "rgba(255,255,255,0.8)" }}>{String(cell)}</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      }
      default:
        return null;
    }
  };

  if (widget.jsx) {
    const Component = DynamicReactComponent;
    return (
      <div
        id={widget.id}
        data-testid={`sandbox-${widget.id}`}
        className="w-full h-full text-white/90 overflow-auto"
      >
        {Component ? (
          <ErrorBoundary>
            <Component ambient={ambientProps} />
          </ErrorBoundary>
        ) : (
          <div className="p-4 text-slate-400 text-xs italic">Compiling React widget...</div>
        )}
      </div>
    );
  }

  if (widget.layout) {
    return (
      <div
        ref={containerRef}
        id={widget.id}
        data-testid={`sandbox-${widget.id}`}
        className="w-full h-full text-white/90 overflow-auto p-4 bg-slate-900/10 rounded-lg"
      >
        {renderComponent("root")}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      id={widget.id}
      data-testid={`sandbox-${widget.id}`}
      className="w-full h-full text-white/90 overflow-auto"
    />
  );
};
