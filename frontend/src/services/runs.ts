import type { RunEvent as GeneratedRunEvent } from "../types/run-events.generated";

export type RunStatus =
  | "queued"
  | "running"
  | "waiting_user"
  | "cancel_requested"
  | "needs_attention"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface RunInteraction {
  id: string;
  run_id: string;
  type: string;
  prompt: string;
  payload: Record<string, unknown>;
  status: "pending" | "resolved";
  response?: unknown;
  created_at: string;
  resolved_at?: string | null;
}

export interface AmbientRun {
  id: string;
  owner_id: string;
  action_id: string;
  action_title: string;
  source_type: string;
  source_id?: string | null;
  adapter_type: string;
  runtime_id: string;
  status: RunStatus;
  progress: number;
  summary: string;
  input: unknown;
  result?: unknown;
  artifacts?: Array<{ type: string; id?: string; path?: string; title?: string }>;
  correlation?: {
    projection_type?: string;
    call_id?: string;
    app_id?: string;
    catalog_id?: string;
    [key: string]: unknown;
  } | null;
  error?: {
    message?: string;
    type?: string;
    effect_state?: "none" | "committed" | "unknown";
    reconciliation?: string;
  } | null;
  parent_run_id?: string | null;
  retry_of?: string | null;
  attempt: number;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  interactions?: RunInteraction[];
  events?: RunEvent[];
}

export type RunEvent = GeneratedRunEvent;

export type EffectReconciliation =
  | "confirmed_not_committed"
  | "compensated"
  | "confirmed_committed";

export interface RunStreamCursor {
  stream_epoch: string | null;
  sequence: number;
}

export interface RuntimeSnapshot {
  id: string;
  type: "mcp" | "http_agent" | "internal";
  managed: boolean;
  status: string;
  pid?: number | null;
  endpoint?: string;
}

const API_BASE = `http://${window.location.hostname}:8000`;

