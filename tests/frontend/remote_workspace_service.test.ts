import { afterEach, describe, expect, it, vi } from "vitest";
import { pairRemoteWorkspace } from "../../frontend/src/services/remoteWorkspace";

const pair = { gateway_url: "http://localhost:8788", portal_url: "http://localhost:3001", name: "Test computer",
  scopes: ["workspace.control"], expires_in: 3600, enrollment_token: "synthetic-enrollment-token-one" };

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("remote workspace error boundary", () => {
  it.each([409, 410, 422, 429, 502])("keeps %s errors bounded and does not echo server input", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: pair.enrollment_token.repeat(1000) }), { status })));
    const error = await pairRemoteWorkspace(pair).catch((reason: unknown) => reason) as Error & { status: number };
    expect(error.status).toBe(status);
    expect(error.message.length).toBeLessThan(240);
    expect(String(error)).not.toContain(pair.enrollment_token);
  });

  it("preserves Retry-After seconds without replaying the POST", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("{}", { status: 429, headers: { "Retry-After": "12" } }));
    vi.stubGlobal("fetch", fetcher);
    await expect(pairRemoteWorkspace(pair)).rejects.toMatchObject({ status: 429, retryAfter: 12 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body).enrollment_token).toBe(pair.enrollment_token);
  });

  it("accepts a bounded future HTTP-date Retry-After", async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 429, headers: { "Retry-After": "Thu, 01 Oct 2026 12:00:10 GMT" } })));
    await expect(pairRemoteWorkspace(pair)).rejects.toMatchObject({ status: 429, retryAfter: 10 });
  });
});
