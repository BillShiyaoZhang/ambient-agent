import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { AppWorkspace } from "../../frontend/src/components/AppWorkspace";
import { resolveChromeMode } from "../../frontend/src/components/system/chromeLayout";
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
  const renderWorkspace = (change = vi.fn()) => render(<AppWorkspace
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

  it("keeps multiple apps mounted and closes a window without uninstalling it", () => {
    const change = vi.fn();
    renderWorkspace(change);

    expect(screen.getByText("weather content")).toBeDefined();
    expect(screen.getByText("tasks content")).toBeDefined();
    fireEvent.click(within(screen.getByTestId("workspace-system-chrome")).getByRole("button", { name: "Close app" }));
    expect(change).toHaveBeenCalledWith(expect.objectContaining({ open_app_ids: ["weather"] }), true);
  });

  it("moves maximized window controls into one system chrome row", () => {
    renderWorkspace();
    const chrome = screen.getByTestId("workspace-system-chrome");
    expect(within(chrome).getByText("Tasks")).toBeDefined();
    expect(within(chrome).getByRole("button", { name: "Close app" })).toBeDefined();
    expect(within(chrome).getByRole("button", { name: "Restore window" })).toBeDefined();
    expect(screen.queryByTestId("window-titlebar-tasks")).toBeNull();
    expect(screen.getByTestId("window-titlebar-weather")).toBeDefined();
  });

  it("keeps one workspace menu open and restores trigger focus on Escape", () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "Layout" }));
    expect(screen.getByText("Focus current app")).toBeDefined();

    const theme = screen.getByRole("button", { name: "Theme" });
    fireEvent.click(theme);
    expect(screen.queryByText("Focus current app")).toBeNull();
    expect(screen.getByText("System")).toBeDefined();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("System")).toBeNull();
    expect(document.activeElement).toBe(theme);
  });

  it("resolves desktop, compact and mobile action visibility", () => {
    expect(resolveChromeMode(1440)).toBe("desktop");
    expect(resolveChromeMode(1024)).toBe("desktop");
    expect(resolveChromeMode(900)).toBe("compact");
    expect(resolveChromeMode(720)).toBe("compact");
    expect(resolveChromeMode(719)).toBe("mobile");
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
