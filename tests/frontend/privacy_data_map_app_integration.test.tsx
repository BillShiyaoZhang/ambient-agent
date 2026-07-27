import React from "react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "../../frontend/src/App";
import { SystemDrawer } from "../../frontend/src/components/system/SystemUI";
import wsService from "../../frontend/src/services/websocket";

const privacyMapResponse = {
  contract_version: 1,
  generated_at: "2026-07-20T08:30:00Z",
  scope: {
    kind: "workspace",
    observed_window: null,
  },
  source_health: {
    status: "healthy",
    valid_audit_records: 0,
    malformed_json_records: 0,
    structurally_invalid_audit_records: 0,
    projection_ineligible_audit_records: 0,
    oversized_audit_lines: 0,
    invalid_app_declarations: 0,
    unsafe_schema_ids: 0,
    missing_schema_references: 0,
  },
  coverage: {
    status: "partial",
    channels: [
      { id: "llm", observation: "partial" },
      { id: "mcp", observation: "not_instrumented" },
      { id: "http_agent", observation: "not_instrumented" },
      { id: "coding_agent_acp", observation: "not_instrumented" },
      { id: "provider_management", observation: "not_instrumented" },
      { id: "isolated_widget_runtime", observation: "not_instrumented" },
    ],
  },
  nodes: [
    {
      id: "platform:ambient-agent",
      kind: "platform",
      label: "Ambient Agent",
    },
  ],
  observed_flows: [],
  declared_associations: [],
  warnings: [],
};

let canvasResponse = {
  version: 3,
  open_app_ids: [] as string[],
  active_app_id: null as string | null,
  windows: {} as Record<string, {
    mode: "maximized";
    bounds: { x: number; y: number; width: number; height: number };
  }>,
};

vi.mock("../../frontend/src/services/websocket", () => {
  let callback: (data: any) => void = () => {};
  return {
    default: {
      connect: vi.fn((_url, _sessionId, nextCallback) => {
        callback = nextCallback;
      }),
      disconnect: vi.fn(),
      isConnected: vi.fn(() => true),
      sendMessage: vi.fn(),
      triggerMessage: (data: any) => callback(data),
    },
  };
});

vi.mock("../../frontend/src/components/AppCenter", () => ({
  AppCenter: ({ isOpen, mode = "overlay", headerActions }: {
    isOpen: boolean;
    mode?: "home" | "overlay";
    headerActions?: React.ReactNode;
  }) => isOpen ? (
    <div
      data-testid={`app-center-${mode}`}
      role={mode === "home" ? "main" : "dialog"}
      aria-modal={mode === "overlay" ? "true" : undefined}
      aria-label={mode === "home" ? "App Center home" : "App Center overlay"}
    >
      {headerActions}
    </div>
  ) : null,
}));

vi.mock("../../frontend/src/components/AuditLogPanel", () => ({
  AuditLogPanel: ({ isOpen, onClose }: {
    isOpen: boolean;
    onClose: () => void;
  }) => (
    <>
      <div data-testid="audit-drawer" data-open={String(isOpen)} />
      <SystemDrawer open={isOpen} onClose={onClose} label="Audit Log Test Drawer">
        <button type="button" onClick={onClose}>Close Audit Drawer</button>
      </SystemDrawer>
    </>
  ),
}));

vi.mock("../../frontend/src/components/TaskDrawer", () => ({
  TaskDrawer: ({ open, onClose }: {
    open: boolean;
    onClose: () => void;
  }) => (
    <>
      <div data-testid="task-drawer" data-open={String(open)} />
      <SystemDrawer open={open} onClose={onClose} label="Task Center Test Drawer">
        <button type="button" onClick={onClose}>Close Task Center Drawer</button>
      </SystemDrawer>
    </>
  ),
}));

vi.mock("../../frontend/src/components/LLMSettings", () => ({
  LLMSettingsDialog: ({ open }: { open: boolean }) => (
    <div data-testid="llm-settings" data-open={String(open)} />
  ),
}));

