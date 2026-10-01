import { describe, expect, it } from "vitest";
import { localizedLLMError } from "../../frontend/src/services/llmErrors";

describe("model failure messages", () => {
  it("does not attribute a native runtime rejection to missing tool support", () => {
    expect(localizedLLMError("llm_capability_unsupported", "en"))
      .toBe("The selected model or runtime configuration does not support this agent request.");
    expect(localizedLLMError("llm_capability_unsupported", "zh"))
      .toBe("所选模型或运行配置不支持这次 Agent 请求。");
  });

  it("preserves API provider authentication guidance and redacts unknown error codes", () => {
    expect(localizedLLMError("llm_auth_failed", "en"))
      .toBe("Provider authentication failed. Check the credentials.");
    expect(localizedLLMError("private-server-error", "zh"))
      .toBe("模型请求失败，请检查 Provider 设置。");
    expect(localizedLLMError("private-server-error", "en"))
      .toBe("The model request failed. Check provider settings.");
  });
});
