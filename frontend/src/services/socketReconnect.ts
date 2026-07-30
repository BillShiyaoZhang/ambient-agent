export type SocketConnectionState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "retrying"
  | "unavailable";

export const MAX_SOCKET_RECONNECT_ATTEMPTS = 5;
const SOCKET_RECONNECT_BASE_DELAY_MS = 500;
const SOCKET_RECONNECT_MAX_DELAY_MS = 8_000;

export function socketReconnectDelay(attempt: number): number {
  const safeAttempt = Math.max(1, Math.floor(attempt));
  return Math.min(
    SOCKET_RECONNECT_BASE_DELAY_MS * (2 ** (safeAttempt - 1)),
    SOCKET_RECONNECT_MAX_DELAY_MS,
  );
}

export function isNormalSocketClose(event: Pick<CloseEvent, "code"> | undefined): boolean {
  return event?.code === 1000;
}

export function isTerminalSocketClose(event: Pick<CloseEvent, "code"> | undefined): boolean {
  return event !== undefined && [1000, 1008, 4400, 4403, 4404].includes(event.code);
}
