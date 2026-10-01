import { apiUrl } from "./apiBase";

export const UNTIL_REVOKED_EXPIRY = "9999-01-01T00:00:00Z";

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
  retry_after?: number;
}

export interface RemoteWorkspacePair {
  portal_url: string;
  gateway_url: string;
  name: string;
  scopes: string[];
  expires_in: number;
  until_revoked?: boolean;
  enrollment_token: string;
}

export class RemoteWorkspaceRequestError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;
  constructor(status: number, retryAfter: number | null = null) {
    super(`Remote connection request failed (${status})`);
    this.name = "RemoteWorkspaceRequestError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function retryAfter(response: Response): number | null {
  const value = response.headers.get("Retry-After")?.trim();
  if (!value || value.length > 128) return null;
  const seconds = /^\d+$/.test(value) ? Number(value) : Math.ceil((Date.parse(value) - Date.now()) / 1000);
  return Number.isSafeInteger(seconds) && seconds >= 1 && seconds <= 86400 ? seconds : null;
}

async function request(path: string, body?: unknown): Promise<RemoteWorkspaceStatus> {
  const response = await fetch(apiUrl(`/api/remote-workspace/${path}`), body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    // Error bodies may echo enrollment input. Only expose the bounded HTTP contract.
    throw new RemoteWorkspaceRequestError(response.status, response.status === 429 ? retryAfter(response) : null);
  }
  return response.json();
}

export const loadRemoteWorkspace = () => request("status");
export const pairRemoteWorkspace = (body: RemoteWorkspacePair) => request("pair", body);
export const approveRemoteWorkspace = (body: { account_id: string; grant_id: string }) => request("approve", body);
export const revokeRemoteWorkspace = () => request("revoke", {});
