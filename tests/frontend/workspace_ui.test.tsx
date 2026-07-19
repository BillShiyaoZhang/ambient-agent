import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AppWorkspace } from "../../frontend/src/components/AppWorkspace";
import { AgentChatOverlay } from "../../frontend/src/components/AgentChatOverlay";
import type { CanvasConfigV3 } from "../../frontend/src/lib/windowManager";

const canvas: CanvasConfigV3 = {
  version: 3,
  open_app_ids: ["weather", "tasks"],
  active_app_id: "tasks",
  windows: {
    weather: { mode: "floating", bounds: { x: 0.1, y: 0.1, width: 0.5, height: 0.6 } },
    tasks: { mode: "maximized", bounds: { x: 0.16, y: 0.12, width: 0.68, height: 0.72 } },
  },
};

describe("App-first workspace UI", () => {
  it("keeps multiple apps mounted and closes a window without uninstalling it", () => {
    const change = vi.fn();
    render(<AppWorkspace
      widgets={[
        { id: "weather", title: "Weather", html: "", css: "", js: "" },
        { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
      ]}
      canvas={canvas}
      onCanvasChange={change}
      renderWidgetContent={(widget) => <div>{widget.id} content</div>}
      onOpenAppStore={vi.fn()}
      onOpenAudit={vi.fn()}
      language="en"
      onLanguageChange={vi.fn()}
      theme={{ preference: "system", effective: "dark" }}
      onThemeChange={vi.fn()}
    />);

    expect(screen.getByText("weather content")).toBeDefined();
    expect(screen.getByText("tasks content")).toBeDefined();
    fireEvent.click(screen.getAllByRole("button", { name: "Close app" })[1]);
    expect(change).toHaveBeenCalledWith(expect.objectContaining({ open_app_ids: ["weather"] }), true);
  });

  it("opens chat without changing the workspace dimensions", () => {
    const openChange = vi.fn();
    render(<div data-testid="workspace" style={{ width: 900, height: 700 }}>
      <AgentChatOverlay
        open={false}
        unreadCount={2}
        messages={[]}
        sessions={[]}
        activeSessionId={null}
        runningSessions={[]}
        isConnected
        language="en"
        onOpenChange={openChange}
        onSendMessage={vi.fn()}
        onSelectSession={vi.fn()}
        onCreateSession={vi.fn()}
        onDeleteSession={vi.fn()}
      />
    </div>);
    const workspace = screen.getByTestId("workspace");
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(openChange).toHaveBeenCalledWith(true);
    expect(workspace.style.width).toBe("900px");
    expect(workspace.style.height).toBe("700px");
  });
});
