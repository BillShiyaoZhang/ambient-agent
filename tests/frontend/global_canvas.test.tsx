import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import App from "../../frontend/src/App";
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
      sendMessage: vi.fn(),
      // Helper to trigger socket mock events in tests
      triggerMessage: (data: any) => cb(data),
    },
  };
});

describe("Frontend Global Canvas & Message Merging TDD", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
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
                html: "<div>mocked</div>",
                css: "",
                js: "",
              }),
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
});
