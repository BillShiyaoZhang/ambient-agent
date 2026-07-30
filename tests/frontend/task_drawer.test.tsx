import React, { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const { list, get, runtimes, resolve, reconcile, retry, subscribe, emitRunEvent } = vi.hoisted(() => {
  let listener: ((event: { run_id: string }) => void) | null = null;
  return {
    list: vi.fn(),
    get: vi.fn(),
    runtimes: vi.fn(),
    resolve: vi.fn(),
    reconcile: vi.fn(),
    retry: vi.fn(),
    subscribe: vi.fn((nextListener: (event: { run_id: string }) => void) => {
      listener = nextListener;
      return () => {
        if (listener === nextListener) listener = null;
      };
    }),
    emitRunEvent: (event: { run_id: string }) => listener?.(event),
  };
});

vi.mock("../../frontend/src/services/runs", () => ({
  runService: {
    list,
    get,
    runtimes,
    resolve,
    reconcile,
    subscribe,
    cancel: vi.fn(),
    retry,
    stopRuntime: vi.fn(),
  },
}));

import { TaskDrawer } from "../../frontend/src/components/TaskDrawer";

const waitingRun = {
  id: "run-1",
  owner_id: "mcp:acme:mail",
  action_id: "send",
  action_title: "Send mail",
  source_type: "app",
  source_id: "mail-ui",
  adapter_type: "mcp_tool",
  runtime_id: "mail-backend",
  status: "waiting_user",
  progress: 0.1,
  summary: "Waiting for permission",
  input: { subject: "Hello" },
  attempt: 1,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  interactions: [{
    id: "interaction-1",
    run_id: "run-1",
    type: "permission",
    prompt: "Allow mail?",
    payload: { scope: "send" },
    status: "pending",
    created_at: new Date().toISOString(),
  }],
};
const recentRunsQuery = { limit: 200, summary_only: true };
const openRunsQuery = {
  status: "queued,running,cancel_requested,waiting_user,needs_attention",
  limit: 500,
  summary_only: true,
};

describe("TaskDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue([waitingRun]);
    get.mockResolvedValue(waitingRun);
    runtimes.mockResolvedValue([{ id: "internal:agent", type: "internal", managed: false, status: "healthy" }]);
    resolve.mockResolvedValue({ ...waitingRun, status: "queued" });
    reconcile.mockResolvedValue({ ...waitingRun, status: "failed" });
  });

  it("surfaces persisted attention items and resolves them", async () => {
    const onCountsChange = vi.fn();
    render(<TaskDrawer open language="en" onClose={() => {}} onCountsChange={onCountsChange} />);
    await waitFor(() => expect(onCountsChange).toHaveBeenCalledWith({ active: 0, attention: 1 }));
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Send mail"));
    const allowButton = await screen.findByText("Allow");
    expect(screen.queryByRole("button", { name: "Execution graph" })).toBeNull();
    fireEvent.click(allowButton);
    await waitFor(() => expect(resolve).toHaveBeenCalledWith("interaction-1", { approved: true }));
  });

  it("projects a selected durable Run onto the shared execution graph", async () => {
    const workflowRun = {
      ...waitingRun,
      adapter_type: "internal_agent",
      workflow_type: "widget_create",
      state: { phase: "wait_schema" },
      checkpoint: { phase: "wait_schema" },
      steps: [
        { step_key: "plan", status: "succeeded", attempt: 1 },
        { step_key: "align_schema", status: "succeeded", attempt: 1 },
        { step_key: "wait_schema", status: "waiting_user", attempt: 1 },
      ],
      events: [
        {
          sequence: 1,
          event_id: "event-1",
          schema_version: 1,
          stream_epoch: "epoch",
          run_id: "run-1",
          session_id: null,
          step_id: "wait_schema",
          attempt: 1,
          trace_id: "run-1",
          duration_ms: null,
          model_usage: null,
          redacted: true,
          type: "run_status_changed",
          payload: { status: "waiting_user" },
          created_at: new Date().toISOString(),
        },
      ],
    };
    get.mockResolvedValue(workflowRun);

    const view = render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Send mail"));
    fireEvent.click(await screen.findByRole("button", { name: "Execution graph" }));

    expect(await screen.findByText("Schema approval")).toBeDefined();
    expect(screen.getByText("Waiting for user")).toBeDefined();
    expect(
      within(screen.getByRole("navigation", { name: "Run detail views" }))
        .getByRole("button", { name: "Overview" }),
    ).toBeDefined();

    view.rerender(<TaskDrawer open language="zh" onClose={() => {}} />);
    expect(screen.getByRole("searchbox", { name: "搜索图谱" })).toBeDefined();
    expect(screen.getByText("路由意图")).toBeDefined();
    expect(screen.getAllByText("Send mail").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "关闭任务中心" })).toBeDefined();
  });

  it("keeps the execution graph reachable for a migrated legacy durable Run", async () => {
    const legacyWorkflowRun = {
      ...waitingRun,
      adapter_type: "internal",
      workflow_type: "legacy",
      workflow_version: 1,
      status: "needs_attention",
      interactions: [],
      events: [{
        sequence: 1,
        event_id: "migration-event",
        schema_version: 1,
        stream_epoch: "epoch",
        run_id: "run-1",
        session_id: null,
        step_id: null,
        attempt: null,
        trace_id: "run-1",
        redacted: true,
        type: "migration_attention_required",
        payload: {
          from: "running",
          to: "needs_attention",
          workflow_version: 1,
          reason: "legacy_state_not_replayable",
        },
        created_at: new Date().toISOString(),
      }],
    };
    list.mockResolvedValue([legacyWorkflowRun]);
    get.mockResolvedValue(legacyWorkflowRun);

    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Send mail"));
    fireEvent.click(await screen.findByRole("button", { name: "Execution graph" }));

    expect(await screen.findByText("Route intent")).toBeDefined();
  });

  it("does not treat an ordinary internal attention Run as a durable workflow", async () => {
    const internalManualRun = {
      ...waitingRun,
      adapter_type: "internal",
      workflow_type: "legacy",
      workflow_version: 1,
      status: "needs_attention",
      interactions: [],
      events: [{
        sequence: 1,
        event_id: "run-event",
        schema_version: 1,
        stream_epoch: "epoch",
        run_id: "run-1",
        session_id: null,
        step_id: null,
        attempt: null,
        trace_id: "run-1",
        redacted: true,
        type: "run_created",
        payload: { status: "queued" },
        created_at: new Date().toISOString(),
      }],
    };
    list.mockResolvedValue([internalManualRun]);
    get.mockResolvedValue(internalManualRun);

    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Send mail"));
    await screen.findByRole("button", { name: "Back to task list" });

    expect(screen.queryByRole("button", { name: "Execution graph" })).toBeNull();
  });

  it("lists backend runtimes in the same drawer", async () => {
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    await waitFor(() => expect(list).toHaveBeenCalledWith(recentRunsQuery));
    expect(list).toHaveBeenCalledWith(openRunsQuery);
    expect(runtimes).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Runtimes"));
    expect(await screen.findByText("internal:agent")).toBeDefined();
  });

  it("explains a terminal non-retryable failure without exposing a Retry action or raw JSON", async () => {
    const failedRun = {
      ...waitingRun,
      status: "failed",
      summary: "Active time exhausted",
      interactions: [],
      error: {
        code: "budget_exhausted",
        message: "Active time exhausted",
        retryable: false,
        effect_state: "none",
      },
    };
    list.mockResolvedValue([failedRun]);
    get.mockResolvedValue(failedRun);
    const onClose = vi.fn();

    render(<TaskDrawer open language="zh" onClose={onClose} />);
    fireEvent.click(screen.getByText("历史"));
    fireEvent.click(await screen.findByText("Send mail"));

    expect(await screen.findByText("任务已达到运行预算上限")).toBeDefined();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    expect(screen.queryByText(/"retryable"/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "关闭任务中心" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(retry).not.toHaveBeenCalled();
  });

  it("coalesces a replay burst into one lightweight refresh without loading runtimes", async () => {
    vi.useFakeTimers();
    try {
      render(<TaskDrawer open={false} language="en" onClose={() => {}} />);
      await act(async () => {
        await Promise.resolve();
      });
      expect(list).toHaveBeenCalledWith(recentRunsQuery);
      expect(list).toHaveBeenCalledWith(openRunsQuery);
      list.mockClear();
      runtimes.mockClear();

      act(() => {
        for (let index = 0; index < 100; index += 1) {
          emitRunEvent({ run_id: `run-${index}` });
        }
        vi.advanceTimersByTime(99);
      });
      expect(list).not.toHaveBeenCalled();

      await act(async () => {
        vi.advanceTimersByTime(100);
        await Promise.resolve();
      });
      expect(list).toHaveBeenCalledTimes(2);
      expect(list).toHaveBeenCalledWith(recentRunsQuery);
      expect(list).toHaveBeenCalledWith(openRunsQuery);
      expect(runtimes).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows at most one follow-up refresh while the prior refresh is in flight", async () => {
    vi.useFakeTimers();
    try {
      render(<TaskDrawer open={false} language="en" onClose={() => {}} />);
      await act(async () => {
        await Promise.resolve();
      });
      list.mockClear();

      let resolveRefresh: ((runs: typeof waitingRun[]) => void) | undefined;
      list.mockImplementationOnce(() => new Promise((resolve) => {
        resolveRefresh = resolve;
      }));

      act(() => {
        emitRunEvent({ run_id: "run-first" });
        vi.advanceTimersByTime(100);
      });
      expect(list).toHaveBeenCalledTimes(2);

      act(() => {
        for (let index = 0; index < 100; index += 1) {
          emitRunEvent({ run_id: `run-follow-up-${index}` });
        }
        vi.advanceTimersByTime(100);
      });
      expect(list).toHaveBeenCalledTimes(2);

      await act(async () => {
        resolveRefresh?.([waitingRun]);
        await Promise.resolve();
      });
      await act(async () => {
        vi.advanceTimersByTime(100);
        await Promise.resolve();
      });
      expect(list).toHaveBeenCalledTimes(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("cancels a queued event refresh when unmounted", async () => {
    vi.useFakeTimers();
    try {
      const view = render(<TaskDrawer open={false} language="en" onClose={() => {}} />);
      await act(async () => {
        await Promise.resolve();
      });
      list.mockClear();

      act(() => {
        emitRunEvent({ run_id: "run-late" });
      });
      view.unmount();
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(list).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not let a stale initial snapshot overwrite a newer event refresh", async () => {
    vi.useFakeTimers();
    try {
      const staleRun = { ...waitingRun, status: "succeeded" };
      let resolveInitial: ((runs: typeof staleRun[]) => void) | undefined;
      list.mockReset();
      list
        .mockImplementationOnce(() => new Promise((resolve) => {
          resolveInitial = resolve;
        }))
        .mockResolvedValueOnce([])
        .mockResolvedValue([waitingRun]);
      const onCountsChange = vi.fn();

      render(
        <TaskDrawer
          open={false}
          language="en"
          onClose={() => {}}
          onCountsChange={onCountsChange}
        />,
      );
      expect(list).toHaveBeenCalledTimes(2);

      await act(async () => {
        emitRunEvent({ run_id: "run-1" });
        vi.advanceTimersByTime(100);
        await Promise.resolve();
      });
      expect(list).toHaveBeenCalledTimes(4);
      expect(onCountsChange).toHaveBeenLastCalledWith({ active: 0, attention: 1 });
      const callsAfterFreshSnapshot = onCountsChange.mock.calls.length;

      await act(async () => {
        resolveInitial?.([staleRun]);
        await Promise.resolve();
      });
      expect(onCountsChange).toHaveBeenLastCalledWith({ active: 0, attention: 1 });
      expect(onCountsChange).toHaveBeenCalledTimes(callsAfterFreshSnapshot);
    } finally {
      vi.useRealTimers();
    }
  });

  it("bounds refresh latency during a continuous event stream", async () => {
    vi.useFakeTimers();
    try {
      render(<TaskDrawer open={false} language="en" onClose={() => {}} />);
      await act(async () => {
        await Promise.resolve();
      });
      list.mockClear();

      act(() => {
        for (let index = 0; index < 5; index += 1) {
          emitRunEvent({ run_id: `run-continuous-${index}` });
          vi.advanceTimersByTime(90);
        }
      });
      expect(list).not.toHaveBeenCalled();

      await act(async () => {
        vi.advanceTimersByTime(50);
        await Promise.resolve();
      });
      expect(list).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refreshes runtime status on a coalesced event while the runtime tab is visible", async () => {
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Runtimes"));
    expect(await screen.findByText("internal:agent")).toBeDefined();

    vi.useFakeTimers();
    try {
      list.mockClear();
      runtimes.mockClear();
      await act(async () => {
        emitRunEvent({ run_id: "run-runtime-change" });
        vi.advanceTimersByTime(100);
        await Promise.resolve();
      });
      expect(list).toHaveBeenCalledTimes(2);
      expect(runtimes).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("requires an explicit effect reconciliation instead of retrying attention runs", async () => {
    const attentionRun = {
      ...waitingRun,
      id: "run-attention",
      status: "needs_attention",
      summary: "External effect state is unknown",
      interactions: [],
      error: { message: "Worker stopped after dispatch", effect_state: "unknown" },
    };
    list.mockResolvedValue([attentionRun]);
    get.mockResolvedValue(attentionRun);

    render(<TaskDrawer open language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByText("Attention"));
    fireEvent.click(await screen.findByText("Send mail"));

    expect(screen.queryByText("Retry")).toBeNull();
    fireEvent.click(await screen.findByText("Not committed"));
    await waitFor(() => expect(reconcile).toHaveBeenCalledWith(
      "run-attention",
      "confirmed_not_committed",
    ));
  });
});
