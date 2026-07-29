import React, { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { list, get, runtimes, resolve, reconcile, subscribe, emitRunEvent } = vi.hoisted(() => {
  let listener: ((event: { run_id: string }) => void) | null = null;
  return {
    list: vi.fn(),
    get: vi.fn(),
    runtimes: vi.fn(),
    resolve: vi.fn(),
    reconcile: vi.fn(),
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
    retry: vi.fn(),
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
    fireEvent.click(await screen.findByText("Allow"));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith("interaction-1", { approved: true }));
  });

  it("lists backend runtimes in the same drawer", async () => {
    render(<TaskDrawer open language="en" onClose={() => {}} />);
    await waitFor(() => expect(list).toHaveBeenCalledWith(recentRunsQuery));
    expect(list).toHaveBeenCalledWith(openRunsQuery);
    expect(runtimes).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Runtimes"));
    expect(await screen.findByText("internal:agent")).toBeDefined();
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
