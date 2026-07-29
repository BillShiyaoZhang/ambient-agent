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

class RunLiveService {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private generation = 0;

  subscribe(
    sessionId: string,
    onEvent: (event: RunLiveEvent) => void,
    onReset: () => void,
  ): () => void {
    this.disconnect();
    const generation = ++this.generation;
    const connect = () => {
      if (generation !== this.generation) return;
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(
        `${scheme}://${window.location.hostname}:8000/ws/run-live?session_id=${encodeURIComponent(sessionId)}`
      );
      this.socket = socket;
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
      socket.onclose = () => {
        if (this.socket !== socket || generation !== this.generation) return;
        this.socket = null;
        onReset();
        this.reconnectTimer = window.setTimeout(connect, 1000);
      };
      socket.onerror = () => {
        // onclose owns reset and reconnect so errors never duplicate state changes.
      };
    };
    connect();
    return () => {
      if (generation !== this.generation) return;
      this.disconnect();
    };
  }

  disconnect(): void {
    this.generation += 1;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.close();
    }
  }
}

export const runLiveService = new RunLiveService();