const RUN_SEQUENCE_KEY = "ambient_run_sequence";
const RUN_STREAM_EPOCH_KEY = "ambient_run_stream_epoch";
const MAX_SEEN_EVENT_IDS = 2048;

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asSequence(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function asEpoch(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function loadRunStreamCursor(): RunStreamCursor {
  try {
    const storedSequence = Number(sessionStorage.getItem(RUN_SEQUENCE_KEY) || "0");
    return {
      stream_epoch: asEpoch(sessionStorage.getItem(RUN_STREAM_EPOCH_KEY)),
      sequence: Number.isSafeInteger(storedSequence) && storedSequence >= 0 ? storedSequence : 0,
    };
  } catch {
    return { stream_epoch: null, sequence: 0 };
  }
}

function normalizeRunEvent(value: unknown): RunEvent | null {
  const event = asRecord(value);
  if (!event) return null;
  const sequence = asSequence(event.sequence);
  if (
    sequence === null
    || sequence === 0
    || typeof event.run_id !== "string"
    || event.run_id.length === 0
    || typeof event.type !== "string"
    || event.type.length === 0
    || typeof event.created_at !== "string"
  ) {
    return null;
  }
  const schemaVersion = event.schema_version ?? 1;
  if (
    typeof schemaVersion !== "number"
    || !Number.isSafeInteger(schemaVersion)
    || schemaVersion < 1
  ) {
    return null;
  }
  const durationMs = event.duration_ms;
  if (durationMs !== undefined && durationMs !== null && (
    typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs < 0
  )) return null;
  const modelUsage = event.model_usage === undefined || event.model_usage === null
    ? null
    : asRecord(event.model_usage);
  if (event.model_usage !== undefined && event.model_usage !== null && !modelUsage) return null;
  return {
    sequence,
    event_id: typeof event.event_id === "string" && event.event_id.length > 0
      ? event.event_id
      : `legacy:${event.run_id}:${sequence}`,
    schema_version: schemaVersion,
    stream_epoch: asEpoch(event.stream_epoch) ?? "legacy",
    run_id: event.run_id,
    session_id: typeof event.session_id === "string" || event.session_id === null
      ? event.session_id
      : null,
    step_id: typeof event.step_id === "string" || event.step_id === null
      ? event.step_id
      : null,
    attempt: typeof event.attempt === "number" || event.attempt === null
      ? event.attempt
      : null,
    trace_id: typeof event.trace_id === "string" && event.trace_id.length > 0
      ? event.trace_id
      : event.run_id,
    duration_ms: typeof durationMs === "number" ? durationMs : null,
    model_usage: modelUsage,
    redacted: event.redacted === true,
    type: event.type,
    payload: event.payload,
    created_at: event.created_at,
  };
}

export class RunService {
  private socket: WebSocket | null = null;
  private listeners = new Set<(event: RunEvent) => void>();
  private cursor = loadRunStreamCursor();
  private reconnectTimer: number | null = null;
  private seenEventIds = new Set<string>();
  private seenEventIdOrder: string[] = [];
  private replayingFromStart = false;
  private pendingGap: { streamEpoch: string | null; afterSequence: number; receivedSequence: number } | null = null;
  private correlatedRuns = new Set<string>();
  private projectedCorrelatedRuns = new Set<string>();
  private correlationProjectionInFlight = new Set<string>();
  private correlationProjectionDirty = new Set<string>();
  private correlationRetryAttempts = new Map<string, number>();
  private correlationRetryTimers = new Map<string, number>();

  private correlationEventName(correlation: NonNullable<AmbientRun["correlation"]>): string | null {
    const projectionType = correlation.projection_type;
    const callId = correlation.call_id;
    if (typeof projectionType !== "string" || typeof callId !== "string" || !callId) return null;
    if (projectionType === "mcp_call_response" || projectionType === "mcp_read_response") {
      return typeof correlation.app_id === "string" && correlation.app_id
        ? `${projectionType}:${correlation.app_id}:${callId}`
        : null;
    }
    if (projectionType === "capability_call_response") {
      return typeof correlation.catalog_id === "string" && correlation.catalog_id
        ? `${projectionType}:${correlation.catalog_id}:${callId}`
        : null;
    }
    return null;
  }

  private async projectCorrelatedCompletion(runId: string): Promise<void> {
    if (this.projectedCorrelatedRuns.has(runId)) return;
    if (this.correlationProjectionInFlight.has(runId)) {
      // Do not lose a later terminal event merely because the Run lookup
      // started by run_created is still in flight.
      this.correlationProjectionDirty.add(runId);
      return;
    }
    this.correlationProjectionInFlight.add(runId);
    let lookupFailed = false;
    try {
      const run = await this.get(runId);
      this.correlationRetryAttempts.delete(runId);
      const retryTimer = this.correlationRetryTimers.get(runId);
      if (retryTimer !== undefined) {
        window.clearTimeout(retryTimer);
        this.correlationRetryTimers.delete(runId);
      }
      if (!["succeeded", "failed", "cancelled", "needs_attention"].includes(run.status)) return;
      const correlation = run.correlation;
      if (!correlation) return;
      const eventName = this.correlationEventName(correlation);
      if (!eventName) return;
      const detail: Record<string, unknown> = {
        ...correlation,
        type: correlation.projection_type,
        run_id: run.id,
      };
      if (run.status === "succeeded") {
        detail.result = run.result;
      } else {
        detail.error = run.error?.message || run.summary || run.status;
      }
      this.projectedCorrelatedRuns.add(runId);
      window.dispatchEvent(new CustomEvent(eventName, { detail }));
    } catch {
      lookupFailed = true;
    } finally {
      this.correlationProjectionInFlight.delete(runId);
      const dirty = this.correlationProjectionDirty.delete(runId);
      if (!this.projectedCorrelatedRuns.has(runId) && dirty) {
        void this.projectCorrelatedCompletion(runId);
      } else if (!this.projectedCorrelatedRuns.has(runId) && lookupFailed) {
        this.scheduleCorrelationRetry(runId);
      }
    }
  }

  private scheduleCorrelationRetry(runId: string): void {
    if (this.correlationRetryTimers.has(runId) || this.projectedCorrelatedRuns.has(runId)) return;
    const attempt = (this.correlationRetryAttempts.get(runId) || 0) + 1;
    // The canonical cursor is allowed to advance even when the compatibility
    // lookup fails, so retries cannot depend on another event arriving.
    if (attempt > 8) return;
    this.correlationRetryAttempts.set(runId, attempt);
    const delay = Math.min(250 * (2 ** (attempt - 1)), 4_000);
    const timer = window.setTimeout(() => {
      this.correlationRetryTimers.delete(runId);
      void this.projectCorrelatedCompletion(runId);
    }, delay);
    this.correlationRetryTimers.set(runId, timer);
  }

  private persistCursor(): void {
    try {
      sessionStorage.setItem(RUN_SEQUENCE_KEY, String(this.cursor.sequence));
      if (this.cursor.stream_epoch) {
        sessionStorage.setItem(RUN_STREAM_EPOCH_KEY, this.cursor.stream_epoch);
      } else {
        sessionStorage.removeItem(RUN_STREAM_EPOCH_KEY);
      }
    } catch {
      // Browsers may deny storage in private/embedded contexts. The in-memory
      // cursor still provides ordered delivery for the current page lifetime.
    }
  }

  private rememberEvent(eventId: string | undefined): boolean {
    if (!eventId) return true;
    if (this.seenEventIds.has(eventId)) return false;
    this.seenEventIds.add(eventId);
    this.seenEventIdOrder.push(eventId);
    if (this.seenEventIdOrder.length > MAX_SEEN_EVENT_IDS) {
      const expired = this.seenEventIdOrder.shift();
      if (expired) this.seenEventIds.delete(expired);
    }
    return true;
  }

  private dispatchStreamSignal(type: "reset" | "gap", detail: Record<string, unknown>): void {
    window.dispatchEvent(new CustomEvent(`ambient_run_stream_${type}`, { detail }));
  }

  private scheduleReconnect(delay = 1000): void {
    if (this.listeners.size === 0 || this.reconnectTimer !== null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private restartStream(delay = 0): void {
    const staleSocket = this.socket;
    this.socket = null;
    if (staleSocket && staleSocket.readyState < WebSocket.CLOSING) staleSocket.close();
    this.scheduleReconnect(delay);
  }

  private resetCursor(streamEpoch: string | null, reason: string): void {
    const previous = { ...this.cursor };
    this.cursor = { stream_epoch: streamEpoch, sequence: 0 };
    this.replayingFromStart = true;
    this.pendingGap = null;
    this.persistCursor();
    this.dispatchStreamSignal("reset", {
      reason,
      previous_stream_epoch: previous.stream_epoch,
      previous_sequence: previous.sequence,
      stream_epoch: streamEpoch,
    });
  }

  private handleStreamControl(data: Record<string, unknown>): boolean {
    if (data.type !== "run_stream_ready" && data.type !== "run_stream_reset") return false;
    const epoch = asEpoch(data.stream_epoch);
    const serverSequence = asSequence(data.latest_sequence) ?? asSequence(data.sequence);
    if (data.type === "run_stream_reset") {
      this.resetCursor(epoch, typeof data.reason === "string" ? data.reason : "server_reset");
      this.restartStream();
      return true;
    }
    if (!this.cursor.stream_epoch && epoch) {
      // The first epoch-aware connection starts at zero, so adopting the epoch
      // cannot skip events left behind by a legacy sequence-only cursor.
      this.cursor = { stream_epoch: epoch, sequence: 0 };
      this.persistCursor();
      return true;
    }
    if (epoch && this.cursor.stream_epoch && epoch !== this.cursor.stream_epoch) {
      this.resetCursor(epoch, "epoch_changed");
      this.restartStream();
      return true;
    }
    if (serverSequence !== null && serverSequence < this.cursor.sequence) {
      this.resetCursor(epoch ?? this.cursor.stream_epoch, "sequence_rewound");
      this.restartStream();
    }
    return true;
  }

  private handleRunEvent(event: RunEvent): void {
    const eventEpoch = event.stream_epoch ?? null;
    if (!this.cursor.stream_epoch && eventEpoch) {
      this.cursor = { stream_epoch: eventEpoch, sequence: 0 };
      this.persistCursor();
    } else if (eventEpoch && this.cursor.stream_epoch && eventEpoch !== this.cursor.stream_epoch) {
      this.resetCursor(eventEpoch, "event_epoch_changed");
      this.restartStream();
      return;
    }

    if (event.sequence <= this.cursor.sequence) return;
    if (event.sequence > this.cursor.sequence + 1 && !this.replayingFromStart) {
      const confirmedServerGap = this.pendingGap
        && this.pendingGap.streamEpoch === this.cursor.stream_epoch
        && this.pendingGap.afterSequence === this.cursor.sequence
        && this.pendingGap.receivedSequence === event.sequence;
      if (confirmedServerGap) {
        // Seeing the same first event after reconnect proves that the missing
        // sequence is no longer retained, rather than lost in transit.
        this.pendingGap = null;
      } else {
        const expectedSequence = this.cursor.sequence + 1;
        this.pendingGap = {
          streamEpoch: this.cursor.stream_epoch,
          afterSequence: this.cursor.sequence,
          receivedSequence: event.sequence,
        };
        this.dispatchStreamSignal("gap", {
          stream_epoch: this.cursor.stream_epoch,
          expected_sequence: expectedSequence,
          received_sequence: event.sequence,
        });
        this.restartStream();
        return;
      }
    } else {
      this.pendingGap = null;
    }

    // During a replay, a first sequence greater than one means the server has
    // already pruned older terminal events. Accept that new floor once instead
    // of entering a permanent reconnect loop.
    this.replayingFromStart = false;
    this.cursor.sequence = event.sequence;
    this.persistCursor();
    if (!this.rememberEvent(event.event_id)) return;
    this.listeners.forEach((listener) => listener(event));
    window.dispatchEvent(new CustomEvent(`ambient_run_event:${event.run_id}`, { detail: event }));
    if (event.type === "run_created") {
      const payload = asRecord(event.payload);
      const correlation = asRecord(payload?.correlation);
      if (correlation && this.correlationEventName(correlation)) {
        this.correlatedRuns.add(event.run_id);
      }
    }
    // A terminal Run may complete while /ws/chat or the backend process is
    // reconnecting. Rebuild legacy call responses from durable correlation
    // instead of relying on the process-local completion callback.
    if (
      this.correlatedRuns.has(event.run_id)
      && ["run_created", "step_committed", "status_changed"].includes(event.type)
    ) {
      void this.projectCorrelatedCompletion(event.run_id);
    }
  }

  connect(): void {
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    if (!this.cursor.stream_epoch && this.cursor.sequence > 0) {
      // A sequence-only cursor cannot prove that the server still owns the
      // same database. Replay once from zero so a reset sequence cannot remain
      // permanently hidden behind a larger legacy cursor.
      this.cursor.sequence = 0;
      this.replayingFromStart = true;
      this.persistCursor();
    }
    const params = new URLSearchParams({
      // A legacy sequence without an epoch is ambiguous after a database reset.
      // Start from zero once; the server's epoch-aware ready frame then anchors it.
      after_sequence: String(this.cursor.sequence),
    });
    if (this.cursor.stream_epoch) params.set("stream_epoch", this.cursor.stream_epoch);
    const socket = new WebSocket(
      `${scheme}://${window.location.hostname}:8000/ws/runs?${params}`
    );
    this.socket = socket;
    socket.onmessage = (message) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(message.data);
      } catch {
        return;
      }
      const data = asRecord(parsed);
      if (!data || this.handleStreamControl(data)) return;
      // Support the original {type: "run_event", event: ...} frame and a
      // future direct versioned envelope. Unknown event types and schema
      // versions remain opaque but are safe to deliver to refresh listeners.
      const candidate = data.type === "run_event" ? data.event : data;
      const event = normalizeRunEvent(candidate);
      if (event) this.handleRunEvent(event);
    };
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      this.scheduleReconnect();
    };
  }

  subscribe(listener: (event: RunEvent) => void): () => void {
    this.listeners.add(listener);
    this.connect();
    return () => this.listeners.delete(listener);
  }

  async list(params: { status?: string; owner_id?: string; limit?: number } = {}): Promise<AmbientRun[]> {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.owner_id) query.set("owner_id", params.owner_id);
    if (params.limit) query.set("limit", String(params.limit));
    const response = await fetch(`${API_BASE}/api/runs?${query}`);
    if (!response.ok) throw new Error(`Unable to list runs: HTTP ${response.status}`);
    const payload = await response.json();
    return Array.isArray(payload) ? payload : [];
  }

  async get(runId: string): Promise<AmbientRun> {
    const response = await fetch(`${API_BASE}/api/runs/${runId}`);
    if (!response.ok) throw new Error(`Run not found: HTTP ${response.status}`);
    return response.json();
  }

  async start(catalogId: string, actionId: string | undefined, input: unknown): Promise<AmbientRun> {
    const response = await fetch(`${API_BASE}/api/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        catalog_id: catalogId,
        action_id: actionId,
        input,
        source: { type: "app", id: catalogId },
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Unable to start Run: HTTP ${response.status}`);
    return payload;
  }

  async cancel(runId: string): Promise<AmbientRun> {
    const response = await fetch(`${API_BASE}/api/runs/${runId}/cancel`, { method: "POST" });
    if (!response.ok) throw new Error(`Unable to cancel Run: HTTP ${response.status}`);
    return response.json();
  }

  async retry(runId: string): Promise<AmbientRun> {
    const response = await fetch(`${API_BASE}/api/runs/${runId}/retry`, { method: "POST" });
    if (!response.ok) throw new Error(`Unable to retry Run: HTTP ${response.status}`);
    return response.json();
  }

  async reconcile(
    runId: string,
    resolution: EffectReconciliation,
    note?: string,
  ): Promise<AmbientRun> {
    const response = await fetch(`${API_BASE}/api/runs/${runId}/reconcile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolution, ...(note ? { note } : {}) }),
    });
    const payload = await response.json().catch(() => null) as { detail?: string } | AmbientRun | null;
    if (!response.ok) {
      const detail = payload && "detail" in payload ? payload.detail : undefined;
      throw new Error(detail || `Unable to reconcile Run: HTTP ${response.status}`);
    }
    return payload as AmbientRun;
  }

  async resolve(interactionId: string, responseData: unknown): Promise<AmbientRun> {
    const response = await fetch(`${API_BASE}/api/run-interactions/${interactionId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ response: responseData }),
    });
    if (!response.ok) throw new Error(`Unable to resolve interaction: HTTP ${response.status}`);
    return response.json();
  }

  async runtimes(): Promise<RuntimeSnapshot[]> {
    const response = await fetch(`${API_BASE}/api/runtimes`);
    if (!response.ok) throw new Error(`Unable to list runtimes: HTTP ${response.status}`);
    const payload = await response.json();
    return Array.isArray(payload) ? payload : [];
  }

  async stopRuntime(runtimeId: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/runtimes/${encodeURIComponent(runtimeId)}/stop`, {
      method: "POST",
    });
    if (!response.ok) throw new Error(`Unable to stop runtime: HTTP ${response.status}`);
  }

  wait(runId: string): Promise<unknown> {
    return new Promise((resolve, reject) => {
      let disposed = false;
      const check = async () => {
        try {
          const run = await this.get(runId);
          if (run.status === "succeeded") {
            disposed = true;
            cleanup();
            resolve(run.result);
          } else if (["failed", "cancelled", "needs_attention"].includes(run.status)) {
            disposed = true;
            cleanup();
            reject(new Error(run.error?.message || run.summary || run.status));
          }
        } catch (error) {
          disposed = true;
          cleanup();
          reject(error);
        }
      };
      const handler = () => { if (!disposed) void check(); };
      const cleanup = () => window.removeEventListener(`ambient_run_event:${runId}`, handler);
      window.addEventListener(`ambient_run_event:${runId}`, handler);
      this.connect();
      void check();
    });
  }
}

export const runService = new RunService();
