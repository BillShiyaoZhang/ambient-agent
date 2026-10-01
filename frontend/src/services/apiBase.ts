export interface BrowserLocation {
  protocol: string;
  hostname: string;
  origin: string;
}

const withoutTrailingSlashes = (value: string) => value.replace(/\/+$/, "");

export interface RemoteWorkspaceContext {
  apiBaseUrl: string;
  nodeId: string;
}

declare global {
  interface Window {
    __AMBIENT_REMOTE__?: RemoteWorkspaceContext;
  }
}

export function getRemoteWorkspaceContext(): RemoteWorkspaceContext | null {
  const context = window.__AMBIENT_REMOTE__;
  if (context === undefined) return null;
  if (context.apiBaseUrl !== "/" || !/^[a-zA-Z0-9-]{1,80}$/.test(context.nodeId)) {
    throw new Error("Invalid remote workspace routing configuration");
  }
  return context;
}

export function resolveApiBaseUrl(
  configured: string | undefined,
  location: BrowserLocation = window.location,
): string {
  const candidate = configured?.trim();
  const url = candidate
    ? new URL(candidate, location.origin)
    : new URL(`${location.protocol}//${location.hostname}:8000`);

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("VITE_API_BASE_URL must use an HTTP or HTTPS URL");
  }

  url.search = "";
  url.hash = "";
  return withoutTrailingSlashes(url.toString());
}

export function getApiBaseUrl(): string {
  if (getRemoteWorkspaceContext()) return withoutTrailingSlashes(window.location.origin);
  return resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL, window.location);
}

export function apiUrl(path: string, base = getApiBaseUrl()): string {
  return new URL(
    path.replace(/^\/+/, ""),
    `${withoutTrailingSlashes(base)}/`,
  ).toString();
}

export function webSocketUrl(path: string, base = getApiBaseUrl()): string {
  const url = new URL(
    path.replace(/^\/+/, ""),
    `${withoutTrailingSlashes(base)}/`,
  );
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
