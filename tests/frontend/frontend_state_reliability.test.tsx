import React, { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentModelConfig, CodingAgentDefinition } from "../../frontend/src/services/codingAgents";

const harness = vi.hoisted(() => ({
  connect: vi.fn(),
  list: vi.fn(async () => []),
  get: vi.fn(),
  cancel: vi.fn(),
  listeners: new Set<(event: Record<string, unknown>) => void>(),
}));

vi.mock("../../frontend/src/services/websocket", () => ({
  default: {
    connect: harness.connect,
    disconnect: vi.fn(),
    sendMessage: vi.fn(() => true),
    retry: vi.fn(),
    subscribeStatus: (listener: (state: string) => void) => {
      listener("connected");
      return () => {};
    },
    registerPersistentMessage: vi.fn(),
    unregisterPersistentMessage: vi.fn(),
  },
}));

vi.mock("../../frontend/src/services/runs", () => ({
  runService: {
    list: harness.list,
    get: harness.get,
    subscribe: (listener: (event: Record<string, unknown>) => void) => {
      harness.listeners.add(listener);
      return () => harness.listeners.delete(listener);
    },
    runtimes: vi.fn(async () => []),
    retryConnection: vi.fn(),
    cancel: harness.cancel, retry: vi.fn(), resolve: vi.fn(), reconcile: vi.fn(), stopRuntime: vi.fn(),
  },
}));

vi.mock("../../frontend/src/services/runLive", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../frontend/src/services/runLive")>();
  return { ...original, runLiveService: { subscribe: () => () => {}, retryConnection: vi.fn() } };
});

vi.mock("../../frontend/src/components/AppCenter", () => ({ AppCenter: () => null }));
vi.mock("../../frontend/src/components/AppWorkspace", () => ({ AppWorkspace: () => null }));
vi.mock("../../frontend/src/components/AgentChatOverlay", () => ({
  AgentChatOverlay: ({ messages, activeSessionId, onSelectSession, runCards = [], onCancelRun, codingAgent, codingAgentModel, onManageModels }: {
    messages: Array<{ content: string }>;
    activeSessionId: string | null;
    onSelectSession: (id: string) => void;
    runCards: Array<{ id: string }>;
    onCancelRun: (id: string) => void;
    codingAgent?: CodingAgentDefinition;
    codingAgentModel?: AgentModelConfig;
    onManageModels: () => void;
  }) => <section>
    <output data-testid="session">{activeSessionId}</output>
    <output data-testid="messages">{messages.map((message) => message.content).join("|")}</output>
    <output data-testid="run-ids">{runCards.map((run) => run.id).join("|")}</output>
    <output data-testid="coding-agent-bindings">{codingAgentModel?.native_model}|{codingAgent?.model_config.native_model}</output>
    {runCards[0] && <button onClick={() => onCancelRun(runCards[0].id)}>Cancel chat run</button>}
    <button onClick={() => onSelectSession("session-b")}>Select session B</button>
    <button onClick={onManageModels}>模型与 Provider</button>
  </section>,
}));

import App from "../../frontend/src/App";
import { TaskDrawer } from "../../frontend/src/components/TaskDrawer";

