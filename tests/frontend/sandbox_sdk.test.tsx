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
    
    const register = vi.spyOn(wsService, "registerPersistentMessage").mockImplementation((_key: string, msg: any) => {
      if (msg.type === "graph_subscribe") {
        subIdCaptured = msg.subscription_id;
      }
    });

    const callbackData = { nodes: [{ id: "n1", type: "Task", properties: { content: "Learn Vitest" } }] };

    const mockWidget: Widget = {
      id: "graph-widget-test",
      title: "Graph Widget Test",
      html: "",
      css: "",
      js: `
        const { useState, useEffect } = ambient.react;
        export default function App() {
          const [text, setText] = useState("No Data");
          useEffect(() => {
            return ambient.graph.subscribe({ type: "Task" }, (data) => {
              setText(data.nodes[0].properties.content);
            });
          }, []);
          return ambient.html\`<div data-testid="output">\${text}</div>\`;
        }
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    expect(register).toHaveBeenCalledWith(
      expect.stringMatching(/^graph:sub-/),
      expect.objectContaining({
        type: "graph_subscribe",
        query: { type: "Task" },
      })
    );
    expect(subIdCaptured).not.toBe("");

    const eventName = `graph_query_update:${subIdCaptured}`;
    window.dispatchEvent(
      new CustomEvent(eventName, {
        detail: callbackData,
      })
    );

    await waitFor(() => {
      const output = screen.getByTestId("output");
      expect(output.textContent).toBe("Learn Vitest");
    });
  });

  it("should unsubscribe correctly and send graph_unsubscribe message", async () => {
    let subIdCaptured = "";
    vi.spyOn(wsService, "registerPersistentMessage").mockImplementation((_key: string, msg: any) => {
      if (msg.type === "graph_subscribe") {
        subIdCaptured = msg.subscription_id;
      }
    });
    const unregister = vi.spyOn(wsService, "unregisterPersistentMessage").mockImplementation(() => {});

    const mockWidget: Widget = {
      id: "graph-widget-test",
      title: "Graph Widget Test",
      html: "",
      css: "",
      js: `
        const { useEffect } = ambient.react;
        const { Button } = ambient.components;
        export default function App() {
          let unsub;
          useEffect(() => {
            unsub = ambient.graph.subscribe({ type: "Task" }, (data) => {});
            return unsub;
          }, []);
          return ambient.html\`<\${Button} data-testid="unsub-btn" label="Unsubscribe" onClick=\${() => unsub && unsub()} />\`;
        }
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);
    expect(subIdCaptured).not.toBe("");

    const btn = screen.getByTestId("unsub-btn");
    btn.click();

    expect(unregister).toHaveBeenCalledWith(
      `graph:${subIdCaptured}`,
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
      html: "",
      css: "",
      js: `
        const { Button } = ambient.components;
        export default function App() {
          const handleMutate = () => {
            ambient.graph.mutate([
              { action: "create_node", type: "Task", properties: { content: "Do chores" } }
            ]);
          };
          return ambient.html\`<\${Button} data-testid="mutate-btn" label="Mutate" onClick=\${handleMutate} />\`;
        }
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
      html: "",
      css: "",
      js: `
        const { useState, useEffect } = ambient.react;
        export default function App() {
          const [val, setVal] = useState("No Data");
          useEffect(() => {
            ambient.mcp.callTool("calc", { x: 5, y: 10 }).then((res) => {
              setVal(JSON.stringify(res));
            });
          }, []);
          return ambient.html\`<div data-testid="output">\${val}</div>\`;
        }
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    expect(wsService.sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "mcp_call_tool",
        name: "calc",
        arguments: { x: 5, y: 10 },
      })
    );

    const calls = (wsService.sendMessage as any).mock.calls;
    const mcpCall = calls.find((c: any) => c[0].type === "mcp_call_tool");
    expect(mcpCall).toBeDefined();
    const callIdCaptured = mcpCall[0].call_id;

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
});
