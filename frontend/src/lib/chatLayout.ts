export interface ChatSize {
  width: number;
  height: number;
}

export type ChatSizePreset = "compact" | "default" | "wide";

export const CHAT_SIZE_STORAGE_KEY = "ambient_chat_size_v1";
export const CHAT_MOBILE_BREAKPOINT = 720;
export const CHAT_MIN_SIZE: ChatSize = { width: 360, height: 420 };
export const CHAT_SIZE_PRESETS: Record<ChatSizePreset, ChatSize> = {
  compact: { width: 380, height: 520 },
  default: { width: 432, height: 600 },
  wide: { width: 620, height: 720 },
};

export function normalizeChatSize(size: ChatSize): ChatSize {
  return {
    width: Math.round(Math.min(720, Math.max(CHAT_MIN_SIZE.width, size.width))),
    height: Math.round(Math.max(CHAT_MIN_SIZE.height, size.height)),
  };
}

export function clampChatSize(
  size: ChatSize,
  viewport: Pick<Window, "innerWidth" | "innerHeight"> = window,
): ChatSize {
  const preferred = normalizeChatSize(size);
  const maxWidth = Math.max(CHAT_MIN_SIZE.width, Math.min(720, viewport.innerWidth - 32));
  const maxHeight = Math.max(CHAT_MIN_SIZE.height, viewport.innerHeight - 96);
  return {
    width: Math.round(Math.min(maxWidth, preferred.width)),
    height: Math.round(Math.min(maxHeight, preferred.height)),
  };
}

export function loadChatSize(): ChatSize {
  try {
    const stored = JSON.parse(localStorage.getItem(CHAT_SIZE_STORAGE_KEY) ?? "null") as Partial<ChatSize> | null;
    if (typeof stored?.width === "number" && typeof stored.height === "number") {
      return normalizeChatSize({ width: stored.width, height: stored.height });
    }
  } catch {
    // Ignore stale or malformed local preferences.
  }
  return CHAT_SIZE_PRESETS.default;
}

export function saveChatSize(size: ChatSize): void {
  try {
    localStorage.setItem(CHAT_SIZE_STORAGE_KEY, JSON.stringify(size));
  } catch {
    // Local storage may be unavailable in private or embedded contexts.
  }
}
