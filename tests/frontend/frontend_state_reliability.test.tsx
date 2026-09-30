import React, { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  AgentChatOverlay: ({ messages, activeSessionId, onSelectSession, runCards = [], onCancelRun }: {
    messages: Array<{ content: string }>;
    activeSessionId: string | null;
    onSelectSession: (id: string) => void;
    runCards: Array<{ id: string }>;
    onCancelRun: (id: string) => void;
  }) => <section>
    <output data-testid="session">{activeSessionId}</output>
    <output data-testid="messages">{messages.map((message) => message.content).join("|")}</output>
    <output data-testid="run-ids">{runCards.map((run) => run.id).join("|")}</output>
    {runCards[0] && <button onClick={() => onCancelRun(runCards[0].id)}>Cancel chat run</button>}
    <button onClick={() => onSelectSession("session-b")}>Select session B</button>
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
