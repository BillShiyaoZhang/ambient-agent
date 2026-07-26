import { beforeEach, describe, expect, it } from "vitest";
import {
  CHAT_SIZE_STORAGE_KEY,
  clampChatSize,
  loadChatSize,
  saveChatSize,
} from "../../frontend/src/lib/chatLayout";

describe("chat window sizing", () => {
  beforeEach(() => localStorage.clear());

  it("clamps dimensions to both interaction minimums and the viewport", () => {
    expect(clampChatSize(
      { width: 120, height: 180 },
      { innerWidth: 1440, innerHeight: 900 },
    )).toEqual({ width: 360, height: 420 });
    expect(clampChatSize(
      { width: 900, height: 1000 },
      { innerWidth: 800, innerHeight: 700 },
    )).toEqual({ width: 720, height: 604 });
  });

  it("persists a valid preferred size and ignores malformed storage", () => {
    saveChatSize({ width: 500, height: 640 });
    expect(JSON.parse(localStorage.getItem(CHAT_SIZE_STORAGE_KEY) ?? "null")).toEqual({ width: 500, height: 640 });
    expect(loadChatSize()).toEqual({ width: 500, height: 640 });

    localStorage.setItem(CHAT_SIZE_STORAGE_KEY, "{broken");
    expect(loadChatSize()).toEqual({ width: 432, height: 600 });
  });
});
