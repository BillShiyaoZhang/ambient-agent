import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  chatConnect: vi.fn(),
  chatDisconnect: vi.fn(),
  runListeners: new Set<(event: Record<string, unknown>) => void>(),
  liveListeners: new Set<(event: Record<string, unknown>) => void>(),
  liveResetListeners: new Set<() => void>(),
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

vi.mock("../../frontend/src/services/runLive", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../frontend/src/services/runLive")>();
  return {
    ...actual,
    runLiveService: {
      subscribe: vi.fn((
        _sessionId: string,
        listener: (event: Record<string, unknown>) => void,
        onReset: () => void,
      ) => {
        harness.liveListeners.add(listener);
        harness.liveResetListeners.add(onReset);
        return () => {
          harness.liveListeners.delete(listener);
          harness.liveResetListeners.delete(onReset);
        };
      }),
    },
  };
});

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

function canonicalProgress(sessionId: string, sequence: number, type: string, payload: unknown): Record<string, unknown> {
  return {
    sequence,
    event_id: `event-progress-${sequence}`,
    schema_version: 1,
    stream_epoch: "epoch-one",
    run_id: "run-progress",
    session_id: sessionId,
    step_id: "stage_code",
    attempt: 1,
    trace_id: "trace-progress",
    type,
    payload,
    created_at: `2026-07-19T00:00:0${sequence}Z`,
  };
}

describe("canonical RunEvent chat projection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    harness.runListeners.clear();
    harness.liveListeners.clear();
    harness.liveResetListeners.clear();
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

  it("groups ephemeral streaming replies into a Run card", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      harness.runListeners.forEach((listener) => {
        listener(canonicalProgress("session-one", 1, "step_started", {
          step_key: "stage_code",
          attempt: 1,
          lease_epoch: 1,
        }));
        listener(canonicalProgress("session-one", 2, "agent_update", {
          type: "reply",
          message: { id: -1, sender: "agent", content: "正在准备组件文件" },
        }));
        listener(canonicalProgress("session-one", 3, "agent_update", {
          type: "reply",
          message: { id: -1, sender: "agent", content: "正在写入组件文件" },
        }));
      });
    });

    expect(await screen.findByText("生成中 · 生成应用")).toBeDefined();
    expect(screen.getAllByText("正在写入组件文件")).toHaveLength(1);
    expect(screen.getByText("2 条进度已归并")).toBeDefined();
  });

  it("shows batched live deltas before commit and removes them at the durable boundary", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      harness.liveListeners.forEach((listener) => {
        listener({
          schema_version: 1,
          run_id: "run-progress",
          session_id: "session-one",
          step_id: "stage_code",
          attempt: 1,
          stream_id: "run-progress:stage_code:1:activity",
          chunk_sequence: 1,
          kind: "activity_delta",
          delta: "正在实时生成",
          replace: true,
          created_at: "2026-07-19T00:00:01Z",
        });
      });
    });

    expect(await screen.findByText("正在实时生成")).toBeDefined();

    act(() => {
      harness.runListeners.forEach((listener) => {
        listener(canonicalProgress("session-one", 2, "step_committed", {
          step_key: "stage_code",
          attempt: 1,
          outcome: { kind: "continue", summary: "代码阶段已完成" },
        }));
      });
    });

    await waitFor(() => expect(screen.queryByText("正在实时生成")).toBeNull());
  });

  it("clears uncommitted live text when the connection is reset", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      harness.liveListeners.forEach((listener) => listener({
        schema_version: 1,
        run_id: "run-progress",
        session_id: "session-one",
        step_id: "stage_code",
        attempt: 1,
        stream_id: "run-progress:stage_code:1:activity",
        chunk_sequence: 1,
        kind: "activity_delta",
        delta: "断线前文本",
        replace: true,
        created_at: "2026-07-19T00:00:01Z",
      }));
    });
    expect(await screen.findByText("断线前文本")).toBeDefined();

    act(() => {
      harness.liveResetListeners.forEach((listener) => listener());
    });
    await waitFor(() => expect(screen.queryByText("断线前文本")).toBeNull());
  });
});
