import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
});
