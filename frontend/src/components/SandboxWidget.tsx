import React, { useEffect, useRef } from "react";
import type { Widget } from "./DashboardCanvas";
import wsService from "../services/websocket";

interface SandboxWidgetProps {
  widget: Widget;
  onFullscreen?: (id: string) => void;
  onMinimize?: (id: string) => void;
}

// Global fetch cache to share API responses across all mounts and sandbox instances
const globalFetchCache = new Map<string, { data: any; timestamp: number }>();
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes TTL

// Scopes widget-defined CSS rules to prevent global layout pollution
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

export const SandboxWidget: React.FC<SandboxWidgetProps> = ({
  widget,
  onFullscreen,
  onMinimize,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const onFullscreenRef = useRef(onFullscreen);
  const onMinimizeRef = useRef(onMinimize);

  useEffect(() => {
    onFullscreenRef.current = onFullscreen;
    onMinimizeRef.current = onMinimize;
  }, [onFullscreen, onMinimize]);

  useEffect(() => {
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

    // 4. Construct ambient SDK
    const ambient = {
      model: {
        get: async () => {
          const res = await fetch(`${API_BASE}/api/apps/${widget.id}/data`);
          if (!res.ok) throw new Error("Failed to load app data");
          return res.json();
        },
        set: async (data: any) => {
          const res = await fetch(`${API_BASE}/api/apps/${widget.id}/data`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          if (!res.ok) throw new Error("Failed to save app data");
          return res.json();
        },
        onChange: (callback: (data: any) => void) => {
          const handler = (e: Event) => {
            callback((e as CustomEvent).detail);
          };
          const eventName = `app_data_update:${widget.id}`;
          window.addEventListener(eventName, handler);
          customListeners.push({ event: eventName, handler });
        },
      },
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
  }, [widget.id, widget.html, widget.css, widget.js]);

  return (
    <div
      ref={containerRef}
      id={widget.id}
      data-testid={`sandbox-${widget.id}`}
      className="w-full h-full text-white/90 overflow-auto"
    />
  );
};
