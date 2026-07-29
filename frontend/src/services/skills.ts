export type SkillSurface = "agent_context";

export type SkillAuthorizationState =
  | "trusted"
  | "quarantined"
  | "authorized";

export type SkillActivationPolicy =
  | "none"
  | "explicit_only"
  | "implicit";

export interface SkillAuthorization {
  state: SkillAuthorizationState;
  activation_policy: SkillActivationPolicy;
  authorized_digest?: string | null;
  requires_reauthorization?: boolean;
  principal_id?: string | null;
  grant_digest?: string | null;
}

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
  authorization?: SkillAuthorization;
}

export interface SkillMarket {
  version: 1;
  revision?: number;
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

export function installSkill(
  apiBase: string,
  marketId: string,
  expectedRevision?: number,
) {
  return jsonRequest<Record<string, unknown>>(`${apiBase}/api/skills/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      market_id: marketId,
      ...(expectedRevision === undefined
        ? {}
        : { expected_revision: expectedRevision }),
    }),
  });
}

export function setSkillEnabled(
  apiBase: string,
  catalogId: string,
  enabled: boolean,
  expectedRevision?: number,
) {
  return jsonRequest<Record<string, unknown>>(
    `${apiBase}/api/skills/${encodeURIComponent(catalogId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled,
        ...(expectedRevision === undefined
          ? {}
          : { expected_revision: expectedRevision }),
      }),
    },
  );
}

export function setSkillAuthorization(
  apiBase: string,
  catalogId: string,
  activationPolicy: SkillActivationPolicy,
  expectedDigest: string,
  expectedRevision?: number,
) {
  return jsonRequest<Record<string, unknown>>(
    `${apiBase}/api/skills/${encodeURIComponent(catalogId)}/authorization`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        activation_policy: activationPolicy,
        expected_digest: expectedDigest,
        ...(expectedRevision === undefined
          ? {}
          : { expected_revision: expectedRevision }),
      }),
    },
  );
}

export function uninstallSkill(
  apiBase: string,
  catalogId: string,
  expectedRevision?: number,
) {
  const query = expectedRevision === undefined
    ? ""
    : `?expected_revision=${encodeURIComponent(String(expectedRevision))}`;
  return jsonRequest<Record<string, unknown>>(
    `${apiBase}/api/skills/${encodeURIComponent(catalogId)}${query}`,
    { method: "DELETE" },
  );
}
