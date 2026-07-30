import { describe, expect, it } from "vitest";

import {
  apiUrl,
  resolveApiBaseUrl,
  webSocketUrl,
} from "../../frontend/src/services/apiBase";

const httpsLocation = {
  protocol: "https:",
  hostname: "ambient.example",
  origin: "https://ambient.example",
};

describe("frontend API base resolution", () => {
  it("keeps the page protocol for the default backend port", () => {
    expect(resolveApiBaseUrl("", httpsLocation)).toBe(
      "https://ambient.example:8000",
    );
  });

  it("preserves an absolute configured path prefix for HTTP and WebSocket traffic", () => {
    const base = resolveApiBaseUrl(
      "https://gateway.example/ambient/runtime/",
      httpsLocation,
    );

    expect(apiUrl("/api/sessions?limit=10", base)).toBe(
      "https://gateway.example/ambient/runtime/api/sessions?limit=10",
    );
    expect(webSocketUrl("/ws/chat?projection=commands_only", base)).toBe(
      "wss://gateway.example/ambient/runtime/ws/chat?projection=commands_only",
    );
  });

  it("resolves a relative configured base against the frontend origin", () => {
    const base = resolveApiBaseUrl("/ambient/", httpsLocation);

    expect(base).toBe("https://ambient.example/ambient");
    expect(apiUrl("api/runs", base)).toBe(
      "https://ambient.example/ambient/api/runs",
    );
  });

  it("rejects a configured non-HTTP transport", () => {
    expect(() => resolveApiBaseUrl("ftp://ambient.example", httpsLocation))
      .toThrow(/HTTP/);
  });
});
