import { afterEach, describe, expect, it, vi } from "vitest";
import * as llm from "../../frontend/src/services/llm";

afterEach(() => vi.unstubAllGlobals());

describe("Codex connection API", () => {
  it("posts without credentials or a request body and returns the public provider", async () => {
    const provider = { id: "ambient-codex", name: "Codex", preset: "codex_native", enabled: true, connection: {}, credential_refs: {}, models: [{ id: "gpt-native" }] };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => provider });
    vi.stubGlobal("fetch", fetchMock);
    expect(typeof llm.syncCodexConnection).toBe("function");
    await expect(llm.syncCodexConnection("https://gateway.example/ambient")).resolves.toEqual(provider);
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith("https://gateway.example/ambient/api/llm/connections/codex/sync", { method: "POST" });
  });

  it("preserves the supported-profile error so the settings UI can explain a retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: { code: "llm_capability_unsupported", message: "Native Codex requires the supported Linux profile" } }) }));
    expect(typeof llm.syncCodexConnection).toBe("function");
    await expect(llm.syncCodexConnection("")).rejects.toThrow("Native Codex requires the supported Linux profile");
  });
});