vi.mock("../../frontend/src/components/AgentChatOverlay", () => ({
  AgentChatOverlay: ({ onInspectRunInteraction }: {
    onInspectRunInteraction?: (interaction: {
      id: string;
      runId: string;
      kind: string;
      status: "pending";
      payload: Record<string, unknown>;
      createdAt: string;
    }) => void;
  }) => (
    <>
      <button
        type="button"
        onClick={() => onInspectRunInteraction?.({
          id: "plan-inspect",
          runId: "run-inspect",
          kind: "plan_approval",
          status: "pending",
          payload: {
            request_id: "plan-inspect",
            app_id: "example",
            plan: "plan",
          },
          createdAt: "2026-07-29T00:00:00Z",
        })}
      >
        Inspect plan interaction
      </button>
      <button
        type="button"
        onClick={() => onInspectRunInteraction?.({
          id: "schema-inspect",
          runId: "run-inspect",
          kind: "schema_approval",
          status: "pending",
          payload: {
            request_id: "schema-inspect",
            app_id: "example",
            proposal: {
              reused_schemas: [],
              new_schemas: [],
              capabilities: [],
            },
          },
          createdAt: "2026-07-29T00:00:00Z",
        })}
      >
        Inspect schema interaction
      </button>
      <button
        type="button"
        onClick={() => onInspectRunInteraction?.({
          id: "verification-inspect",
          runId: "run-inspect",
          kind: "verification_approval",
          status: "pending",
          payload: {
            request_id: "verification-inspect",
            app_id: "example",
            report: "report",
          },
          createdAt: "2026-07-29T00:00:00Z",
        })}
      >
        Inspect verification interaction
      </button>
    </>
  ),
}));

vi.mock("../../frontend/src/components/AppPermissionModal", () => ({
  AppPermissionModal: () => null,
}));

vi.mock("../../frontend/src/components/SandboxWidget", () => ({
  SandboxWidget: () => null,
}));

vi.mock("../../frontend/src/components/MutationPreview", () => ({
  MutationPreview: () => null,
}));

