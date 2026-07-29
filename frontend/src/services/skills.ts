export type SkillSurface = "agent_context";

export type SkillInstallState =
  | "not_installed"
  | "installed"
  | "update_available"
  | "market_older"
  | "integrity_conflict";

export interface SkillProvenance {
  source: string;
  digest: string;
  verified: boolean;
  trust?: "bundled" | "local";
}

export interface MarketSkill {
  market_id: string;
  catalog_id: string;
  name: string;
  title: string;
  description: string;
  version: string;
  provider: string;
  tags: string[];
  icon?: string | null;
  accent?: string | null;
  license?: string | null;
  compatibility?: string | null;
  ontology_refs: string[];
  surfaces: SkillSurface[];
  provenance: SkillProvenance;
  install_state: SkillInstallState;
  installed_version?: string | null;
  enabled?: boolean;
}

export interface SkillMarket {
  version: 1;
  items: MarketSkill[];
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail ?? body;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message ?? detail?.code ?? `Request failed (${response.status})`
    );
  }
  return body as T;
}

export async function loadSkillMarket(apiBase: string): Promise<SkillMarket> {
  const result = await jsonRequest<SkillMarket>(`${apiBase}/api/skill-market`);
  if (result?.version !== 1 || !Array.isArray(result.items)) {
    throw new Error("Invalid skill market response");
  }
  return result;
}

export function installSkill(apiBase: string, marketId: string) {
  return jsonRequest<Record<string, unknown>>(`${apiBase}/api/skills/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ market_id: marketId }),
  });
}

export function setSkillEnabled(
  apiBase: string,
  catalogId: string,
  enabled: boolean,
) {
  return jsonRequest<Record<string, unknown>>(
    `${apiBase}/api/skills/${encodeURIComponent(catalogId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
}

export function uninstallSkill(apiBase: string, catalogId: string) {
  return jsonRequest<Record<string, unknown>>(
    `${apiBase}/api/skills/${encodeURIComponent(catalogId)}`,
    { method: "DELETE" },
  );
}
