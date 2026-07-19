import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { AgentChatOverlay } from "../../frontend/src/components/AgentChatOverlay";
import { ChatPanel } from "../../frontend/src/components/ChatPanel";

describe("chat message keys", () => {
  it("does not emit duplicate-key warnings while duplicate transport messages are settling", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
    const duplicateMessages = [
      { id: 1, sender: "user" as const, content: "hello", timestamp: "first" },
      { id: 1, sender: "user" as const, content: "hello", timestamp: "second" },
    ];

    render(<>
      <ChatPanel messages={duplicateMessages} onSendMessage={vi.fn()} isConnected />
      <AgentChatOverlay
        open
        unreadCount={0}
        messages={duplicateMessages}
        sessions={[]}
        activeSessionId={null}
        runningSessions={[]}
        isConnected
        language="en"
        onOpenChange={vi.fn()}
        onSendMessage={vi.fn()}
        onSelectSession={vi.fn()}
        onCreateSession={vi.fn()}
        onDeleteSession={vi.fn()}
      />
    </>);

    expect(error.mock.calls.some((call) => String(call[0]).includes("same key"))).toBe(false);
    error.mockRestore();
  });
});