describe("Privacy Map App integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    canvasResponse = {
      version: 3,
      open_app_ids: [],
      active_app_id: null,
      windows: {},
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: string | URL | Request) => {
        const url = String(input);
        if (url.endsWith("/api/sessions")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve([
              { id: "session-1", title: "Session 1", language: "en" },
            ]),
          });
        }
        if (url.endsWith("/api/canvas")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(canvasResponse),
          });
        }
        if (url.includes("/api/apps/")) {
          const id = url.split("/").at(-1) ?? "example";
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
              id,
              title: "Example App",
              html: "<main>Example</main>",
              css: "",
              js: "",
            }),
          });
        }
        if (url.endsWith("/api/llm/settings")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
              default_model: null,
              fast_model: null,
            }),
          });
        }
        if (url.endsWith("/api/privacy-data-map")) {
          return Promise.resolve(
            new Response(JSON.stringify(privacyMapResponse), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            })
          );
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([]),
        });
      })
    );
  });

  it("keeps Privacy Map, Audit Log, and Task Center mutually exclusive", async () => {
    render(<App />);

    const privacyMap = await screen.findByRole("button", { name: "Privacy Map" });
    fireEvent.click(screen.getByRole("button", { name: "Task Center" }));
    expect(screen.getByTestId("task-drawer").dataset.open).toBe("true");

    fireEvent.click(privacyMap);
    expect(screen.getByRole("dialog", { name: "Privacy Map" })).toBeDefined();
    expect(screen.getByTestId("task-drawer").dataset.open).toBe("false");
    expect(screen.getByTestId("audit-drawer").dataset.open).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Audit log" }));
    expect(screen.queryByRole("dialog", { name: "Privacy Map" })).toBeNull();
    expect(screen.getByTestId("audit-drawer").dataset.open).toBe("true");
    expect(screen.getByTestId("task-drawer").dataset.open).toBe("false");
  });

  it("closes the modal App Center overlay before opening Privacy Map", async () => {
    canvasResponse = {
      version: 3,
      open_app_ids: ["example"],
      active_app_id: "example",
      windows: {
        example: {
          mode: "maximized",
          bounds: { x: 0.16, y: 0.12, width: 0.68, height: 0.72 },
        },
      },
    };
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open App Center" }));
    const appCenter = await screen.findByRole("dialog", { name: "App Center overlay" });
    expect(appCenter.getAttribute("aria-modal")).toBe("true");

    fireEvent.click(within(appCenter).getByRole("button", { name: "Privacy Map" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "App Center overlay" })).toBeNull();
      expect(screen.getAllByRole("dialog")).toHaveLength(1);
      expect(screen.getByRole("dialog", { name: "Privacy Map" })).toBeDefined();
    });
    await screen.findByText(
      "Privacy map updated: 1 node, 0 observed transfers, 0 declared associations."
    );
  });

  it("closes every system drawer before opening Models & Providers", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Privacy Map" }));
    expect(screen.getByRole("dialog", { name: "Privacy Map" })).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Models & Providers" }));
    expect(screen.queryByRole("dialog", { name: "Privacy Map" })).toBeNull();
    expect(screen.getByTestId("audit-drawer").dataset.open).toBe("false");
    expect(screen.getByTestId("task-drawer").dataset.open).toBe("false");
    expect(screen.getByTestId("llm-settings").dataset.open).toBe("true");
  });

  it("restores focus to the App Center trigger after a normal close", async () => {
    render(<App />);

    const trigger = await screen.findByRole("button", { name: "Privacy Map" });
    fireEvent.click(trigger);
    const close = screen.getByRole("button", { name: "Close privacy map" });
    await waitFor(() => expect(document.activeElement).toBe(close));

    fireEvent.click(close);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("keeps keyboard focus inside the real Privacy Map drawer", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Privacy Map" }));
    const dialog = screen.getByRole("dialog", { name: "Privacy Map" });
    await screen.findByText(
      "Privacy map updated: 1 node, 0 observed transfers, 0 declared associations."
    );

    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), " +
          "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
      )
    );
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) throw new Error("Privacy Map drawer did not expose focusable controls.");

    first.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });

  it.each([
    {
      triggerName: "Audit log",
      drawerTestId: "audit-drawer",
      closeName: "Close Audit Drawer",
    },
    {
      triggerName: "Task Center",
      drawerTestId: "task-drawer",
      closeName: "Close Task Center Drawer",
    },
  ])(
    "restores $triggerName focus to its visible toolbar trigger after switching from Privacy Map",
    async ({ triggerName, drawerTestId, closeName }) => {
      render(<App />);

      fireEvent.click(await screen.findByRole("button", { name: "Privacy Map" }));
      const hiddenPrivacyControl = screen.getByRole("button", {
        name: "Close privacy map",
      });
      await waitFor(() => expect(document.activeElement).toBe(hiddenPrivacyControl));

      const nextDrawerTrigger = screen.getByRole("button", { name: triggerName });
      expect(nextDrawerTrigger.closest("[data-system-toolbar]")).not.toBeNull();
      fireEvent.click(nextDrawerTrigger);

      await waitFor(() => {
        expect(screen.getByTestId(drawerTestId).dataset.open).toBe("true");
        expect(document.activeElement).toBe(
          screen.getByRole("button", { name: closeName })
        );
      });
      expect(hiddenPrivacyControl.closest('[aria-hidden="true"]')).not.toBeNull();

      fireEvent.click(screen.getByRole("button", { name: closeName }));

      await waitFor(() => {
        expect(document.activeElement).toBe(nextDrawerTrigger);
        expect(document.activeElement).not.toBe(hiddenPrivacyControl);
      });
    }
  );

  it("falls back to a visible control in the originating system toolbar", async () => {
    render(<App />);

    const trigger = await screen.findByRole("button", { name: "Privacy Map" });
    fireEvent.click(trigger);
    const close = screen.getByRole("button", { name: "Close privacy map" });
    await waitFor(() => expect(document.activeElement).toBe(close));

    trigger.style.display = "none";
    fireEvent.click(close);

    await waitFor(() => {
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Task Center" })
      );
    });
  });

  it.each([
    {
      type: "permission_request",
      request_id: "permission-1",
      tool_call: "write",
      details: "details",
    },
    {
      type: "backend_permission_request",
      request_id: "backend-permission-1",
    },
  ])("closes Privacy Map when $type becomes blocking", async (message) => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Privacy Map" }));
    expect(screen.getByRole("dialog", { name: "Privacy Map" })).toBeDefined();

    (
      wsService as typeof wsService & {
        triggerMessage: (data: typeof message) => void;
      }
    ).triggerMessage(message);

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Privacy Map" })).toBeNull();
    });
  });

  it.each([
    {
      inspectName: "Inspect plan interaction",
      blockingDialogName: "App Development Plan Confirmation",
    },
    {
      inspectName: "Inspect schema interaction",
      blockingDialogName: "Schema and Capability Alignment",
    },
    {
      inspectName: "Inspect verification interaction",
      blockingDialogName: "Schema Alignment Warning",
    },
  ].flatMap((interaction) => [
    {
      ...interaction,
      drawerTriggerName: "Privacy Map",
      drawerDialogName: "Privacy Map",
    },
    {
      ...interaction,
      drawerTriggerName: "Audit log",
      drawerDialogName: "Audit Log Test Drawer",
    },
    {
      ...interaction,
      drawerTriggerName: "Task Center",
      drawerDialogName: "Task Center Test Drawer",
    },
  ]))(
    "closes $drawerTriggerName before durable $inspectName opens a blocking dialog",
    async ({ inspectName, blockingDialogName, drawerTriggerName, drawerDialogName }) => {
      render(<App />);

      fireEvent.click(await screen.findByRole("button", { name: drawerTriggerName }));
      expect(screen.getByRole("dialog", { name: drawerDialogName })).toBeDefined();

      fireEvent.click(screen.getByRole("button", { name: inspectName }));

      await waitFor(() => {
        expect(screen.queryByRole("dialog", { name: drawerDialogName })).toBeNull();
        expect(screen.getByRole("dialog", { name: blockingDialogName })).toBeDefined();
        expect(screen.getAllByRole("dialog")).toHaveLength(1);
      });
    }
  );

  it("moves focus into a blocking dialog instead of restoring the Privacy Map trigger", async () => {
    render(<App />);

    const trigger = await screen.findByRole("button", { name: "Privacy Map" });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "Privacy Map" })).toBeDefined();

    (
      wsService as typeof wsService & {
        triggerMessage: (data: {
          type: string;
          request_id: string;
          tool_call: string;
          details: string;
        }) => void;
      }
    ).triggerMessage({
      type: "permission_request",
      request_id: "permission-focus",
      tool_call: "write",
      details: "details",
    });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Privacy Map" })).toBeNull();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Deny" }));
      expect(document.activeElement).not.toBe(trigger);
    });
  });

  it("restores blocking-dialog focus to a safe system control, not the hidden drawer", async () => {
    render(<App />);

    const trigger = await screen.findByRole("button", { name: "Privacy Map" });
    fireEvent.click(trigger);
    const hiddenDrawerControl = screen.getByRole("button", { name: "Close privacy map" });
    await waitFor(() => expect(document.activeElement).toBe(hiddenDrawerControl));

    act(() => {
      (
        wsService as typeof wsService & {
          triggerMessage: (data: {
            type: string;
            request_id: string;
            tool_call: string;
            details: string;
          }) => void;
        }
      ).triggerMessage({
        type: "permission_request",
        request_id: "permission-focus-return",
        tool_call: "write",
        details: "details",
      });
    });

    const deny = await screen.findByRole("button", { name: "Deny" });
    await waitFor(() => expect(document.activeElement).toBe(deny));
    expect(hiddenDrawerControl.closest('[aria-hidden="true"]')).not.toBeNull();

    fireEvent.click(deny);

    await waitFor(() => {
      expect(document.activeElement).toBe(trigger);
      expect(document.activeElement).not.toBe(hiddenDrawerControl);
    });
  });
});
