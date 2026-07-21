export type CodingAgentId = "opencode" | "codex";
export type AgentModelMode = "native" | "shared_binding" | "hybrid" | "none";

export interface AgentModelConfig {
  mode: AgentModelMode;
  inherit?: "ambient.primary" | null;
  provider_id?: string | null;
  model_id?: string | null;
  native_model?: string | null;
}

export interface CodingAgentInstallOperation {
  id: string;
  agent_id: CodingAgentId;
  status: "installing" | "installed" | "failed";
  created_at: number;
  error: string;
}

export interface CodingAgentAuthSession {
  id: string;
  agent_id: CodingAgentId;
  status: "signed_out" | "starting" | "waiting" | "signed_in" | "failed" | "cancelled" | "expired";
  method: string;
  verification_uri: string;
  user_code: string;
  expires_at: string | null;
  error: string;
}

export interface CodingAgentDefinition {
  id: CodingAgentId;
  name: string;
  description: string;
  auth_hint: string;
  auth_mode: "run_model" | "codex_native";
  auth_methods: string[];
  uses_run_model: boolean;
  available: boolean;
  installed: boolean;
  installable: boolean;
  install_state: "not_installed" | "installing" | "installed" | "failed";
  install_operation: CodingAgentInstallOperation | null;
  command_env: string;
  execution_target: "container";
  authenticated: boolean | null;
  auth_state: "not_required" | "signed_out" | "starting" | "waiting" | "signed_in" | "failed" | "cancelled" | "expired";
  version: string;
  status_detail: string;
  model_capability: {
    modes: AgentModelMode[];
    default_mode: AgentModelMode;
    selection: "none" | "optional" | "required";
    catalog_source: "none" | "agent" | "provider_registry";
    supports_inherit: boolean;
  };
  model_config: AgentModelConfig;
}

export interface CodingAgentSettings {
  default_agent: CodingAgentId;
  agent_models: Record<string, AgentModelConfig>;
}

export interface CodingAgentNativeModel {
  id: string;
  model: string;
  display_name: string;
  description: string;
  is_default: boolean;
  default_reasoning_effort: string;
  supported_reasoning_efforts: string[];
}

export interface CodingAgentModelCatalog {
  agent_id: CodingAgentId;
  source?: "agent";
  default_model: string | null;
  models: CodingAgentNativeModel[];
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

export function installCodingAgent(apiBase: string, agentId: CodingAgentId) {
  return jsonRequest<CodingAgentInstallOperation>(`${apiBase}/api/coding-agents/${agentId}/install`, { method: "POST" });
}

export function startCodingAgentAuth(apiBase: string, agentId: CodingAgentId, method = "device_code") {
  return jsonRequest<CodingAgentAuthSession>(`${apiBase}/api/coding-agents/${agentId}/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method }),
  });
}

export function getCodingAgentAuth(apiBase: string, agentId: CodingAgentId) {
  return jsonRequest<CodingAgentAuthSession>(`${apiBase}/api/coding-agents/${agentId}/auth`);
}

export function listCodingAgentModels(apiBase: string, agentId: CodingAgentId) {
  return jsonRequest<CodingAgentModelCatalog>(`${apiBase}/api/coding-agents/${agentId}/models`);
}

export function clearCodingAgentAuth(apiBase: string, agentId: CodingAgentId) {
  return jsonRequest<CodingAgentAuthSession>(`${apiBase}/api/coding-agents/${agentId}/auth`, { method: "DELETE" });
}

export function updateCodingAgentModel(apiBase: string, agentId: CodingAgentId, config: AgentModelConfig) {
  return jsonRequest<AgentModelConfig>(`${apiBase}/api/coding-agents/${agentId}/model`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}
