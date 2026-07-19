import React, { useEffect, useRef, useState, useMemo } from "react";
import type { Widget } from "./DashboardCanvas";
import wsService from "../services/websocket";
import { runService } from "../services/runs";
import * as Babel from "@babel/standalone";
import { ErrorBoundary } from "./ErrorBoundary";
import htm from "htm";

const html = htm.bind(React.createElement);
const API_BASE = `http://${window.location.hostname}:8000`;

// Pre-defined React components for ambient.components unified scheme
const Column = ({ children, gap, padding, style, onClick, ...rest }: any) => {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: gap,
        padding: padding,
        cursor: onClick ? "pointer" : undefined,
        ...style
      }}
      {...rest}
    >
      {children}
    </div>
  );
};

const Row = ({ children, gap, padding, align, style, onClick, ...rest }: any) => {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: "row",
        gap: gap,
        padding: padding,
        alignItems: align === "center" ? "center" : align === "end" ? "flex-end" : "flex-start",
        cursor: onClick ? "pointer" : undefined,
        ...style
      }}
      {...rest}
    >
      {children}
    </div>
  );
};

const Card = ({ title, children, style, onClick, ...rest }: any) => {
  return (
    <div
      onClick={onClick}
      style={{
        border: "1px solid var(--widget-border, rgba(255,255,255,0.08))",
        borderRadius: "12px",
        padding: "16px",
        color: "var(--widget-text, rgba(255,255,255,0.9))",
        backgroundColor: "var(--widget-surface, rgba(30,41,59,0.3))",
        cursor: onClick ? "pointer" : undefined,
        ...style
      }}
      {...rest}
    >
      {title && <h3 style={{ fontSize: "14px", fontWeight: "600", marginBottom: "12px", color: "var(--widget-text, rgba(255,255,255,0.9))", borderBottom: "1px solid var(--widget-border, rgba(255,255,255,0.06))", paddingBottom: "6px" }}>{title}</h3>}
      {children}
    </div>
  );
};

const Text = ({ text, style, onClick, ...rest }: any) => {
  return (
    <span
      onClick={onClick}
      style={{
        cursor: onClick ? "pointer" : undefined,
        ...style
      }}
      {...rest}
    >
      {text}
    </span>
  );
};

const Button = ({ label, variant, style, onClick, ...rest }: any) => {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "6px 14px",
        borderRadius: "6px",
        cursor: "pointer",
        border: "none",
        fontWeight: "600",
        fontSize: "13px",
        backgroundColor: variant === "danger" ? "#ef4444" : variant === "secondary" ? "var(--widget-control, #475569)" : "var(--accent, #2563eb)",
        color: "#ffffff",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        ...style
      }}
      {...rest}
    >
      {label}
    </button>
  );
};

const TextField = ({ label, placeholder, value, onChange, onEnter, style, ...rest }: any) => {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px", ...style }}>
      {label && <label style={{ fontSize: "12px", fontWeight: "500", color: "var(--widget-muted, rgba(255,255,255,0.5))" }}>{label}</label>}
      <input
        type="text"
        placeholder={placeholder}
        value={value ?? ""}
        onChange={onChange}
        onKeyDown={(e) => {
          if (e.key === "Enter" && onEnter) {
            onEnter(e.currentTarget.value);
          }
        }}
        style={{
          padding: "8px 12px",
          borderRadius: "6px",
          backgroundColor: "var(--widget-input, rgba(15,23,42,0.4))",
          border: "1px solid var(--widget-border, rgba(255,255,255,0.08))",
          color: "var(--widget-text, #ffffff)",
          fontSize: "13px",
          outline: "none",
          width: "100%"
        }}
        {...rest}
      />
    </div>
  );
};

const Checkbox = ({ label, checked, onChange, style, ...rest }: any) => {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", ...style }}>
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange && onChange(e.target.checked)}
        style={{ cursor: "pointer" }}
        {...rest}
      />
      <span style={{ fontSize: "13px", color: "var(--widget-text, rgba(255,255,255,0.8))" }}>{label}</span>
    </label>
  );
};

