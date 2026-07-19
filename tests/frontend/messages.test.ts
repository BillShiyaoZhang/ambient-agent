import { describe, expect, it } from "vitest";
import { mergeIncomingMessage } from "../../frontend/src/lib/messages";

describe("mergeIncomingMessage", () => {
  it("replaces an existing persisted message instead of duplicating its React key", () => {
    const history = [{ id: 1, sender: "user" as const, content: "hello" }];

    expect(mergeIncomingMessage(history, { id: 1, sender: "user", content: "hello" })).toEqual(history);
  });

  it("replaces the pending message when a persisted reply arrives", () => {
    const pending = [{ id: -1, sender: "agent" as const, content: "working" }];

    expect(mergeIncomingMessage(pending, { id: 2, sender: "agent", content: "done" })).toEqual([
      { id: 2, sender: "agent", content: "done" },
    ]);
  });
});
