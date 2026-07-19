export type CodingAgentId = "opencode" | "codex";

export interface CodingAgentDefinition {
  id: CodingAgentId;
  name: string;
  description: string;
  auth_hint: string;
  auth_mode: "run_model" | "codex_native";
  uses_run_model: boolean;
  available: boolean;
  command_env: string;
  execution_target: "host" | "container";
  authenticated: boolean | null;
  version: string;
  status_detail: string;
}

export interface CodingAgentSettings {
  default_agent: CodingAgentId;
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail ?? body;
    throw new Error(detail?.message ?? detail?.code ?? `Request failed (${response.status})`);
  }
  return body as T;
}

export function loadCodingAgentConfiguration(apiBase: string) {
  return jsonRequest<{ agents: CodingAgentDefinition[]; settings: CodingAgentSettings }>(`${apiBase}/api/coding-agents`);
}

export function updateCodingAgentSettings(apiBase: string, patch: Partial<CodingAgentSettings>) {
  return jsonRequest<CodingAgentSettings>(`${apiBase}/api/coding-agents/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}
