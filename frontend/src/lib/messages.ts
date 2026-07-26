import type { Message } from "../components/ChatPanel";

/** Merge websocket messages without duplicating persisted message ids. */
export function mergeIncomingMessage(previous: Message[], incoming: Message): Message[] {
  if (!Number.isSafeInteger(incoming.id) || Number(incoming.id) < 1) return previous;
  const existingIndex = previous.findIndex((message) => message.id === incoming.id);
  if (existingIndex === -1) return [...previous, incoming];
  return previous
      .filter((message, index) => message.id !== incoming.id || index === existingIndex)
      .map((message, index) => index === existingIndex ? incoming : message);
}
