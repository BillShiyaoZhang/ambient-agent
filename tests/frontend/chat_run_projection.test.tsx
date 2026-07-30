import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  chatConnect: vi.fn(),
  chatDisconnect: vi.fn(),
  chatRetry: vi.fn(),
  runConnectionRetry: vi.fn(),
  liveConnectionRetry: vi.fn(),
  chatSend: vi.fn(() => true),
  connectionListeners: new Set<(state: string) => void>(),
  runListeners: new Set<(event: Record<string, unknown>) => void>(),
  liveListeners: new Set<(event: Record<string, unknown>) => void>(),
  liveResetListeners: new Set<() => void>(),
}));

vi.mock("../../frontend/src/services/websocket", () => ({
  default: {
    connect: harness.chatConnect,
    disconnect: harness.chatDisconnect,
    isConnected: vi.fn(() => true),
    sendMessage: harness.chatSend,
    retry: harness.chatRetry,
    subscribeStatus: vi.fn((listener: (state: string) => void) => {
      harness.connectionListeners.add(listener);
      listener("connected");
      return () => harness.connectionListeners.delete(listener);
    }),
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
    retryConnection: harness.runConnectionRetry,
  },
}));

vi.mock("../../frontend/src/services/runLive", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../frontend/src/services/runLive")>();
  return {
    ...actual,
    runLiveService: {
      retryConnection: harness.liveConnectionRetry,
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
    harness.connectionListeners.clear();
    harness.chatSend.mockReset();
    harness.chatSend.mockReturnValue(true);
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

  it("surfaces a failed durable run stream and retries both Ambient connections", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      window.dispatchEvent(new CustomEvent("ambient_run_stream_status", {
        detail: { state: "unavailable" },
      }));
    });

    expect(screen.getByText("连接不可用")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "重试连接" }));
    expect(harness.chatRetry).toHaveBeenCalledOnce();
    expect(harness.runConnectionRetry).toHaveBeenCalledOnce();
    expect(harness.liveConnectionRetry).toHaveBeenCalledOnce();
  });

  it("keeps the active conversation and explains a rejected deletion", async () => {
    vi.mocked(global.fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/sessions/session-one") && init?.method === "DELETE") {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({
            detail: {
              code: "session_has_active_runs",
              message: "Cancel or resolve active tasks before deleting this conversation",
            },
          }),
        } as Response);
      }
      if (url.endsWith("/api/sessions")) {
        return response([{ id: "session-one", title: "Session One", language: "zh" }]);
      }
      if (url.includes("/messages")) return response([]);
      if (url.endsWith("/api/canvas")) {
        return response({ version: 3, open_app_ids: [], active_app_id: null, windows: {} });
      }
      if (url.endsWith("/api/llm/catalog") || url.endsWith("/api/llm/providers")) return response([]);
      if (url.endsWith("/api/llm/settings")) {
        return response({ default_model: null, fast_model: null });
      }
      return response({});
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));
    fireEvent.click(screen.getByRole("button", { name: "聊天历史" }));
    fireEvent.click(screen.getByRole("button", { name: "删除 Session One" }));

    expect((await screen.findByRole("alert")).textContent).toContain("请先取消或处理仍在运行的任务");
    await waitFor(() => expect(harness.chatConnect).toHaveBeenLastCalledWith(
      expect.stringContaining("/ws/chat"),
      "session-one",
      expect.any(Function),
    ));
    expect(screen.getAllByText("Session One").length).toBeGreaterThan(0);
  });

  it("localizes the destructive conversation confirmation", async () => {
    const confirmDelete = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<App />);

    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));
    fireEvent.click(screen.getByRole("button", { name: "聊天历史" }));
    fireEvent.click(screen.getByRole("button", { name: "删除 Session One" }));

    expect(confirmDelete).toHaveBeenCalledWith("确定要删除这个对话吗？");
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

  it("projects structured progress into a Run card without a chat message", async () => {
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
        listener(canonicalProgress("session-one", 2, "activity_updated", {
          type: "activity_updated",
          activity_id: "code:generation",
          activity_type: "code",
          status: "running",
          summary: "Generating staged App",
          detail: "正在准备组件文件",
          metadata: {},
        }));
        listener(canonicalProgress("session-one", 3, "activity_updated", {
          type: "activity_updated",
          activity_id: "code:generation",
          activity_type: "code",
          status: "running",
          summary: "Generating staged App",
          detail: "正在写入组件文件",
          metadata: {},
        }));
      });
    });

    expect(await screen.findByText("生成中 · 生成应用")).toBeDefined();
    expect(screen.getAllByText("正在写入组件文件")).toHaveLength(1);
    expect(screen.queryByText("正在准备组件文件")).toBeNull();
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

  it("keeps ordinary plan approval inline and sends the selected action", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      harness.runListeners.forEach((listener) => listener(
        canonicalProgress("session-one", 1, "plan_approval_request", {
          type: "plan_approval_request",
          request_id: "interaction-plan",
          app_id: "weather-app",
          plan: "生成天气概览与逐小时预报。",
        }),
      ));
    });

    expect(await screen.findByText("确认开发计划")).toBeDefined();
    expect(screen.queryByText("App 开发计划确认")).toBeNull();
    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "批准计划" }));
    expect((await screen.findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByText("确认开发计划")).toBeDefined();

    harness.chatSend.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "批准计划" }));
    expect(harness.chatSend).toHaveBeenCalledWith({
      type: "plan_approval_response",
      request_id: "interaction-plan",
      approved: true,
      plan: "生成天气概览与逐小时预报。",
      feedback: "",
    });

    fireEvent.click(screen.getByRole("button", { name: "查看 / 调整" }));
    const planDialog = await screen.findByRole("dialog", { name: "App 开发计划确认" });
    const approvePlan = within(planDialog).getByRole("button", {
      name: "确认计划并开始开发 (Approve)",
    });
    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(approvePlan);
    expect((await within(planDialog).findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByRole("dialog", { name: "App 开发计划确认" })).toBeDefined();

    harness.chatSend.mockReturnValueOnce(true);
    fireEvent.click(approvePlan);
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "App 开发计划确认" })).toBeNull();
    });
  });

  it("keeps verification repair choices pending until their command is delivered", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      harness.runListeners.forEach((listener) => listener(
        canonicalProgress("session-one", 1, "verification_approval_request", {
          type: "verification_approval_request",
          request_id: "interaction-verification",
          app_id: "weather-app",
          report: "The staged app needs one repair.",
          allowed_actions: ["rework_code"],
          options: [],
        }),
      ));
    });

    expect(await screen.findByText("选择验证修复方式")).toBeDefined();
    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "修复代码" }));
    expect((await screen.findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByText("选择验证修复方式")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    const verificationDialog = await screen.findByRole("dialog", { name: "Schema 校验未完全对齐" });
    const repair = within(verificationDialog).getByRole("button", { name: "智能修复代码 (Auto-Fix)" });
    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(repair);
    expect((await within(verificationDialog).findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByRole("dialog", { name: "Schema 校验未完全对齐" })).toBeDefined();

    harness.chatSend.mockReturnValueOnce(true);
    fireEvent.click(repair);
    expect(harness.chatSend).toHaveBeenLastCalledWith({
      type: "verification_approval_response",
      request_id: "interaction-verification",
      approved: "rework_code",
      feedback: "",
      approved_options: [],
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Schema 校验未完全对齐" })).toBeNull();
    });
  });

  it("keeps server schema diagnostics visible but stops them blocking after the proposal is edited", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "打开聊天" }));

    act(() => {
      harness.runListeners.forEach((listener) => listener(
        canonicalProgress("session-one", 1, "schema_approval_request", {
          type: "schema_approval_request",
          request_id: "interaction-schema",
          app_id: "release-app",
          proposal: {
            reused_schemas: [],
            new_schemas: [{
              id: "Release",
              name: "Release",
              description: "A software release",
              properties: { title: "string" },
              subclass_of: "Thing",
              ontology_iri: "urn:ambient:ontology:Release",
              equivalent_to: [],
              data_scope: "user_context",
            }],
            capabilities: [],
          },
          validation_errors: ["Server validation rejected the previous Release description"],
        }),
      ));
    });

    expect(await screen.findByText("确认数据与能力方案")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "查看 / 编辑" }));

    const dialog = await screen.findByRole("dialog", { name: "Schema 与能力授权对齐" });
    expect(await within(dialog).findByText("Schema 与能力提案")).toBeDefined();
    expect(within(dialog).getAllByText("Release").length).toBeGreaterThan(0);
    const approve = within(dialog).getByRole("button", { name: "确认对齐并编码 (Approve)" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(within(dialog).getByText("Server validation rejected the previous Release description")).toBeDefined();

    fireEvent.change(within(dialog).getByPlaceholderText("实体说明"), {
      target: { value: "An edited software release" },
    });

    expect((approve as HTMLButtonElement).disabled).toBe(false);
    expect(within(dialog).getByText("上次提交的服务端诊断（已过期）")).toBeDefined();
    expect(within(dialog).getByText("Server validation rejected the previous Release description")).toBeDefined();

    const entityId = within(dialog).getByPlaceholderText("实体 ID（例如 Habit）");
    fireEvent.change(entityId, { target: { value: "" } });

    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(within(dialog).getAllByText("Every schema entity must have a non-empty ID.").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("上次提交的服务端诊断（已过期）")).toBeDefined();

    fireEvent.change(entityId, { target: { value: "Release" } });
    expect((approve as HTMLButtonElement).disabled).toBe(false);

    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(approve);
    expect((await within(dialog).findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByRole("dialog", { name: "Schema 与能力授权对齐" })).toBeDefined();

    harness.chatSend.mockReturnValueOnce(true);
    fireEvent.click(approve);
    expect(harness.chatSend).toHaveBeenCalledWith({
      type: "schema_approval_response",
      request_id: "interaction-schema",
      approved: true,
      proposal: expect.objectContaining({
        new_schemas: [expect.objectContaining({
          id: "Release",
          description: "An edited software release",
        })],
      }),
      feedback: "",
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Schema 与能力授权对齐" })).toBeNull();
    });
  });

  it("keeps a sensitive permission pending when its response cannot be delivered", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());

    act(() => {
      harness.runListeners.forEach((listener) => listener(
        canonicalProgress("session-one", 1, "permission_request", {
          type: "permission_request",
          request_id: "permission-one",
          tool_call: "terminal",
          details: "npm publish",
        }),
      ));
    });

    expect(await screen.findByText("OpenCode 授权请求")).toBeDefined();
    expect(screen.getByText("npm publish")).toBeDefined();
    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "允许 (Allow)" }));
    expect((await screen.findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByText("OpenCode 授权请求")).toBeDefined();

    harness.chatSend.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "允许 (Allow)" }));
    await waitFor(() => expect(screen.queryByText("OpenCode 授权请求")).toBeNull());
  });

  it("keeps a backend permission pending when its response cannot be delivered", async () => {
    render(<App />);
    await waitFor(() => expect(harness.chatConnect).toHaveBeenCalled());
    const handleProjection = harness.chatConnect.mock.calls.at(-1)?.[2] as
      | ((data: Record<string, unknown>) => void)
      | undefined;
    expect(handleProjection).toBeDefined();

    act(() => {
      handleProjection?.({
        type: "backend_permission_request",
        request_id: "backend-permission-one",
        app_id: "weather-app",
        permission_type: "external_agent",
        value: { agent_url: "https://agent.example.test" },
      });
    });
    expect(await screen.findByText("后端服务授权请求 (weather-app)")).toBeDefined();

    harness.chatSend.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "允许 (Allow)" }));
    expect((await screen.findByRole("alert")).textContent).toContain("响应尚未发送");
    expect(screen.getByText("后端服务授权请求 (weather-app)")).toBeDefined();

    harness.chatSend.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "允许 (Allow)" }));
    await waitFor(() => {
      expect(screen.queryByText("后端服务授权请求 (weather-app)")).toBeNull();
    });
  });
});
