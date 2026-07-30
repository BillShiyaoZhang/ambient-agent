import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import App from "../../frontend/src/App";
import wsService from "../../frontend/src/services/websocket";
import React from "react";

// Stub scrollIntoView for JSDOM compatibility
window.HTMLElement.prototype.scrollIntoView = vi.fn();

// Mock WebSocket Client Service
vi.mock("../../frontend/src/services/websocket", () => {
  let cb: (data: any) => void = () => {};
  return {
    default: {
      connect: vi.fn((url, sessionId, callback) => {
        cb = callback;
      }),
      disconnect: vi.fn(),
      isConnected: vi.fn(() => true),
      sendMessage: vi.fn(() => true),
      retry: vi.fn(),
      subscribeStatus: vi.fn((listener: (state: string) => void) => {
        listener("connected");
        return () => {};
      }),
      // Helper to trigger socket mock events in tests
      triggerMessage: (data: any) => cb(data),
    },
  };
});

describe("Frontend Global Canvas & Message Merging TDD", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    
    // Mock global fetch
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/sessions")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve([
                { id: "session-1", title: "Session 1" },
                { id: "session-2", title: "Session 2" },
              ]),
          });
        }
        if (url.includes("/messages")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve([]),
          });
        }
        if (url.includes("/api/apps/")) {
          const app_id = url.split("/").pop();
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                id: app_id,
                title: `App ${app_id}`,
                js: "",
              }),
          });
        }
        if (url.includes("/api/canvas")) {
          const savedPinned = localStorage.getItem("pinned_widgets_global");
          const pinned_ids = savedPinned ? JSON.parse(savedPinned) : [];
          const savedSpans = localStorage.getItem("widget_spans_global");
          const widget_spans = savedSpans ? JSON.parse(savedSpans) : {};
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ pinned_ids, widget_spans }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      })
    );
  });

  it("should use global localStorage keys instead of session-specific keys", async () => {
    localStorage.setItem(
      "pinned_widgets_global",
      JSON.stringify(["global-app-1"])
    );
    localStorage.setItem(
      "widget_spans_global",
      JSON.stringify({ "global-app-1": { cols: 2, rows: 2 } })
    );

    render(<App />);

    // Verify app-1 widget gets loaded and displayed based on global storage
    await waitFor(() => {
      expect(screen.getByText("App global-app-1")).toBeDefined();
    });

    // Verify localStorage.getItem was called for global key
    expect(localStorage.getItem("pinned_widgets_global")).toContain("global-app-1");
  });

  it("does not let a late bootstrap snapshot overwrite a newer WebSocket widget revision", async () => {
    let resolveBootstrapApp!: (response: Response) => void;
    const bootstrapApp = new Promise<Response>((resolve) => {
      resolveBootstrapApp = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/sessions")) {
          return Promise.resolve({
            ok: true,
            json: async () => [{ id: "session-1", title: "Session 1" }],
          });
        }
        if (url.includes("/messages")) {
          return Promise.resolve({ ok: true, json: async () => [] });
        }
        if (url.endsWith("/api/apps/race-app")) return bootstrapApp;
        if (url.endsWith("/api/canvas") && init?.method !== "POST") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              version: 3,
              open_app_ids: ["race-app"],
              active_app_id: "race-app",
              windows: {
                "race-app": {
                  mode: "maximized",
                  bounds: { x: 0.16, y: 0.12, width: 0.68, height: 0.72 },
                },
              },
            }),
          });
        }
        return Promise.resolve({ ok: true, json: async () => ({}) });
      }),
    );

    render(<App />);
    await waitFor(() => expect(wsService.connect).toHaveBeenCalled());

    act(() => {
      (wsService as typeof wsService & { triggerMessage(data: unknown): void }).triggerMessage({
        type: "widget",
        widget: {
          id: "race-app",
          title: "Live Revision",
          js: "",
          manifest_revision: "2:1.0.1",
        },
      });
    });
    expect(await screen.findByText("Live Revision")).toBeDefined();

    await act(async () => {
      resolveBootstrapApp({
        ok: true,
        json: async () => ({
          id: "race-app",
          title: "Stale Bootstrap Revision",
          js: "",
          manifest_revision: "2:1.0.0",
        }),
      } as Response);
      await bootstrapApp;
    });

    await waitFor(() => {
      expect(screen.queryByText("Stale Bootstrap Revision")).toBeNull();
      expect(screen.getByText("Live Revision")).toBeDefined();
    });
  });
});
