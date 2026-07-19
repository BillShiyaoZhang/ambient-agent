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
  error?: { message?: string; type?: string } | null;
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

export interface RunEvent {
  sequence: number;
  run_id: string;
  type: string;
  payload: unknown;
  created_at: string;
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

class RunService {
  private socket: WebSocket | null = null;
  private listeners = new Set<(event: RunEvent) => void>();
  private sequence = Number(sessionStorage.getItem("ambient_run_sequence") || "0");
  private reconnectTimer: number | null = null;

  connect(): void {
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(
      `${scheme}://${window.location.hostname}:8000/ws/runs?after_sequence=${this.sequence}`
    );
    this.socket.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.type !== "run_event") return;
      const event = data.event as RunEvent;
      if (event.sequence <= this.sequence) return;
      this.sequence = event.sequence;
      sessionStorage.setItem("ambient_run_sequence", String(this.sequence));
      this.listeners.forEach((listener) => listener(event));
      window.dispatchEvent(new CustomEvent(`ambient_run_event:${event.run_id}`, { detail: event }));
    };
    this.socket.onclose = () => {
      this.socket = null;
      if (this.listeners.size > 0 && this.reconnectTimer === null) {
        this.reconnectTimer = window.setTimeout(() => {
          this.reconnectTimer = null;
          this.connect();
        }, 1000);
      }
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
