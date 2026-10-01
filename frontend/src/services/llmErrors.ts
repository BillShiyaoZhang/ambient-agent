export function localizedLLMError(code: string, language: "zh" | "en"): string {
  const messages: Record<string, [string, string]> = {
    llm_configuration_required: ["请先在“模型与 Provider”中完成配置。", "Configure a model and provider before sending a request."],
    llm_auth_failed: ["Provider 鉴权失败，请检查凭据。", "Provider authentication failed. Check the credentials."],
    llm_rate_limited: ["Provider 已限流，请稍后重试。", "The provider rate limit was reached. Try again later."],
    llm_timeout: ["模型请求超时，请检查端点或超时设置。", "The model request timed out. Check the endpoint or timeout setting."],
    llm_model_not_found: ["Provider 找不到所选模型。", "The selected model was not found by the provider."],
    llm_capability_unsupported: ["所选模型或运行配置不支持这次 Agent 请求。", "The selected model or runtime configuration does not support this agent request."],
  };
  const pair = messages[code] ?? ["模型请求失败，请检查 Provider 设置。", "The model request failed. Check provider settings."];
  return pair[language === "zh" ? 0 : 1];
}
