import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import {
  AppWorkspace,
  type WidgetRuntimeLifecycle,
} from "../../frontend/src/components/AppWorkspace";
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

const RuntimeProbe: React.FC<{
  id: string;
  title: string;
  lifecycle: Exclude<WidgetRuntimeLifecycle, "suspended">;
  onActivate: () => void;
  onSuspendReady: () => void;
}> = ({ id, title, lifecycle, onActivate, onSuspendReady }) => (
  <div data-testid={`runtime-${id}`}>
    <span>{id} runtime</span>
    <button type="button" onClick={onActivate}>Activate {title}</button>
    {lifecycle === "suspending" ? (
      <button type="button" onClick={onSuspendReady}>
        Finish suspending {title}
      </button>
    ) : null}
  </div>
);

describe("App-first workspace UI", () => {
  const renderWorkspace = (
    change = vi.fn(),
    onOpenPrivacyMap = vi.fn()
  ) => render(<AppWorkspace
      widgets={[
        { id: "weather", title: "Weather", html: "", css: "", js: "" },
        { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
      ]}
      canvas={canvas}
      onCanvasChange={change}
      renderWidgetContent={(widget) => <div>{widget.id} content</div>}
      onOpenAppStore={vi.fn()}
      onOpenPrivacyMap={onOpenPrivacyMap}
      onOpenAudit={vi.fn()}
      language="en"
      onLanguageChange={vi.fn()}
      theme={{ preference: "system", effective: "dark" }}
      onThemeChange={vi.fn()}
    />);

  it("keeps active and warm apps mounted and closes a window without uninstalling it", () => {
    const change = vi.fn();
    renderWorkspace(change);

    expect(screen.getByText("weather content")).toBeDefined();
    expect(screen.getByText("tasks content")).toBeDefined();
    fireEvent.click(within(screen.getByTestId("workspace-system-chrome")).getByRole("button", { name: "Close app" }));
    expect(change).toHaveBeenCalledWith(expect.objectContaining({ open_app_ids: ["weather"] }), true);
  });

  it("keeps only active plus one warm runtime and resumes suspended apps on demand", () => {
    const lifecycleCanvas: CanvasConfigV3 = {
      version: 3,
      open_app_ids: ["weather", "calendar", "notes", "tasks"],
      active_app_id: "tasks",
      windows: {
        weather: { mode: "floating", bounds: { x: 0.02, y: 0.04, width: 0.45, height: 0.44 } },
        calendar: { mode: "floating", bounds: { x: 0.5, y: 0.04, width: 0.45, height: 0.44 } },
        notes: { mode: "floating", bounds: { x: 0.02, y: 0.52, width: 0.45, height: 0.44 } },
        tasks: { mode: "floating", bounds: { x: 0.5, y: 0.52, width: 0.45, height: 0.44 } },
      },
    };
    let currentCanvas = lifecycleCanvas;

    const Harness = () => {
      const [current, setCurrent] = React.useState(lifecycleCanvas);
      return <AppWorkspace
        widgets={[
          { id: "weather", title: "Weather", html: "", css: "", js: "" },
          { id: "calendar", title: "Calendar", html: "", css: "", js: "" },
          { id: "notes", title: "Notes", html: "", css: "", js: "" },
          { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
        ]}
        canvas={current}
        onCanvasChange={(next) => {
          currentCanvas = next;
          setCurrent(next);
        }}
        renderWidgetContent={(widget, lifecycle, onActivate, onSuspendReady) => (
          <RuntimeProbe
            id={widget.id}
            title={widget.title}
            lifecycle={lifecycle}
            onActivate={onActivate}
            onSuspendReady={onSuspendReady}
          />
        )}
        onOpenAppStore={vi.fn()}
        onOpenAudit={vi.fn()}
        language="en"
        onLanguageChange={vi.fn()}
        theme={{ preference: "system", effective: "dark" }}
        onThemeChange={vi.fn()}
      />;
    };

    render(<Harness />);

    expect(screen.queryByText("weather runtime")).toBeNull();
    expect(screen.queryByText("calendar runtime")).toBeNull();
    expect(screen.getByText("notes runtime")).toBeDefined();
    expect(screen.getByText("tasks runtime")).toBeDefined();
    expect(document.querySelector('[data-window-id="notes"]')?.getAttribute("data-runtime-lifecycle")).toBe("warm");
    expect(document.querySelector('[data-window-id="tasks"]')?.getAttribute("data-runtime-lifecycle")).toBe("active");
    expect(screen.getByRole("button", { name: "Resume Weather" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Resume Calendar" })).toBeDefined();
    expect(screen.getAllByText("Saved state restores; memory-only state resets")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Resume Weather" }));

    expect(screen.getByText("weather runtime")).toBeDefined();
    expect(screen.getByText("tasks runtime")).toBeDefined();
    expect(screen.getByText("notes runtime")).toBeDefined();
    expect(document.querySelector('[data-window-id="weather"]')?.getAttribute("data-runtime-lifecycle")).toBe("active");
    expect(document.querySelector('[data-window-id="tasks"]')?.getAttribute("data-runtime-lifecycle")).toBe("warm");
    expect(document.querySelector('[data-window-id="notes"]')?.getAttribute("data-runtime-lifecycle")).toBe("suspending");
    expect(screen.getAllByTestId(/^runtime-/)).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Finish suspending Notes" }));
    expect(screen.queryByText("notes runtime")).toBeNull();
    expect(screen.getByRole("button", { name: "Resume Notes" })).toBeDefined();
    expect(currentCanvas.open_app_ids).toEqual(["calendar", "notes", "tasks", "weather"]);
    expect(currentCanvas.active_app_id).toBe("weather");
    expect(currentCanvas.windows).toEqual(lifecycleCanvas.windows);
  });

  it("caps rapid switching at three runtimes, cancels reactivated suspension, and has a workspace timeout", () => {
    vi.useFakeTimers();
    const lifecycleCanvas: CanvasConfigV3 = {
      version: 3,
      open_app_ids: ["weather", "calendar", "notes", "tasks"],
      active_app_id: "tasks",
      windows: {
        weather: { mode: "floating", bounds: { x: 0.02, y: 0.04, width: 0.45, height: 0.44 } },
        calendar: { mode: "floating", bounds: { x: 0.5, y: 0.04, width: 0.45, height: 0.44 } },
        notes: { mode: "floating", bounds: { x: 0.02, y: 0.52, width: 0.45, height: 0.44 } },
        tasks: { mode: "floating", bounds: { x: 0.5, y: 0.52, width: 0.45, height: 0.44 } },
      },
    };

    const Harness = () => {
      const [current, setCurrent] = React.useState(lifecycleCanvas);
      return <AppWorkspace
        widgets={[
          { id: "weather", title: "Weather", html: "", css: "", js: "" },
          { id: "calendar", title: "Calendar", html: "", css: "", js: "" },
          { id: "notes", title: "Notes", html: "", css: "", js: "" },
          { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
        ]}
        canvas={current}
        onCanvasChange={(next) => setCurrent(next)}
        renderWidgetContent={(widget, lifecycle, onActivate, onSuspendReady) => (
          <RuntimeProbe
            id={widget.id}
            title={widget.title}
            lifecycle={lifecycle}
            onActivate={onActivate}
            onSuspendReady={onSuspendReady}
          />
        )}
        onOpenAppStore={vi.fn()}
        onOpenAudit={vi.fn()}
        language="en"
        onLanguageChange={vi.fn()}
        theme={{ preference: "system", effective: "dark" }}
        onThemeChange={vi.fn()}
      />;
    };

    try {
      render(<Harness />);
      fireEvent.click(screen.getByRole("button", { name: "Resume Weather" }));
      expect(document.querySelector('[data-window-id="notes"]')?.getAttribute("data-runtime-lifecycle")).toBe("suspending");
      expect(screen.getAllByTestId(/^runtime-/)).toHaveLength(3);

      fireEvent.click(screen.getByRole("button", { name: "Resume Calendar" }));
      expect(screen.getAllByTestId(/^runtime-/)).toHaveLength(3);
      expect(screen.queryByTestId("runtime-notes")).toBeNull();
      expect(document.querySelector('[data-window-id="tasks"]')?.getAttribute("data-runtime-lifecycle")).toBe("suspending");

      fireEvent.click(screen.getByRole("button", { name: "Activate Tasks" }));
      expect(document.querySelector('[data-window-id="tasks"]')?.getAttribute("data-runtime-lifecycle")).toBe("active");
      expect(screen.getByTestId("runtime-tasks")).toBeDefined();
      expect(screen.getAllByTestId(/^runtime-/)).toHaveLength(3);

      const switcher = screen.getByTestId("workspace-system-chrome");
      for (let index = 0; index < 20; index += 1) {
        const title = ["Weather", "Calendar", "Notes", "Tasks"][index % 4];
        fireEvent.click(within(switcher).getByRole("button", { name: title }));
        expect(screen.queryAllByTestId(/^runtime-/).length).toBeLessThanOrEqual(3);
        expect(document.querySelectorAll('[data-runtime-lifecycle="suspending"]').length).toBeLessThanOrEqual(1);
      }

      act(() => vi.advanceTimersByTime(1_249));
      expect(screen.getAllByTestId(/^runtime-/)).toHaveLength(3);
      act(() => vi.advanceTimersByTime(1));
      expect(screen.getAllByTestId(/^runtime-/)).toHaveLength(2);
      expect(screen.getByTestId("runtime-tasks")).toBeDefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores an old suspension acknowledgement after the same app starts a new cycle", () => {
    const lifecycleCanvas: CanvasConfigV3 = {
      version: 3,
      open_app_ids: ["weather", "calendar", "notes", "tasks"],
      active_app_id: "tasks",
      windows: {
        weather: { mode: "floating", bounds: { x: 0.02, y: 0.04, width: 0.45, height: 0.44 } },
        calendar: { mode: "floating", bounds: { x: 0.5, y: 0.04, width: 0.45, height: 0.44 } },
        notes: { mode: "floating", bounds: { x: 0.02, y: 0.52, width: 0.45, height: 0.44 } },
        tasks: { mode: "floating", bounds: { x: 0.5, y: 0.52, width: 0.45, height: 0.44 } },
      },
    };
    const latestReady: Record<string, () => void> = {};

    const Harness = () => {
      const [current, setCurrent] = React.useState(lifecycleCanvas);
      return <AppWorkspace
        widgets={[
          { id: "weather", title: "Weather", html: "", css: "", js: "" },
          { id: "calendar", title: "Calendar", html: "", css: "", js: "" },
          { id: "notes", title: "Notes", html: "", css: "", js: "" },
          { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
        ]}
        canvas={current}
        onCanvasChange={(next) => setCurrent(next)}
        renderWidgetContent={(widget, lifecycle, onActivate, onSuspendReady) => {
          if (lifecycle === "suspending") latestReady[widget.id] = onSuspendReady;
          return (
            <RuntimeProbe
              id={widget.id}
              title={widget.title}
              lifecycle={lifecycle}
              onActivate={onActivate}
              onSuspendReady={onSuspendReady}
            />
          );
        }}
        onOpenAppStore={vi.fn()}
        onOpenAudit={vi.fn()}
        language="en"
        onLanguageChange={vi.fn()}
        theme={{ preference: "system", effective: "dark" }}
        onThemeChange={vi.fn()}
      />;
    };

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Resume Weather" }));
    const oldNotesReady = latestReady.notes;
    expect(oldNotesReady).toBeTypeOf("function");

    fireEvent.click(screen.getByRole("button", { name: "Activate Notes" }));
    fireEvent.click(screen.getByRole("button", { name: "Resume Calendar" }));
    fireEvent.click(screen.getByRole("button", { name: "Resume Tasks" }));
    const currentNotesReady = latestReady.notes;
    expect(currentNotesReady).toBeTypeOf("function");
    expect(currentNotesReady).not.toBe(oldNotesReady);
    expect(document.querySelector('[data-window-id="notes"]')?.getAttribute("data-runtime-lifecycle")).toBe("suspending");

    act(() => oldNotesReady());
    expect(screen.getByTestId("runtime-notes")).toBeDefined();
    expect(document.querySelector('[data-window-id="notes"]')?.getAttribute("data-runtime-lifecycle")).toBe("suspending");

    act(() => currentNotesReady());
    expect(screen.queryByTestId("runtime-notes")).toBeNull();
  });

  it("releases a pending runtime immediately when its window closes or widget disappears", () => {
    vi.useFakeTimers();
    const lifecycleCanvas: CanvasConfigV3 = {
      version: 3,
      open_app_ids: ["weather", "calendar", "notes", "tasks"],
      active_app_id: "tasks",
      windows: {
        weather: { mode: "floating", bounds: { x: 0.02, y: 0.04, width: 0.45, height: 0.44 } },
        calendar: { mode: "floating", bounds: { x: 0.5, y: 0.04, width: 0.45, height: 0.44 } },
        notes: { mode: "floating", bounds: { x: 0.02, y: 0.52, width: 0.45, height: 0.44 } },
        tasks: { mode: "floating", bounds: { x: 0.5, y: 0.52, width: 0.45, height: 0.44 } },
      },
    };

    const Harness = () => {
      const [current, setCurrent] = React.useState(lifecycleCanvas);
      const [widgetIds, setWidgetIds] = React.useState([
        "weather",
        "calendar",
        "notes",
        "tasks",
      ]);
      return <>
        <button type="button" onClick={() => setWidgetIds((ids) => ids.filter((id) => id !== "tasks"))}>
          Remove Tasks catalog
        </button>
        <AppWorkspace
          widgets={widgetIds.map((id) => ({
            id,
            title: id.slice(0, 1).toUpperCase() + id.slice(1),
            html: "",
            css: "",
            js: "",
          }))}
          canvas={current}
          onCanvasChange={(next) => setCurrent(next)}
          renderWidgetContent={(widget, lifecycle, onActivate, onSuspendReady) => (
            <RuntimeProbe
              id={widget.id}
              title={widget.title}
              lifecycle={lifecycle}
              onActivate={onActivate}
              onSuspendReady={onSuspendReady}
            />
          )}
          onOpenAppStore={vi.fn()}
          onOpenAudit={vi.fn()}
          language="en"
          onLanguageChange={vi.fn()}
          theme={{ preference: "system", effective: "dark" }}
          onThemeChange={vi.fn()}
        />
      </>;
    };

    try {
      render(<Harness />);
      fireEvent.click(screen.getByRole("button", { name: "Resume Weather" }));
      const notesWindow = document.querySelector('[data-window-id="notes"]');
      expect(notesWindow?.getAttribute("data-runtime-lifecycle")).toBe("suspending");
      fireEvent.click(within(notesWindow as HTMLElement).getByRole("button", { name: "Close app" }));
      expect(screen.queryByTestId("runtime-notes")).toBeNull();
      expect(document.querySelector('[data-window-id="notes"]')).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Resume Calendar" }));
      expect(document.querySelector('[data-window-id="tasks"]')?.getAttribute("data-runtime-lifecycle")).toBe("suspending");
      fireEvent.click(screen.getByRole("button", { name: "Remove Tasks catalog" }));
      expect(screen.queryByTestId("runtime-tasks")).toBeNull();
      expect(document.querySelector('[data-window-id="tasks"]')).toBeNull();

      act(() => vi.advanceTimersByTime(2_000));
      expect(screen.queryByTestId("runtime-notes")).toBeNull();
      expect(screen.queryByTestId("runtime-tasks")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("falls back to the most recent renderable app when the persisted active app is unavailable", () => {
    const change = vi.fn();
    const unavailableActiveCanvas: CanvasConfigV3 = {
      version: 3,
      open_app_ids: ["weather", "missing", "tasks"],
      active_app_id: "missing",
      windows: {
        weather: { mode: "floating", bounds: { x: 0.1, y: 0.1, width: 0.5, height: 0.6 } },
        missing: { mode: "maximized", bounds: { x: 0.16, y: 0.12, width: 0.68, height: 0.72 } },
        tasks: { mode: "floating", bounds: { x: 0.2, y: 0.2, width: 0.5, height: 0.6 } },
      },
    };

    render(<AppWorkspace
      widgets={[
        { id: "weather", title: "Weather", html: "", css: "", js: "" },
        { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
      ]}
      canvas={unavailableActiveCanvas}
      onCanvasChange={change}
      renderWidgetContent={(widget, _lifecycle, onActivate) => (
        <button type="button" onClick={onActivate}>
          Activate {widget.title}
        </button>
      )}
      onOpenAppStore={vi.fn()}
      onOpenAudit={vi.fn()}
      language="en"
      onLanguageChange={vi.fn()}
      theme={{ preference: "system", effective: "dark" }}
      onThemeChange={vi.fn()}
    />);

    expect(document.querySelector('[data-window-id="tasks"]')?.getAttribute("data-runtime-lifecycle")).toBe("active");
    expect(document.querySelector('[data-window-id="weather"]')?.getAttribute("data-runtime-lifecycle")).toBe("warm");
    expect(screen.getByRole("button", { name: "Activate Tasks" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Activate Weather" })).toBeDefined();
    expect(change).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Activate Tasks" }));

    expect(change).toHaveBeenCalledWith({
      ...unavailableActiveCanvas,
      open_app_ids: ["weather", "missing", "tasks"],
      active_app_id: "tasks",
    }, true);
  });

  it("promotes a warm iframe when its authenticated runtime reports activation", () => {
    let currentCanvas = canvas;
    const change = vi.fn();
    const Harness = () => {
      const [current, setCurrent] = React.useState(canvas);
      return <AppWorkspace
        widgets={[
          { id: "weather", title: "Weather", html: "", css: "", js: "" },
          { id: "tasks", title: "Tasks", html: "", css: "", js: "" },
        ]}
        canvas={current}
        onCanvasChange={(next, persist) => {
          change(next, persist);
          currentCanvas = next;
          setCurrent(next);
        }}
        renderWidgetContent={(widget, _lifecycle, onActivate?: () => void) => (
          <button type="button" onClick={onActivate}>
            Activate {widget.title}
          </button>
        )}
        onOpenAppStore={vi.fn()}
        onOpenAudit={vi.fn()}
        language="en"
        onLanguageChange={vi.fn()}
        theme={{ preference: "system", effective: "dark" }}
        onThemeChange={vi.fn()}
      />;
    };

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Activate Weather" }));

    expect(currentCanvas.active_app_id).toBe("weather");
    expect(currentCanvas.open_app_ids).toEqual(["tasks", "weather"]);
    expect(change).toHaveBeenLastCalledWith(currentCanvas, true);

    const persistedChanges = change.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Activate Weather" }));
    expect(change).toHaveBeenCalledTimes(persistedChanges);
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

  it("opens Privacy Map from desktop chrome with the exact trigger", () => {
    const onOpenPrivacyMap = vi.fn();
    renderWorkspace(vi.fn(), onOpenPrivacyMap);

    fireEvent.click(screen.getByRole("button", { name: "Layout" }));
    expect(screen.getByText("Focus current app")).toBeDefined();

    const action = within(
      screen.getByTestId("workspace-system-chrome")
    ).getByRole("button", { name: "Privacy Map" });
    fireEvent.click(action);

    expect(onOpenPrivacyMap).toHaveBeenCalledWith(action);
    expect(screen.queryByText("Focus current app")).toBeNull();
  });

  it("opens Privacy Map from the mobile menu and restores to the More trigger", () => {
    const onOpenPrivacyMap = vi.fn();
    renderWorkspace(vi.fn(), onOpenPrivacyMap);

    const moreTrigger = screen.getByRole("button", { name: "More" });
    fireEvent.click(moreTrigger);
    const menu = screen.getByRole("menu", { name: "More workspace actions" });
    fireEvent.click(within(menu).getByRole("button", { name: "Privacy Map" }));

    expect(onOpenPrivacyMap).toHaveBeenCalledWith(moreTrigger);
    expect(screen.queryByRole("menu", { name: "More workspace actions" })).toBeNull();
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

  it("labels Ambient and coding models as separate roles", () => {
    render(<AgentChatOverlay
      open
      unreadCount={0}
      messages={[]}
      sessions={[]}
      activeSessionId={null}
      runningSessions={[]}
      isConnected
      language="zh"
      onOpenChange={vi.fn()}
      onSendMessage={vi.fn()}
      onSelectSession={vi.fn()}
      onCreateSession={vi.fn()}
      onDeleteSession={vi.fn()}
      codingAgent={{ id: "codex", name: "Codex" } as never}
      codingAgentModel={{ mode: "native" }}
    />);

    expect(screen.getByText("Ambient")).toBeDefined();
    expect(screen.getByText("代码 · Codex · Agent 默认")).toBeDefined();
  });
});
