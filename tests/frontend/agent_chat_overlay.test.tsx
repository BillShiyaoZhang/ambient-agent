import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentChatOverlay } from "../../frontend/src/components/AgentChatOverlay";
import { CHAT_SIZE_STORAGE_KEY } from "../../frontend/src/lib/chatLayout";

describe("AgentChatOverlay", () => {
  const commonProps = {
    open: true,
    unreadCount: 0,
    sessions: [],
    activeSessionId: null,
    runningSessions: [],
    isConnected: true,
    language: "en" as const,
    onOpenChange: vi.fn(),
    onSendMessage: vi.fn(),
    onSelectSession: vi.fn(),
    onCreateSession: vi.fn(),
    onDeleteSession: vi.fn(),
  };

  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 1000 });
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  });

  it("moves keyboard focus to the named composer when the chat opens", () => {
    const view = render(
      <AgentChatOverlay {...commonProps} open={false} messages={[]} />,
    );
    expect(screen.queryByRole("textbox")).toBeNull();

    view.rerender(<AgentChatOverlay {...commonProps} open messages={[]} />);
    const composer = screen.getByRole("textbox", {
      name: "Message Ambient… Type / for commands",
    });
    expect(document.activeElement).toBe(composer);
  });

  it("explains an exhausted command connection and lets the user retry", () => {
    const onRetryConnection = vi.fn();
    render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      isConnected={false}
      connectionState="unavailable"
      onRetryConnection={onRetryConnection}
    />);

    expect(screen.getByText("Connection unavailable")).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain(
      "Responses and new messages stay on this device until the connection returns.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    expect(onRetryConnection).toHaveBeenCalledTimes(1);
  });

  it("keeps a composed message when the connection races closed during send", () => {
    const onSendMessage = vi.fn(() => false);
    render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      onSendMessage={onSendMessage}
    />);

    const composer = screen.getByRole("textbox", {
      name: "Message Ambient… Type / for commands",
    }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Keep this draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSendMessage).toHaveBeenCalledWith("Keep this draft");
    expect(composer.value).toBe("Keep this draft");
  });

  it("applies and remembers desktop size presets", () => {
    render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
    />);

    const panel = screen.getByLabelText("Agent chat");
    expect(panel.getAttribute("style")).toContain("width: 432px");
    fireEvent.click(screen.getByRole("button", { name: "Chat window size" }));
    fireEvent.click(screen.getByRole("button", { name: /Compact/ }));

    expect(panel.getAttribute("style")).toContain("width: 380px");
    expect(panel.getAttribute("style")).toContain("height: 520px");
    expect(JSON.parse(localStorage.getItem(CHAT_SIZE_STORAGE_KEY) ?? "null")).toEqual({
      width: 380,
      height: 520,
    });
  });

  it("keeps the chat-history delete control compact so the title owns the row", () => {
    render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      sessions={[{
        id: "session-one",
        title: "A conversation title that needs room",
        updated_at: "2026-07-29T00:00:00Z",
      }]}
      activeSessionId="session-one"
    />);

    fireEvent.click(screen.getByRole("button", { name: "Chat history" }));
    const deleteButton = screen.getByRole("button", { name: "Delete A conversation title that needs room" });
    expect(deleteButton.classList.contains("chat-history-delete")).toBe(true);
    const stylesheet = readFileSync(
      resolve(process.cwd(), "src/components/Workspace.css"),
      "utf8",
    );
    expect(stylesheet).toMatch(/\.chat-history-delete\s*\{[^}]*width:\s*28px[^}]*min-width:\s*28px/);
    expect(stylesheet).toMatch(/\.chat-history-list\s*\{[^}]*overflow-x:\s*hidden/);
  });

  it("prevents deleting a conversation while it still has active tasks", () => {
    render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      sessions={[{ id: "session-one", title: "Session One" }]}
      activeSessionId="session-one"
      runningSessions={["session-one"]}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Chat history" }));
    const deleteButton = screen.getByRole("button", {
      name: "Finish or cancel active tasks before deleting Session One",
    }) as HTMLButtonElement;
    expect(deleteButton.disabled).toBe(true);
  });

  it("restores the preferred size after a temporary viewport clamp", () => {
    localStorage.setItem(CHAT_SIZE_STORAGE_KEY, JSON.stringify({ width: 620, height: 720 }));
    render(<AgentChatOverlay {...commonProps} messages={[]} />);
    const panel = screen.getByLabelText("Agent chat");

    Object.defineProperty(window, "innerWidth", { configurable: true, value: 500 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 600 });
    act(() => window.dispatchEvent(new Event("resize")));
    expect(panel.getAttribute("style")).toContain("width: 468px");
    expect(panel.getAttribute("style")).toContain("height: 504px");

    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 1000 });
    act(() => window.dispatchEvent(new Event("resize")));
    expect(panel.getAttribute("style")).toContain("width: 620px");
    expect(panel.getAttribute("style")).toContain("height: 720px");
    expect(JSON.parse(localStorage.getItem(CHAT_SIZE_STORAGE_KEY) ?? "null")).toEqual({ width: 620, height: 720 });
  });

  it("stops auto-following while the user reads older progress", () => {
    const run = {
      id: "run-one",
      status: "running" as const,
      phase: "stage_code",
      attempt: 1,
      summary: "",
      createdAt: "2026-07-26T00:00:00Z",
      updatedAt: "2026-07-26T00:00:01Z",
      modelTurns: 1,
      repairCount: 0,
      artifactCount: 0,
      activities: [],
    };
    const { container, rerender } = render(<AgentChatOverlay {...commonProps} messages={[]} runCards={[run]} />);
    const scroller = container.querySelector(".agent-chat-messages");
    expect(scroller).not.toBeNull();
    Object.defineProperties(scroller!, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, value: 100, writable: true },
    });
    fireEvent.scroll(scroller!);

    rerender(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      runCards={[{ ...run, updatedAt: "2026-07-26T00:00:02Z" }]}
    />);
    expect(screen.getByRole("button", { name: "View latest progress" })).toBeDefined();
  });

  it("keeps completed widget work collapsed until the user opens it", () => {
    const completedRun = {
      id: "run-complete",
      status: "succeeded" as const,
      phase: "promote",
      workflowType: "widget_create",
      attempt: 1,
      summary: "Published",
      createdAt: "2026-07-26T00:00:00Z",
      updatedAt: "2026-07-26T00:01:00Z",
      modelTurns: 4,
      repairCount: 1,
      artifactCount: 1,
      activities: [{
        id: "phase:promote",
        phase: "promote",
        status: "completed" as const,
        detail: "Published the verified App",
        updates: 0,
        toolCalls: 0,
        startedAt: "2026-07-26T00:00:50Z",
        updatedAt: "2026-07-26T00:01:00Z",
      }],
    };
    const { container } = render(<AgentChatOverlay {...commonProps} messages={[]} runCards={[completedRun]} />);
    const toggle = container.querySelector(".chat-run-header");
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Published the verified App")).toBeNull();

    fireEvent.click(toggle!);
    expect(screen.getByText("Published the verified App")).toBeDefined();
  });

  it("does not show widget phases for a normal conversation Run", () => {
    const { container } = render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      runCards={[{
        id: "run-converse",
        status: "running",
        phase: "converse",
        workflowType: "converse",
        attempt: 1,
        summary: "",
        createdAt: "2026-07-26T00:00:00Z",
        updatedAt: "2026-07-26T00:00:01Z",
        modelTurns: 1,
        repairCount: 0,
        artifactCount: 0,
        activities: [],
      }]}
    />);
    expect(container.querySelector(".chat-run-phase-rail")).toBeNull();
  });

  it("labels replies produced inside an external Skill sandbox", () => {
    const { container } = render(<AgentChatOverlay
      {...commonProps}
      messages={[{
        id: 7,
        sender: "agent",
        content: "A suggestion influenced by third-party guidance.",
        context_policy: "display_only",
        provenance: {
          kind: "external_skill_output",
          skills: [{ catalog_id: "agent-skill:external:review-notes" }],
        },
      }]}
    />);

    expect(screen.getByText("External Skill sandbox · review-notes")).toBeDefined();
    expect(container.querySelector(".agent-message.is-external-skill")).not.toBeNull();
  });

  it("renders ordinary approval as an inline Run interaction", () => {
    const onResolveRunInteraction = vi.fn();
    const onInspectRunInteraction = vi.fn();
    const interaction = {
      id: "interaction-one",
      runId: "run-one",
      kind: "plan_approval",
      status: "pending" as const,
      payload: {
        type: "plan_approval_request",
        request_id: "interaction-one",
        app_id: "weather-app",
        plan: "Build a weather dashboard with hourly conditions.",
      },
      createdAt: "2026-07-26T00:00:01Z",
    };
    render(<AgentChatOverlay
      {...commonProps}
      messages={[]}
      runCards={[{
        id: "run-one",
        status: "waiting_user",
        phase: "wait_plan",
        workflowType: "widget_create",
        attempt: 1,
        summary: "Approve development plan",
        createdAt: "2026-07-26T00:00:00Z",
        updatedAt: "2026-07-26T00:00:01Z",
        modelTurns: 1,
        repairCount: 0,
        artifactCount: 0,
        activities: [],
      }]}
      interactions={{ "interaction-one": interaction }}
      onResolveRunInteraction={onResolveRunInteraction}
      onInspectRunInteraction={onInspectRunInteraction}
    />);

    expect(screen.getByText("Review development plan")).toBeDefined();
    expect(screen.getByText(/Build a weather dashboard/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Approve plan" }));
    expect(onResolveRunInteraction).toHaveBeenCalledWith(interaction, "approve");
    fireEvent.click(screen.getByRole("button", { name: "Review / edit" }));
    expect(onInspectRunInteraction).toHaveBeenCalledWith(interaction);
  });
});
