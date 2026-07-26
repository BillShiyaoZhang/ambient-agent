import { describe, expect, it } from "vitest";
import { mergeIncomingMessage } from "../../frontend/src/lib/messages";

describe("mergeIncomingMessage", () => {
  it("replaces an existing persisted message instead of duplicating its React key", () => {
    const history = [{ id: 1, sender: "user" as const, content: "hello" }];

    expect(mergeIncomingMessage(history, { id: 1, sender: "user", content: "hello" })).toEqual(history);
  });

  it("ignores messages that do not have a persisted positive id", () => {
    const history = [{ id: 1, sender: "user" as const, content: "hello" }];
    expect(mergeIncomingMessage(history, { id: 0, sender: "agent", content: "working" })).toBe(history);
  });
});
