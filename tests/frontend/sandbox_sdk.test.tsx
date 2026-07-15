import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import { Widget } from "../../frontend/src/components/DashboardCanvas";
import wsService from "../../frontend/src/services/websocket";
import React from "react";

describe("SandboxWidget with ambient SDK Injection (Graph DB APIs)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    vi.spyOn(wsService, "sendMessage").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("should inject ambient.graph.subscribe and trigger callback when update event is fired", async () => {
    let subIdCaptured = "";
    
    // We will intercept the WebSocket subscription message to get the auto-generated subId
    vi.spyOn(wsService, "sendMessage").mockImplementation((msg: any) => {
      if (msg.type === "graph_subscribe") {
        subIdCaptured = msg.subscription_id;
      }
    });

    const callbackData = { nodes: [{ id: "n1", type: "Task", properties: { content: "Learn Vitest" } }] };

    const mockWidget: Widget = {
      id: "graph-widget-test",
      title: "Graph Widget Test",
      html: '<div id="output" data-testid="output">No Data</div>',
      css: "",
      js: `
        const unsubscribe = ambient.graph.subscribe({ type: "Task" }, (data) => {
          root.querySelector("#output").textContent = data.nodes[0].properties.content;
        });
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    // Verify graph_subscribe message was sent
    expect(wsService.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "graph_subscribe",
        query: { type: "Task" },
      })
    );
    expect(subIdCaptured).not.toBe("");

    // Simulate WS response by dispatching the custom window event
    const eventName = `graph_query_update:${subIdCaptured}`;
    window.dispatchEvent(
      new CustomEvent(eventName, {
        detail: callbackData,
      })
    );

    // Wait for DOM to update
    await waitFor(() => {
      const output = screen.getByTestId("output");
      expect(output.textContent).toBe("Learn Vitest");
    });
  });

  it("should unsubscribe correctly and send graph_unsubscribe message", async () => {
    let subIdCaptured = "";
    vi.spyOn(wsService, "sendMessage").mockImplementation((msg: any) => {
      if (msg.type === "graph_subscribe") {
        subIdCaptured = msg.subscription_id;
      }
    });

    const mockWidget: Widget = {
      id: "graph-widget-test",
      title: "Graph Widget Test",
      html: '<button id="unsub-btn" data-testid="unsub-btn">Unsubscribe</button>',
      css: "",
      js: `
        const unsubscribe = ambient.graph.subscribe({ type: "Task" }, (data) => {});
        root.querySelector("#unsub-btn").addEventListener("click", () => {
          unsubscribe();
        });
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);
    expect(subIdCaptured).not.toBe("");

    // Click unsubscribe button
    const btn = screen.getByTestId("unsub-btn");
    btn.click();

    // Verify graph_unsubscribe message was sent
    expect(wsService.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "graph_unsubscribe",
        subscription_id: subIdCaptured,
      })
    );
  });

  it("should allow mutating graph data via ambient.graph.mutate", async () => {
    const mockMutateResult = { success: true };
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => mockMutateResult,
    });

    const actions = [
      { action: "create_node", type: "Task", properties: { content: "Do chores" } }
    ];

    const mockWidget: Widget = {
      id: "graph-widget-test",
      title: "Graph Widget Test",
      html: '<button id="mutate-btn" data-testid="mutate-btn">Mutate</button>',
      css: "",
      js: `
        root.querySelector("#mutate-btn").addEventListener("click", () => {
          ambient.graph.mutate([
            { action: "create_node", type: "Task", properties: { content: "Do chores" } }
          ]);
        });
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    const btn = screen.getByTestId("mutate-btn");
    btn.click();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "http://localhost:8000/api/graph/mutate",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actions }),
        })
      );
    });
  });

  it("should support ambient.mcp.callTool and resolve promise on WebSocket response", async () => {
    const mockWidget: Widget = {
      id: "mcp-widget-test",
      title: "MCP Widget Test",
      html: '<div id="output" data-testid="output">No Data</div>',
      css: "",
      js: `
        ambient.mcp.callTool("calc", { x: 5, y: 10 }).then((res) => {
          root.querySelector("#output").textContent = JSON.stringify(res);
        });
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    // Intercept WebSocket message to get the auto-generated callId
    expect(wsService.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "mcp_call_tool",
        name: "calc",
        arguments: { x: 5, y: 10 },
      })
    );

    // Retrieve the callId from the mock implementation call history
    const calls = (wsService.sendMessage as any).mock.calls;
    const mcpCall = calls.find((c: any) => c[0].type === "mcp_call_tool");
    expect(mcpCall).toBeDefined();
    const callIdCaptured = mcpCall[0].call_id;

    // Simulate WS response
    const eventName = `mcp_call_response:mcp-widget-test:${callIdCaptured}`;
    window.dispatchEvent(
      new CustomEvent(eventName, {
        detail: { result: { sum: 15 } },
      })
    );

    await waitFor(() => {
      const output = screen.getByTestId("output");
      expect(output.textContent).toBe(JSON.stringify({ sum: 15 }));
    });
  });

  it("should support ambient.agent.connect and receive state snapshots", async () => {
    const mockWidget: Widget = {
      id: "agent-widget-test",
      title: "Agent Widget Test",
      layout: JSON.stringify([
        {
          id: "root",
          type: "Text",
          props: { text: { binding: "/agent_status" } }
        }
      ]),
      js: `
        const unsubscribe = ambient.agent.connect();
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    expect(wsService.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "ag_ui_message",
        app_id: "agent-widget-test",
        message: { type: "connect" },
      })
    );

    // Simulate ag_ui_event representing STATE_SNAPSHOT
    const eventName = `ag_ui_event:agent-widget-test`;
    act(() => {
      window.dispatchEvent(
        new CustomEvent(eventName, {
          detail: {
            type: "STATE_SNAPSHOT",
            state: { agent_status: "Agent is thinking..." }
          },
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Agent is thinking...")).toBeDefined();
    });
  });
});
