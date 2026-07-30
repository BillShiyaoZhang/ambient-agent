import { webSocketUrl } from "./apiBase";
import {
  isNormalSocketClose,
  MAX_SOCKET_RECONNECT_ATTEMPTS,
  socketReconnectDelay,
  type SocketConnectionState,
} from "./socketReconnect";

export type RunLiveEventKind = "assistant_message_delta" | "activity_delta" | "tool_progress";

export interface RunLiveEvent {
  schema_version: number;
  run_id: string;
  session_id: string;
  step_id: string;
  attempt: number;
  stream_id: string;
  chunk_sequence: number;
  kind: RunLiveEventKind;
  delta: string;
  replace: boolean;
  tool?: string;
  tool_status?: string;
  created_at: string;
}

const LIVE_KINDS = new Set<RunLiveEventKind>([
  "assistant_message_delta",
  "activity_delta",
  "tool_progress",
]);

const asRecord = (value: unknown): Record<string, unknown> | null => (
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

export function normalizeRunLiveEvent(value: unknown, sessionId?: string): RunLiveEvent | null {
  const event = asRecord(value);
  if (
    !event
    || event.schema_version !== 1
    || typeof event.run_id !== "string"
    || typeof event.session_id !== "string"
    || (sessionId !== undefined && event.session_id !== sessionId)
    || typeof event.step_id !== "string"
    || typeof event.stream_id !== "string"
    || typeof event.chunk_sequence !== "number"
    || !Number.isSafeInteger(event.chunk_sequence)
    || event.chunk_sequence < 1
    || typeof event.kind !== "string"
    || !LIVE_KINDS.has(event.kind as RunLiveEventKind)
    || typeof event.delta !== "string"
    || typeof event.created_at !== "string"
  ) return null;
  return {
    schema_version: 1,
    run_id: event.run_id,
    session_id: event.session_id,
    step_id: event.step_id,
    attempt: typeof event.attempt === "number" && Number.isSafeInteger(event.attempt)
      ? Math.max(1, event.attempt)
      : 1,
    stream_id: event.stream_id,
    chunk_sequence: event.chunk_sequence,
    kind: event.kind as RunLiveEventKind,
    delta: event.delta,
    replace: event.replace === true,
    tool: typeof event.tool === "string" ? event.tool : undefined,
    tool_status: typeof event.tool_status === "string" ? event.tool_status : undefined,
    created_at: event.created_at,
  };
}

export class RunLiveEventBatcher {
  private pending: RunLiveEvent[] = [];
  private timer: number | null = null;
  private readonly onFlush: (events: RunLiveEvent[]) => void;
  private readonly intervalMs: number;

  constructor(
    onFlush: (events: RunLiveEvent[]) => void,
    intervalMs = 50,
  ) {
    this.onFlush = onFlush;
    this.intervalMs = intervalMs;
  }

  push(event: RunLiveEvent): void {
    this.pending.push(event);
    if (this.timer !== null) return;
    this.timer = window.setTimeout(() => this.flushNow(), this.intervalMs);
  }

  flushNow(): void {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.pending.length === 0) return;
    const events = this.pending;
    this.pending = [];
    this.onFlush(events);
  }

  clear(): void {
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
    this.pending = [];
  }
}

export class RunLiveService {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private generation = 0;
  private connectionState: SocketConnectionState = "disconnected";
  private statusListener: ((state: SocketConnectionState) => void) | null = null;
  private retryConnect: (() => void) | null = null;

  private setConnectionState(state: SocketConnectionState): void {
    if (state === this.connectionState) return;
    this.connectionState = state;
    this.statusListener?.(state);
  }

  subscribe(
    sessionId: string,
    onEvent: (event: RunLiveEvent) => void,
    onReset: () => void,
    onStatus?: (state: SocketConnectionState) => void,
  ): () => void {
    this.disconnect();
    this.statusListener = onStatus ?? null;
    this.reconnectAttempt = 0;
    const generation = ++this.generation;
    const connect = () => {
      if (generation !== this.generation) return;
      this.setConnectionState(this.reconnectAttempt > 0 ? "retrying" : "connecting");
      const socket = new WebSocket(
        webSocketUrl(`/ws/run-live?session_id=${encodeURIComponent(sessionId)}`),
      );
      this.socket = socket;
      socket.onopen = () => {
        if (this.socket !== socket || generation !== this.generation) return;
        this.reconnectAttempt = 0;
        this.setConnectionState("connected");
      };
      socket.onmessage = (message) => {
        if (this.socket !== socket || generation !== this.generation) return;
        let payload: unknown;
        try {
          payload = JSON.parse(message.data);
        } catch {
          return;
        }
        const frame = asRecord(payload);
        if (frame?.type !== "run_live_event") return;
        const event = normalizeRunLiveEvent(frame.event, sessionId);
        if (event) onEvent(event);
      };
      socket.onclose = (event) => {
        if (this.socket !== socket || generation !== this.generation) return;
        this.socket = null;
        onReset();
        if (isNormalSocketClose(event)) {
          this.setConnectionState("disconnected");
          return;
        }
        if (this.reconnectAttempt >= MAX_SOCKET_RECONNECT_ATTEMPTS) {
          this.setConnectionState("unavailable");
          return;
        }
        this.reconnectAttempt += 1;
        this.setConnectionState("retrying");
        this.reconnectTimer = window.setTimeout(
          connect,
          socketReconnectDelay(this.reconnectAttempt),
        );
      };
      socket.onerror = () => {
        // onclose owns reset and reconnect so errors never duplicate state changes.
      };
    };
    this.retryConnect = connect;
    connect();
    return () => {
      if (generation !== this.generation) return;
      this.disconnect();
    };
  }

  getConnectionState(): SocketConnectionState {
    return this.connectionState;
  }

  retryConnection(): boolean {
    if (
      !this.retryConnect
      || this.connectionState === "connected"
      || this.connectionState === "connecting"
    ) return false;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const staleSocket = this.socket;
    this.socket = null;
    staleSocket?.close();
    this.reconnectAttempt = 0;
    this.setConnectionState("connecting");
    this.retryConnect();
    return true;
  }

  disconnect(): void {
    this.generation += 1;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.close();
    }
    this.setConnectionState("disconnected");
    this.statusListener = null;
    this.retryConnect = null;
  }
}

export const runLiveService = new RunLiveService();
