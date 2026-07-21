import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import { Widget } from "../../frontend/src/components/DashboardCanvas";
import wsService from "../../frontend/src/services/websocket";
import { runService } from "../../frontend/src/services/runs";
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
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:test",
      capabilities: [{ id: "graph.query", scope: { entities: ["Task"] } }],
    };

    render(<SandboxWidget widget={mockWidget} />);

    expect(register).toHaveBeenCalledWith(
      expect.stringMatching(/^graph:sub-/),
      expect.objectContaining({
        type: "graph_subscribe",
        app_id: "graph-widget-test",
        manifest_revision: "2:1.0.0",
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

  it("should consume Row flex layout props without forwarding invalid DOM attributes", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const mockWidget: Widget = {
      id: "layout-widget-test",
      title: "Layout Widget Test",
      js: `
        const { Row } = ambient.components;
        export default function App() {
          return ambient.html\`<\${Row} data-testid="layout-row" wrap=\${true} justify="space-between">content<//>\`;
        }
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    const row = screen.getByTestId("layout-row");
    expect(row.style.flexWrap).toBe("wrap");
    expect(row.style.justifyContent).toBe("space-between");
    expect(row.hasAttribute("wrap")).toBe(false);
    expect(row.hasAttribute("justify")).toBe(false);
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("should normalize HTM child arrays without React key warnings", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const mockWidget: Widget = {
      id: "static-siblings-widget-test",
      title: "Static Siblings Widget Test",
      js: `
        export default function App() {
          const labels = ["first", "second", "third"];
          return ambient.html\`<div data-testid="static-siblings">
            \${labels.map((label) => ambient.html\`<span>\${label}</span>\`)}
          </div>\`;
        }
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    expect(screen.getByTestId("static-siblings").textContent?.replace(/\s/g, "")).toBe("firstsecondthird");
    expect(consoleError.mock.calls.flat().join("\n")).not.toContain(
      'Each child in a list should have a unique "key" prop',
    );
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
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:test",
      capabilities: [{ id: "graph.query", scope: { entities: ["Task"] } }],
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
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:test",
      capabilities: [
        { id: "graph.mutate", scope: { entities: ["Task"], operations: ["create"] } },
      ],
    };

    render(<SandboxWidget widget={mockWidget} />);

    const btn = screen.getByTestId("mutate-btn");
    btn.click();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });
    const [url, request] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/apps/graph-widget-test/graph/mutate");
    expect(request).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const payload = JSON.parse(String(request?.body));
    expect(payload.actions).toEqual(actions);
    expect(payload.idempotency_key).toMatch(/^widget:graph-widget-test:/);
    expect(payload.manifest_revision).toBe("2:1.0.0");
  });

  it("should access only an app-scoped declared data source through ambient.net", async () => {
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ data: { temperature: 28 } }),
    });
    const mockWidget: Widget = {
      id: "weather-app",
      title: "Weather",
      js: `
        const { useEffect, useState } = ambient.react;
        export default function App() {
          const [temperature, setTemperature] = useState(null);
          useEffect(() => {
            ambient.net.request("forecast", {
              path: "/v1/forecast",
              method: "GET",
              query: { latitude: 31.23 }
            }).then((data) => setTemperature(data.temperature));
          }, []);
          return ambient.html\`<div data-testid="temperature">\${temperature}</div>\`;
        }
      `,
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:test",
      capabilities: [
        {
          id: "network.request",
          scope: {
            sources: {
              forecast: {
                base_url: "https://api.example.com",
                paths: ["/v1/forecast"],
                methods: ["GET"],
                response_limit: 4096,
              },
            },
          },
        },
      ],
    };

    render(<SandboxWidget widget={mockWidget} />);

    await waitFor(() => expect(screen.getByTestId("temperature").textContent).toBe("28"));
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/apps/weather-app/data-sources/forecast/request",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it("should expose only approved frozen SDK namespaces", async () => {
    const mockWidget: Widget = {
      id: "no-grants-widget",
      title: "No Grants",
      js: `
        export default function App() {
          const result = ["graph", "net", "files", "capabilities", "runs", "mcp"]
            .map((key) => \`\${key}:\${key in ambient}\`)
            .join(",");
          return ambient.html\`<div data-testid="surface">\${result}|frozen:\${String(Object.isFrozen(ambient))}</div>\`;
        }
      `,
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:empty",
      capabilities: [],
    };

    render(<SandboxWidget widget={mockWidget} />);

    expect(screen.getByTestId("surface").textContent).toBe(
      "graph:false,net:false,files:false,capabilities:false,runs:false,mcp:false|frozen:true",
    );
  });

  it("should invoke only a grant-scoped installed capability", async () => {
    vi.spyOn(runService, "start").mockResolvedValue({ id: "run-1" } as any);
    vi.spyOn(runService, "wait").mockResolvedValue({ status: "succeeded", result: { count: 3 } } as any);
    const mockWidget: Widget = {
      id: "calendar-ui",
      title: "Calendar",
      js: `
        const { Button } = ambient.components;
        export default function App() {
          const invoke = () => ambient.capabilities.invoke("mcp:calendar:calendar", {}, "list-events");
          return ambient.html\`<\${Button} data-testid="invoke" label="Load" onClick=\${invoke} />\`;
        }
      `,
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:invoke",
      capabilities: [
        {
          id: "capability.invoke",
          scope: { catalog_ids: ["mcp:calendar:calendar"], actions: ["list-events"] },
        },
      ],
    };

    render(<SandboxWidget widget={mockWidget} />);
    screen.getByTestId("invoke").click();
    await waitFor(() => expect(runService.start).toHaveBeenCalledWith(
      "mcp:calendar:calendar",
      "list-events",
      {},
      expect.objectContaining({ appId: "calendar-ui", manifestRevision: "2:1.0.0" }),
    ));
  });

  it("should bind approved file writes to the current app", async () => {
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) });
    const mockWidget: Widget = {
      id: "notes-app",
      title: "Notes",
      js: `
        const { Button } = ambient.components;
        export default function App() {
          const save = () => ambient.files.write("drafts/today.md", "hello");
          return ambient.html\`<\${Button} data-testid="save" label="Save" onClick=\${save} />\`;
        }
      `,
      manifest_revision: "2:1.0.0",
      grants_digest: "sha256:files",
      capabilities: [
        { id: "file.write", scope: { paths: ["drafts/**"], max_bytes: 1024 } },
      ],
    };

    render(<SandboxWidget widget={mockWidget} />);
    screen.getByTestId("save").click();
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/apps/notes-app/files/write",
      expect.objectContaining({ method: "POST" }),
    ));
  });
});