const List = ({ items, itemStyle, onItemClick, style, ...rest }: any) => {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px", ...style }} {...rest}>
      {(items || []).map((item: any, idx: number) => {
        const handleItemClick = () => {
          if (onItemClick) onItemClick(item, idx);
        };
        
        const currentItemStyle = {
          padding: "8px 12px",
          borderRadius: "6px",
          backgroundColor: "var(--widget-surface-soft, rgba(255,255,255,0.02))",
          border: "1px solid var(--widget-border, rgba(255,255,255,0.03))",
          fontSize: "13px",
          cursor: onItemClick ? "pointer" : "default",
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
          <div key={idx} onClick={handleItemClick} style={currentItemStyle}>
            {itemContent}
          </div>
        );
      })}
    </div>
  );
};

const Table = ({ columns, rows, onRowClick, style, ...rest }: any) => {
  return (
    <div style={{ overflowX: "auto", width: "100%", ...style }} {...rest}>
      <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "13px" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.15)" }}>
            {(columns || []).map((col: string, idx: number) => (
              <th key={idx} style={{ padding: "8px", fontWeight: "600", color: "rgba(255,255,255,0.6)" }}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(rows || []).map((row: any, rIdx: number) => {
            const cells = Array.isArray(row) ? row : Object.values(row);
            const handleRowClick = () => {
              if (onRowClick) onRowClick(row, rIdx);
            };
            return (
              <tr
                key={rIdx}
                onClick={handleRowClick}
                style={{
                  borderBottom: "1px solid rgba(255,255,255,0.05)",
                  cursor: onRowClick ? "pointer" : "default",
                  transition: "background-color 0.2s"
                }}
                onMouseEnter={(e) => {
                  if (onRowClick) e.currentTarget.style.backgroundColor = "rgba(255,255,255,0.02)";
                }}
                onMouseLeave={(e) => {
                  if (onRowClick) e.currentTarget.style.backgroundColor = "";
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
};

const ambientComponents = {
  Column,
  Row,
  Card,
  Text,
  Button,
  TextField,
  Checkbox,
  List,
  Table
};

// --- SandboxWidget Prop Interface ---

interface SandboxWidgetProps {
  widget: Widget;
  onFullscreen?: (id: string) => void;
  onMinimize?: (id: string) => void;
}

export const SandboxWidget: React.FC<SandboxWidgetProps> = ({
  widget,
  onFullscreen,
  onMinimize,
}) => {
  const onFullscreenRef = useRef(onFullscreen);
  const onMinimizeRef = useRef(onMinimize);

  useEffect(() => {
    onFullscreenRef.current = onFullscreen;
    onMinimizeRef.current = onMinimize;
  }, [onFullscreen, onMinimize]);

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
      theme: {
        get preference() {
          return document.documentElement.dataset.themePreference || "system";
        },
        get effective() {
          return document.documentElement.dataset.theme || "dark";
        },
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

          const registrationKey = `graph:${subId}`;
          wsService.registerPersistentMessage(registrationKey, {
            type: "graph_subscribe",
            subscription_id: subId,
            query: query
          });

          return () => {
            window.removeEventListener(eventName, handler);
            const idx = customListenersRef.current.findIndex(l => l.event === eventName && l.handler === handler);
            if (idx !== -1) customListenersRef.current.splice(idx, 1);

            wsService.unregisterPersistentMessage(registrationKey, {
              type: "graph_unsubscribe",
              subscription_id: subId
            });
          };
        },
        mutate: async (actions: any[]) => {
          const invocationId = typeof crypto.randomUUID === "function"
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
          const res = await fetch(`${API_BASE}/api/graph/mutate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              actions,
              idempotency_key: `widget:${widget.id}:${invocationId}`
            })
          });
          if (!res.ok) throw new Error("Failed to mutate graph");
          return res.json();
        }
      },
      capabilities: {
        invoke: async (catalogId: string, input: any, actionId?: string) => {
          const run = await runService.start(catalogId, actionId, input);
          return runService.wait(run.id);
        },
      },
      runs: {
        start: (catalogId: string, actionId: string | undefined, input: any) => runService.start(catalogId, actionId, input),
        get: (runId: string) => runService.get(runId),
        cancel: (runId: string) => runService.cancel(runId),
        subscribe: (runId: string, callback: (event: any) => void) => {
          const handler = (event: Event) => callback((event as CustomEvent).detail);
          const eventName = `ambient_run_event:${runId}`;
          window.addEventListener(eventName, handler);
          customListenersRef.current.push({ event: eventName, handler });
          runService.connect();
          return () => {
            window.removeEventListener(eventName, handler);
            const index = customListenersRef.current.findIndex((listener) => listener.event === eventName && listener.handler === handler);
            if (index >= 0) customListenersRef.current.splice(index, 1);
          };
        },
      },
      mcp: {
        callTool: (name: string, args: any) => {
          return new Promise((resolve, reject) => {
            const callId = `call-${Math.random().toString(36).substring(2, 11)}`;
            const eventName = `mcp_call_response:${widget.id}:${callId}`;
            const handler = (e: Event) => {
              window.removeEventListener(eventName, handler);
              const idx = customListenersRef.current.findIndex(l => l.event === eventName && l.handler === handler);
              if (idx !== -1) customListenersRef.current.splice(idx, 1);
              
              const resData = (e as CustomEvent).detail;
              if (resData.error) {
                reject(new Error(resData.error));
              } else {
                resolve(resData.result);
              }
            };
            window.addEventListener(eventName, handler);
            customListenersRef.current.push({ event: eventName, handler });

            wsService.sendMessage({
              type: "mcp_call_tool",
              app_id: widget.id,
              call_id: callId,
              name: name,
              arguments: args
            });
          });
        }
      },
      html: html,
      components: ambientComponents,
      react: {
        useState,
        useEffect,
        useMemo,
        useRef,
        useCallback: React.useCallback,
        useContext: React.useContext,
        useReducer: React.useReducer
      }
    };
  }, [widget.id]);

  useEffect(() => {
    const listeners = customListenersRef.current;
    return () => {
      listeners.forEach(({ event, handler }) => {
        window.removeEventListener(event, handler);
      });
    };
  }, []);

  // --- React + Tailwind Dynamic Compilation ---
  const DynamicReactComponent = useMemo(() => {
    try {
      // Unified HTM Mode
      const scriptJs = widget.js || "";
      const transpileScript = Babel.transform(scriptJs, {
        presets: [["react", { runtime: "classic" }]],
        plugins: ["transform-modules-commonjs"],
        filename: "widget.js"
      }).code;

      const exportsObj: any = {};
      const runScript = new Function("exports", "React", "ambient", transpileScript || "");
      runScript(exportsObj, React, ambientProps);

      const WidgetComponent = exportsObj.default || Object.values(exportsObj)[0];
      if (!WidgetComponent) {
        throw new Error("widget.js does not export a default component");
      }

      return WidgetComponent as React.ComponentType<{ ambient: any }>;
    } catch (err: any) {
      console.error("Compilation error in React/HTM widget:", err);
      return () => (
        <div className="p-4 bg-red-950/40 border border-red-500/20 text-red-400 rounded-xl text-xs font-mono">
          <strong className="block mb-1">React/HTM Compiling Error:</strong>
          {err.message}
        </div>
      );
    }
  }, [widget.js, ambientProps]);

  const Component = DynamicReactComponent;
  return (
    <div
      id={widget.id}
      data-testid={`sandbox-${widget.id}`}
      className="ambient-widget-root w-full h-full overflow-auto"
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
};
