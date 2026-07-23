import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  chatConnect: vi.fn(),
  chatDisconnect: vi.fn(),
  runListeners: new Set<(event: Record<string, unknown>) => void>(),
}));

vi.mock("../../frontend/src/services/websocket", () => ({
  default: {
    connect: harness.chatConnect,
    disconnect: harness.chatDisconnect,
    isConnected: vi.fn(() => true),
    sendMessage: vi.fn(),
    registerPersistentMessage: vi.fn(),
    unregisterPersistentMessage: vi.fn(),
  },
}));

vi.mock("../../frontend/src/services/runs", () => ({
  runService: {
    subscribe: vi.fn((listener: (event: Record<string, unknown>) => void) => {
      harness.runListeners.add(listener);
      return () => harness.runListeners.delete(listener);
    }),
    list: vi.fn(async () => []),
    runtimes: vi.fn(async () => []),
    get: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    resolve: vi.fn(),
    stopRuntime: vi.fn(),
  },
}));

import App from "../../frontend/src/App";

window.HTMLElement.prototype.scrollIntoView = vi.fn();

function response(body: unknown): Promise<Response> {
  return Promise.resolve({
    ok: true,
    json: async () => body,
  } as Response);
}

function canonicalReply(sessionId: string, content: string): Record<string, unknown> {
  return {
    sequence: 1,
    event_id: "event-reply-1",
    schema_version: 1,
    stream_epoch: "epoch-one",
    run_id: "run-one",
    session_id: sessionId,
    step_id: "converse",
    attempt: 1,
    trace_id: "trace-one",
    type: "reply",
    payload: {
      type: "reply",
      message: {
        id: 42,
        sender: "agent",
        role: "agent",
        content,
        timestamp: "2026-07-19T00:00:00Z",
      },
    },
    created_at: "2026-07-19T00:00:00Z",
  };
}

describe("canonical RunEvent chat projection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    harness.runListeners.clear();
    localStorage.clear();
    sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/sessions")) {
        return response([{ id: "session-one", title: "Session One", language: "zh" }]);
      }
      if (url.includes("/messages")) return response([]);
      if (url.endsWith("/api/canvas")) {
        return response({ version: 3, open_app_ids: [], active_app_id: null, windows: {} });
      }
      if (url.endsWith("/api/llm/catalog") || url.endsWith("/api/llm/providers")) {
        return response([]);
      }
      if (url.endsWith("/api/llm/settings")) {
        return response({ default_model: null, fast_model: null });
      }
      return response({});
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses commands_only on /ws/chat and projects only the active session payload", async () => {
    render(<App />);

    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    const [url, sessionId] = harness.chatConnect.mock.calls.at(-1) ?? [];
    expect(url).toBe("ws://localhost:8000/ws/chat?projection=commands_only");
    expect(sessionId).toBe("session-one");

    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));
    act(() => {
      harness.runListeners.forEach((listener) => {
        listener(canonicalReply("another-session", "Must not leak across sessions"));
        listener(canonicalReply("session-one", "Projected exactly once"));
      });
    });

    expect(await screen.findByText("Projected exactly once")).toBeDefined();
    expect(screen.queryByText("Must not leak across sessions")).toBeNull();
  });

  it("creates only one default session under React StrictMode", async () => {
    let createdSession: { id: string; title: string; language: string } | null = null;
    let createCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/sessions") && init?.method === "POST") {
        createCalls += 1;
        const body = JSON.parse(String(init.body));
        createdSession = body;
        return response(body);
      }
      if (url.endsWith("/api/sessions")) return response(createdSession ? [createdSession] : []);
      if (url.includes("/messages")) return response([]);
      if (url.endsWith("/api/canvas")) {
        return response({ version: 3, open_app_ids: [], active_app_id: null, windows: {} });
      }
      if (url.endsWith("/api/llm/catalog") || url.endsWith("/api/llm/providers")) return response([]);
      if (url.endsWith("/api/llm/settings")) return response({ default_model: null, fast_model: null });
      return response({});
    }));

    render(<React.StrictMode><App /></React.StrictMode>);

    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    expect(createCalls).toBe(1);
  });
});
