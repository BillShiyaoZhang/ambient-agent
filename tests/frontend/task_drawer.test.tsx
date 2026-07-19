import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { list, get, runtimes, resolve, subscribe } = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  runtimes: vi.fn(),
  resolve: vi.fn(),
  subscribe: vi.fn(() => () => {}),
}));

vi.mock("../../frontend/src/services/runs", () => ({
  runService: {
    list,
    get,
    runtimes,
    resolve,
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

describe("TaskDrawer", () => {
  beforeEach(() => {
    list.mockResolvedValue([waitingRun]);
    get.mockResolvedValue(waitingRun);
    runtimes.mockResolvedValue([{ id: "internal:agent", type: "internal", managed: false, status: "healthy" }]);
    resolve.mockResolvedValue({ ...waitingRun, status: "queued" });
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
    fireEvent.click(screen.getByText("Runtimes"));
    expect(await screen.findByText("internal:agent")).toBeDefined();
  });
});
