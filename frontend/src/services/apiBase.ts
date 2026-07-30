export interface BrowserLocation {
  protocol: string;
  hostname: string;
  origin: string;
}

const withoutTrailingSlashes = (value: string) => value.replace(/\/+$/, "");

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