function response(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const message = (id: number, content: string) => ({ id, role: "agent", sender: "agent", content, timestamp: "2026-09-30T00:00:00Z" });
const run = (id: string, title: string) => ({
  id, owner_id: "audit", action_id: "read", action_title: title, source_type: "app", source_id: "audit",
  adapter_type: "mcp_tool", runtime_id: "audit", status: "waiting_user", progress: 0,
  summary: title, input: {}, attempt: 1, created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z", interactions: [],
});

const managedCodex: CodingAgentDefinition = {
  id: "codex", name: "Codex", description: "Managed coding agent", auth_hint: "Independent login", auth_mode: "codex_native", auth_methods: ["device_code"], uses_run_model: false,
  available: true, installed: true, installable: true, install_state: "installed", install_operation: null, command_env: "CODEX_COMMAND", execution_target: "container",
  authenticated: true, auth_state: "signed_in", version: "codex-cli 0.159.3", status_detail: "Logged in",
  model_capability: { modes: ["native"], default_mode: "native", selection: "optional", catalog_source: "agent", supports_inherit: false },
  model_config: { mode: "native", native_model: "gpt-fast" },
};

function codingConfiguration(binding: AgentModelConfig) {
  return { agents: [{ ...managedCodex, model_config: binding }], settings: { default_agent: "codex", agent_models: { codex: binding } } };
}

function mockCodingModelApi() {
  let binding = managedCodex.model_config;
  const read = vi.fn(async () => response(codingConfiguration(binding)));
  const save = vi.fn(async (config: AgentModelConfig) => { binding = config; return response(binding); });
  const defaultFetch = vi.mocked(fetch).getMockImplementation()!;
  const models = ["gpt-default", "gpt-fast", "gpt-5.6-luna", "fixture-4", "fixture-5", "fixture-6", "fixture-7", "fixture-8"].map((id, index) => ({
    id, model: id, display_name: id === "gpt-5.6-luna" ? "GPT Luna" : id, description: "", is_default: index === 0, default_reasoning_effort: "medium", supported_reasoning_efforts: ["medium"],
  }));
  vi.mocked(fetch).mockImplementation((input, init) => {
    const url = String(input);
    if (url.endsWith("/api/coding-agents")) return read();
    if (url.endsWith("/api/coding-agents/codex/models")) return Promise.resolve(response({ agent_id: "codex", default_model: "gpt-default", models }));
    if (url.endsWith("/api/coding-agents/codex/model") && init?.method === "PATCH") return save(JSON.parse(String(init.body)));
    return defaultFetch(input, init);
  });
  return { read, save, snapshot: () => codingConfiguration(binding), setBinding: (config: AgentModelConfig) => { binding = config; } };
}

async function openCodingModels() {
  render(<App />);
  await waitFor(() => expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-fast|gpt-fast"));
  fireEvent.click(screen.getByRole("button", { name: "模型与 Provider" }));
  await screen.findByRole("option", { name: "GPT Luna" });
  const picker = screen.getByRole("combobox", { name: "Codex 模型" }) as HTMLSelectElement;
  await waitFor(() => expect(picker.disabled).toBe(false));
  return picker;
}

describe("frontend async state regression contracts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    harness.listeners.clear();
    harness.list.mockResolvedValue([]);
    harness.get.mockReset();
    harness.cancel.mockReset();
    localStorage.clear();
    sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/sessions")) return Promise.resolve(response([
        { id: "session-a", title: "A", language: "zh" }, { id: "session-b", title: "B", language: "zh" },
      ]));
      if (url.endsWith("/messages")) return Promise.resolve(response([]));
      if (url.endsWith("/api/canvas")) return Promise.resolve(response({ version: 3, open_app_ids: [], active_app_id: null, windows: {} }));
      if (url.endsWith("/api/llm/catalog") || url.endsWith("/api/llm/providers")) return Promise.resolve(response([]));
      if (url.endsWith("/api/llm/settings")) return Promise.resolve(response({ default_model: null, fast_model: null }));
      return Promise.resolve(response({}));
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  describe("shared Codex connection refresh", () => {
    const provider = { id: "ambient-codex", name: "Managed Codex", preset: "codex_native", enabled: true, connection: {}, models: [{ id: "gpt-inference", display_name: "GPT Inference" }] };

    it("shows a retry error when sync succeeds but its configuration refresh fails", async () => {
      const api = mockCodingModelApi();
      await openCodingModels();
      const defaultFetch = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation((input, init) => String(input).endsWith("/api/llm/connections/codex/sync") ? Promise.resolve(response(provider)) : defaultFetch(input, init));
      const error = vi.spyOn(console, "error").mockImplementation(() => {});
      try {
        api.read.mockRejectedValueOnce(new Error("Configuration unavailable"));
        fireEvent.click(screen.getByRole("button", { name: "同步主/快速模型" }));
        await screen.findByText(/Configuration unavailable.*同步主\/快速模型.*重试/);
        expect(screen.queryByText("Codex 模型已同步，可分别选择主模型和快速模型。")).toBeNull();
        expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-fast|gpt-fast");
        expect(screen.getByRole("button", { name: "同步主/快速模型" }).hasAttribute("disabled")).toBe(false);
      } finally {
        error.mockRestore();
      }
    });

    it("does not apply a sync refresh after sign-out begins while the configuration request is pending", async () => {
      const api = mockCodingModelApi();
      await openCodingModels();
      const pendingConfiguration = deferred<Response>();
      const logout = deferred<Response>();
      api.read.mockReturnValueOnce(pendingConfiguration.promise);
      const defaultFetch = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation((input, init) => {
        const url = String(input);
        if (url.endsWith("/api/llm/connections/codex/sync")) return Promise.resolve(response(provider));
        if (url.endsWith("/api/coding-agents/codex/auth") && init?.method === "DELETE") return logout.promise;
        return defaultFetch(input, init);
      });
      fireEvent.click(screen.getByRole("button", { name: "同步主/快速模型" }));
      await waitFor(() => expect(api.read).toHaveBeenCalledTimes(3));
      fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
      await act(async () => pendingConfiguration.resolve(response(codingConfiguration({ mode: "native", native_model: "gpt-default" }))));
      expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-fast|gpt-fast");
      expect(screen.queryByText("Codex 模型已同步，可分别选择主模型和快速模型。")).toBeNull();
      await act(async () => logout.resolve(response({})));
    });
  });

  describe("coding model persistence", () => {
    it("applies a saved native model before a slow refresh and keeps it when settings reopen", async () => {
      const api = mockCodingModelApi();
      const picker = await openCodingModels();
      expect(picker.options).toHaveLength(9);
      const refresh = deferred<Response>();
      api.read.mockReturnValueOnce(refresh.promise);
      fireEvent.change(picker, { target: { value: "gpt-5.6-luna" } });
      await waitFor(() => expect(api.read).toHaveBeenCalledTimes(3));
      expect(api.save).toHaveBeenCalledWith({ mode: "native", native_model: "gpt-5.6-luna" });
      expect(picker.value).toBe("gpt-5.6-luna");
      expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-5.6-luna|gpt-5.6-luna");
      await act(async () => refresh.resolve(response(api.snapshot())));
      fireEvent.click(screen.getByRole("button", { name: "关闭" }));
      fireEvent.click(screen.getByRole("button", { name: "模型与 Provider" }));
      await waitFor(() => expect((screen.getByRole("combobox", { name: "Codex 模型" }) as HTMLSelectElement).value).toBe("gpt-5.6-luna"));
    });

    it("invalidates reads begun before and during a model save so late snapshots cannot undo it", async () => {
      const api = mockCodingModelApi();
      const picker = await openCodingModels();
      const beforeSave = deferred<Response>();
      const duringSave = deferred<Response>();
      const save = deferred<AgentModelConfig>();
      api.read.mockReturnValueOnce(beforeSave.promise);
      fireEvent.click(screen.getByRole("button", { name: "刷新配置" }));
      api.save.mockImplementationOnce(async () => { const binding = await save.promise; api.setBinding(binding); return response(binding); });
      fireEvent.change(picker, { target: { value: "gpt-5.6-luna" } });
      await act(async () => beforeSave.resolve(response(codingConfiguration({ mode: "native", native_model: "gpt-default" }))));
      expect(picker.value).toBe("gpt-fast");
      api.read.mockReturnValueOnce(duringSave.promise);
      fireEvent.click(screen.getByRole("button", { name: "刷新配置" }));
      await act(async () => save.resolve({ mode: "native", native_model: "gpt-5.6-luna" }));
      await waitFor(() => expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-5.6-luna|gpt-5.6-luna"));
      await act(async () => duringSave.resolve(response(codingConfiguration({ mode: "native", native_model: "gpt-default" }))));
      expect(picker.value).toBe("gpt-5.6-luna");
      expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-5.6-luna|gpt-5.6-luna");
    });

    it("keeps a successful save visible when its background configuration refresh fails", async () => {
      const api = mockCodingModelApi();
      const picker = await openCodingModels();
      const error = vi.spyOn(console, "error").mockImplementation(() => {});
      try {
        api.read.mockRejectedValueOnce(new Error("Configuration unavailable"));
        fireEvent.change(picker, { target: { value: "gpt-5.6-luna" } });
        await screen.findByText("Codex 模型配置已更新");
        expect(picker.value).toBe("gpt-5.6-luna");
        expect(screen.getByTestId("coding-agent-bindings").textContent).toBe("gpt-5.6-luna|gpt-5.6-luna");
        expect(error).toHaveBeenCalledWith("Error loading LLM configuration:", expect.any(Error));
      } finally {
        error.mockRestore();
      }
    });
  });

  it("does not let session A's late history overwrite selected session B", async () => {
    const historyA = deferred<Response>();
    const defaultFetch = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (String(input).includes("/sessions/session-a/messages")) return historyA.promise;
      if (String(input).includes("/sessions/session-b/messages")) return Promise.resolve(response([message(2, "History B")]));
      return defaultFetch(input, init);
    });
    render(<App />);
    await waitFor(() => expect(harness.connect).toHaveBeenCalled());
    fireEvent.click(screen.getByText("Select session B"));
    await waitFor(() => expect(screen.getByTestId("messages").textContent).toBe("History B"));
    await act(async () => { historyA.resolve(response([message(1, "History A")])); await historyA.promise; });
    expect(screen.getByTestId("session").textContent).toBe("session-b");
    expect(screen.getByTestId("messages").textContent).toBe("History B");
  });

  it("preserves a live reply when an older history snapshot arrives", async () => {
    const history = deferred<Response>();
    const defaultFetch = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input).includes("/messages") ? history.promise : defaultFetch(input, init));
    render(<App />);
    await waitFor(() => expect(harness.connect).toHaveBeenCalled());
    const callback = harness.connect.mock.calls.at(-1)![2] as (data: unknown) => void;
    act(() => callback({ type: "reply", message: message(5, "Live reply") }));
    expect(screen.getByTestId("messages").textContent).toBe("Live reply");
    await act(async () => { history.resolve(response([message(1, "Old history")])); await history.promise; });
    expect(screen.getByTestId("messages").textContent).toContain("Live reply");
  });

  it("does not show a previously selected Run after its slower detail response", async () => {
    const runA = run("run-a", "Action A");
    const runB = run("run-b", "Action B");
    const detailsA = deferred<typeof runA>();
    harness.list.mockResolvedValue([runA, runB] as never);
    harness.get.mockImplementation((id: string) => id === "run-a" ? detailsA.promise : Promise.resolve(runB));
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Action A"));
    fireEvent.click(screen.getByText("Action B"));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Action B" })).toBeDefined());
    await act(async () => { detailsA.resolve(runA); await detailsA.promise; });
    expect(screen.queryByRole("heading", { name: "Action A" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Action B" })).toBeDefined();
  });

  it("keeps system-theme changes working after StrictMode effect replay", async () => {
    const handlers = new Set<(event: { matches: boolean }) => void>();
    const darkMedia = {
      matches: false,
      addEventListener: (_name: string, callback: (event: { matches: boolean }) => void) => handlers.add(callback),
      removeEventListener: (_name: string, callback: (event: { matches: boolean }) => void) => handlers.delete(callback),
    };
    vi.stubGlobal("matchMedia", vi.fn((query: string) => query.includes("prefers-color-scheme") ? darkMedia : { matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    render(<StrictMode><App /></StrictMode>);
    await waitFor(() => expect(harness.connect).toHaveBeenCalled());
    expect(document.documentElement.dataset.theme).toBe("light");
    act(() => { darkMedia.matches = true; handlers.forEach((handler) => handler({ matches: true })); });
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("keeps the live version of a message ID when history arrives and deduplicates it", async () => {
    const history = deferred<Response>();
    const defaultFetch = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input).includes("/messages") ? history.promise : defaultFetch(input, init));
    render(<App />);
    await waitFor(() => expect(harness.connect).toHaveBeenCalled());
    const callback = harness.connect.mock.calls.at(-1)![2] as (data: unknown) => void;
    act(() => callback({ type: "reply", message: message(5, "New live version") }));
    await act(async () => { history.resolve(response([message(5, "Old snapshot version")])); await history.promise; });
    expect(screen.getByTestId("messages").textContent).toBe("New live version");
  });

  it("keeps a post-action detail refresh scoped to the task selected at click time", async () => {
    const runA = run("run-a", "Action A");
    const runB = run("run-b", "Action B");
    const cancellation = deferred<typeof runA>();
    harness.list.mockResolvedValue([runA, runB] as never);
    harness.get.mockImplementation((id: string) => Promise.resolve(id === "run-a" ? runA : runB));
    harness.cancel.mockReturnValue(cancellation.promise);
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Action A"));
    await screen.findByRole("heading", { name: "Action A" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to task list" }));
    fireEvent.click(screen.getByText("Action B"));
    await screen.findByRole("heading", { name: "Action B" });
    await act(async () => { cancellation.resolve(runA); await cancellation.promise; });
    expect(screen.queryByRole("heading", { name: "Action A" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Action B" })).toBeDefined();
  });

  it("disables duplicate task actions while a submission is pending", async () => {
    const runA = run("run-a", "Action A");
    const cancellation = deferred<typeof runA>();
    harness.list.mockResolvedValue([runA] as never);
    harness.get.mockResolvedValue(runA);
    harness.cancel.mockReturnValue(cancellation.promise);
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Action A"));
    const cancel = await screen.findByRole("button", { name: "Cancel" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    expect(harness.cancel).toHaveBeenCalledTimes(1);
    await act(async () => { cancellation.resolve(runA); await cancellation.promise; });
  });

  it("invalidates a pending detail read when the drawer closes", async () => {
    const runA = run("run-a", "Action A");
    const details = deferred<typeof runA>();
    harness.list.mockResolvedValue([runA] as never);
    harness.get.mockReturnValue(details.promise);
    const view = render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Action A"));
    view.rerender(<TaskDrawer open={false} language="en" onClose={() => {}} />);
    await act(async () => { details.resolve(runA); await details.promise; });
    view.rerender(<TaskDrawer open language="en" onClose={() => {}} />);
    expect(screen.queryByRole("heading", { name: "Action A" })).toBeNull();
  });

  it("shows detail-read errors locally so the user can select a task again", async () => {
    const runA = run("run-a", "Action A");
    harness.list.mockResolvedValue([runA] as never);
    harness.get.mockRejectedValue(new Error("Detail temporarily unavailable"));
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Action A"));
    expect(await screen.findByText("Detail temporarily unavailable")).toBeDefined();
    harness.get.mockResolvedValue(runA);
    fireEvent.click(screen.getByText("Action A"));
    expect(await screen.findByRole("heading", { name: "Action A" })).toBeDefined();
  });

  it("ignores a prior action error after leaving and reselecting the same task", async () => {
    const runA = run("run-a", "Action A");
    const runB = run("run-b", "Action B");
    let rejectCancellation!: (error: Error) => void;
    harness.cancel.mockReturnValue(new Promise((_resolve, reject) => { rejectCancellation = reject; }));
    harness.list.mockResolvedValue([runA, runB] as never);
    harness.get.mockImplementation((id: string) => Promise.resolve(id === "run-a" ? runA : runB));
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Action A"));
    await screen.findByRole("heading", { name: "Action A" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to task list" }));
    fireEvent.click(screen.getByText("Action B"));
    await screen.findByRole("heading", { name: "Action B" });
    fireEvent.click(screen.getByRole("button", { name: "Back to task list" }));
    fireEvent.click(screen.getByText("Action A"));
    await screen.findByRole("heading", { name: "Action A" });
    await act(async () => { rejectCancellation(new Error("Old action failure")); await Promise.resolve(); });
    expect(screen.queryByText("Old action failure")).toBeNull();
  });

  it("does not project a cancelled chat Run into a conversation selected later", async () => {
    const runA = { ...run("run-a", "Chat A"), status: "running", source_type: "chat", source_id: "session-a" };
    const cancellation = deferred<typeof runA>();
    harness.list.mockImplementation(async (params?: { source_id?: string }) => (params?.source_id === "session-a" ? [runA] : []) as never);
    harness.cancel.mockReturnValue(cancellation.promise);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("run-ids").textContent).toBe("run-a"));
    fireEvent.click(screen.getByText("Cancel chat run"));
    fireEvent.click(screen.getByText("Select session B"));
    await waitFor(() => expect(screen.getByTestId("session").textContent).toBe("session-b"));
    await act(async () => { cancellation.resolve({ ...runA, status: "cancelled" }); await cancellation.promise; });
    expect(screen.getByTestId("run-ids").textContent).toBe("");
  });
});
