import { apiUrl } from "./apiBase";

export interface RemoteWorkspaceStatus {
  status: string;
  online: boolean;
  scopes: string[];
  node_id?: string | null;
  name?: string | null;
  gateway_url?: string | null;
  portal_url?: string | null;
  account_id?: string | null;
  account_label?: string | null;
  grant_id?: string | null;
  expires_at?: string | null;
  pairing_code?: string | null;
  pairing_expires_at?: string | null;
  workspace_origin?: string | null;
  last_error?: string | null;
}

export interface RemoteWorkspacePair {
  portal_url: string;
  gateway_url: string;
  name: string;
  scopes: string[];
  expires_in: number;
}

async function request(path: string, body?: unknown): Promise<RemoteWorkspaceStatus> {
  const response = await fetch(apiUrl(`/api/remote-workspace/${path}`), body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message ?? `Remote connection request failed (${response.status})`);
  }
  return response.json();
}

export const loadRemoteWorkspace = () => request("status");
export const pairRemoteWorkspace = (body: RemoteWorkspacePair) => request("pair", body);
export const approveRemoteWorkspace = (body: { account_id: string; grant_id: string }) => request("approve", body);
export const revokeRemoteWorkspace = () => request("revoke", {});
