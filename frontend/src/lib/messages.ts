import type { Message } from "../components/ChatPanel";

/** Merge websocket messages without duplicating persisted message ids. */
export function mergeIncomingMessage(previous: Message[], incoming: Message): Message[] {
  if (incoming.id === -1) {
    const pendingIndex = previous.map((message) => message.id).lastIndexOf(-1);
    if (pendingIndex === -1) return [...previous, incoming];
    const next = [...previous];
    next[pendingIndex] = incoming;
    return next;
  }

  if (incoming.id !== undefined) {
    const withoutPending = previous.filter((message) => message.id !== -1);
    const existingIndex = withoutPending.findIndex((message) => message.id === incoming.id);
    if (existingIndex === -1) return [...withoutPending, incoming];
    return withoutPending
      .filter((message, index) => message.id !== incoming.id || index === existingIndex)
      .map((message, index) => index === existingIndex ? incoming : message);
  }

  return [...previous, incoming];
}
