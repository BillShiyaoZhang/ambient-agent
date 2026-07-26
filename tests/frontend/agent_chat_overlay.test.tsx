import React from "react";
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
});
