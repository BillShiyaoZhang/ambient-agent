export interface ModelSelection {
  provider_id: string;
  model_id: string;
}

export interface ModelCapabilities {
  tool_calling?: boolean | null;
  vision?: boolean | null;
  reasoning?: boolean | null;
  context_window?: number | null;
  verification?: "unknown" | "verified" | "unsupported";
}

export interface LLMModel {
  id: string;
  display_name?: string | null;
  api_mode?: "chat_completions" | "responses" | null;
  capabilities?: ModelCapabilities;
  source?: "manual" | "discovered" | "catalog";
}

export interface CredentialStatus {
  source: "stored" | "env";
  configured: boolean;
  env_var?: string | null;
  masked?: string | null;
}

export interface LLMProvider {
  id: string;
  name: string;
  preset: string;
  enabled: boolean;
  connection: Record<string, unknown>;
  credentials?: Record<string, CredentialStatus>;
  credential_refs?: Record<string, { source: "stored" | "env"; env_var?: string | null }>;
  models: LLMModel[];
}

export interface CatalogField {
  id: string;
  label: string;
  secret?: boolean;
  required?: boolean;
  kind?: string;
}

export interface ProviderPreset {
  id: string;
  name: string;
  category: string;
  fields: CatalogField[];
  advanced_fields?: CatalogField[];
  default_base_url?: string | null;
  api_mode?: "chat_completions" | "responses";
}

export interface LLMSettings {
  default_model: ModelSelection | null;
  fast_model: ModelSelection | null;
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

export async function loadLLMConfiguration(apiBase: string) {
  const [rawCatalog, rawProviders, rawSettings] = await Promise.all([
    jsonRequest<ProviderPreset[]>(`${apiBase}/api/llm/catalog`),
    jsonRequest<LLMProvider[]>(`${apiBase}/api/llm/providers`),
    jsonRequest<LLMSettings>(`${apiBase}/api/llm/settings`),
  ]);
  const catalog = Array.isArray(rawCatalog) ? rawCatalog : [];
  const providers = Array.isArray(rawProviders) ? rawProviders : [];
  const settings = rawSettings && typeof rawSettings === "object" && "default_model" in rawSettings
    ? rawSettings
    : { default_model: null, fast_model: null };
  return { catalog, providers, settings };
}

export function createProvider(apiBase: string, profile: Record<string, unknown>, credentials: Record<string, unknown>) {
  return jsonRequest<LLMProvider>(`${apiBase}/api/llm/providers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, credentials }),
  });
}

export function updateProvider(apiBase: string, providerId: string, profile: Record<string, unknown>, credentials?: Record<string, unknown>) {
  return jsonRequest<LLMProvider>(`${apiBase}/api/llm/providers/${providerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, ...(credentials === undefined ? {} : { credentials }) }),
  });
}

export function deleteProvider(apiBase: string, providerId: string) {
  return jsonRequest<{ status: string }>(`${apiBase}/api/llm/providers/${providerId}`, { method: "DELETE" });
}

export function discoverProviderModels(apiBase: string, providerId: string) {
  return jsonRequest<{ models: LLMModel[] }>(`${apiBase}/api/llm/providers/${providerId}/discover-models`, { method: "POST" });
}

export async function testProviderConnection(apiBase: string, providerId: string, modelId?: string, mode: "connection" | "tools" = "connection") {
  const result = await jsonRequest<{ ok: boolean; model_id: string; message?: string }>(`${apiBase}/api/llm/providers/${providerId}/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId ?? null, mode }),
  });
  if (!result.ok) throw new Error(result.message || "Model capability test failed");
  return result;
}

export function updateLLMSettings(apiBase: string, patch: Partial<LLMSettings>) {
  return jsonRequest<LLMSettings>(`${apiBase}/api/llm/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export function updateSessionModel(apiBase: string, sessionId: string, selection: ModelSelection) {
  return jsonRequest<{ model_selection: ModelSelection }>(`${apiBase}/api/sessions/${sessionId}/model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(selection),
  });
}
