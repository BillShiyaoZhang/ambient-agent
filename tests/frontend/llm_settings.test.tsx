import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LLMSettingsDialog, ModelPicker } from "../../frontend/src/components/LLMSettings";
import type { CodingAgentDefinition, CodingAgentModelCatalog, CodingAgentSettings } from "../../frontend/src/services/codingAgents";

const providers = [{
  id: "openai-main",
  name: "OpenAI Main",
  preset: "openai",
  enabled: true,
  connection: {},
  credentials: { api_key: { source: "stored", configured: true, masked: "••••cret" } },
  models: [
    { id: "gpt-a", display_name: "GPT A", capabilities: { tool_calling: true } },
    { id: "gpt-b", display_name: "GPT B", capabilities: { tool_calling: false } },
  ],
}];

const codingSettings: CodingAgentSettings = {
  default_agent: "opencode",
  agent_models: {
    opencode: { mode: "shared_binding", inherit: "ambient.primary" },
    codex: { mode: "native" },
  },
};

const opencodeAgent: CodingAgentDefinition = {
  id: "opencode", name: "OpenCode", description: "ACP agent", auth_hint: "Uses provider credentials.", auth_mode: "run_model", auth_methods: [], uses_run_model: true,
  available: false, installed: false, installable: false, install_state: "not_installed", install_operation: null, command_env: "OPENCODE_COMMAND", execution_target: "container",
  authenticated: null, auth_state: "not_required", version: "", status_detail: "",
  model_capability: { modes: ["shared_binding"], default_mode: "shared_binding", selection: "required", catalog_source: "provider_registry", supports_inherit: true },
  model_config: { mode: "shared_binding", inherit: "ambient.primary" },
};

const codexAgent: CodingAgentDefinition = {
  id: "codex", name: "Codex", description: "Managed Codex agent", auth_hint: "Uses its own subscription.", auth_mode: "codex_native", auth_methods: ["device_code"], uses_run_model: false,
  available: true, installed: true, installable: true, install_state: "installed", install_operation: null, command_env: "CODEX_COMMAND", execution_target: "container",
  authenticated: true, auth_state: "signed_in", version: "codex-cli 1.0", status_detail: "Logged in",
  model_capability: { modes: ["native"], default_mode: "native", selection: "optional", catalog_source: "agent", supports_inherit: false },
  model_config: { mode: "native" },
};

const codexModels: CodingAgentModelCatalog = {
  agent_id: "codex",
  default_model: "gpt-default",
  models: [
    { id: "gpt-default", model: "gpt-default", display_name: "GPT Default", description: "Default model", is_default: true, default_reasoning_effort: "medium", supported_reasoning_efforts: ["low", "medium"] },
    { id: "gpt-fast", model: "gpt-fast", display_name: "GPT Fast", description: "Fast model", is_default: false, default_reasoning_effort: "low", supported_reasoning_efforts: ["low"] },
  ],
};

describe("LLM provider settings", () => {
  it("groups models by provider and warns for models without verified tool use", () => {
    const select = vi.fn();
    render(<ModelPicker
      providers={providers}
      value={{ provider_id: "openai-main", model_id: "gpt-a" }}
      onChange={select}
      language="en"
    />);

    fireEvent.click(screen.getByRole("button", { name: "GPT A" }));
    expect(screen.getByText("OpenAI Main")).toBeDefined();
    expect(screen.getByText("GPT B")).toBeDefined();
    expect(screen.getByText("Tool use not verified")).toBeDefined();
    fireEvent.click(screen.getByText("GPT B"));
    expect(select).toHaveBeenCalledWith({ provider_id: "openai-main", model_id: "gpt-b" });
  });

  it("shows masked credentials and never renders a secret value", () => {
    render(<LLMSettingsDialog
      open
      language="en"
      catalog={[{ id: "openai", name: "OpenAI", category: "global", fields: [] }]}
      providers={providers}
      settings={{ default_model: null, fast_model: null }}
      onClose={vi.fn()}
      onRefresh={vi.fn()}
    />);

    expect(screen.getByRole("dialog", { name: "Models & Providers" })).toBeDefined();
    expect(screen.getByText("••••cret")).toBeDefined();
    expect(screen.queryByText("sk-never-return")).toBeNull();
    expect(screen.getByRole("button", { name: "Add provider" })).toBeDefined();
  });

  it("stores entered credentials while omitting untouched optional secret fields", async () => {
    const create = vi.fn().mockResolvedValue({});
    render(<LLMSettingsDialog
      open
      language="en"
      catalog={[{
        id: "openai",
        name: "OpenAI",
        category: "global",
        fields: [{ id: "api_key", label: "API key", secret: true, required: true }],
        advanced_fields: [{ id: "secret_headers", label: "Secret headers", secret: true }],
      }]}
      providers={[]}
      settings={{ default_model: null, fast_model: null }}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onCreateProvider={create}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    fireEvent.change(screen.getByLabelText("API key *"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const [profile, credentials] = create.mock.calls[0];
    expect(profile.credential_refs).toEqual({ api_key: { source: "stored" } });
    expect(credentials).toEqual({ api_key: { source: "stored", value: "sk-test" } });
  });

  it("tests the provider default model instead of the first discovered model", async () => {
    const testProvider = vi.fn().mockResolvedValue({ ok: true });
    render(<LLMSettingsDialog
      open
      language="en"
      catalog={[{ id: "openai", name: "OpenAI", category: "global", fields: [] }]}
      providers={providers}
      settings={{ default_model: { provider_id: "openai-main", model_id: "gpt-b" }, fast_model: null }}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onTestProvider={testProvider}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Test" }));

    await waitFor(() => expect(testProvider).toHaveBeenCalledWith("openai-main", "gpt-b"));
  });

  it("lets users select an available coding agent and disables missing CLIs", async () => {
    const updateCodingAgent = vi.fn().mockResolvedValue({ default_agent: "codex" });
    render(<LLMSettingsDialog
      open
      language="en"
      catalog={[]}
      providers={providers}
      settings={{ default_model: null, fast_model: null }}
      codingAgents={[
        opencodeAgent,
        codexAgent,
      ]}
      codingAgentSettings={codingSettings}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onUpdateCodingAgent={updateCodingAgent}
    />);

    fireEvent.click(screen.getByRole("radio", { name: /^Codex/ }));
    await waitFor(() => expect(updateCodingAgent).toHaveBeenCalledWith({ default_agent: "codex" }));
    expect(screen.getByRole("radio", { name: /^OpenCode/ }).hasAttribute("disabled")).toBe(true);
  });

  it("installs Codex on demand and shows the device-code login flow", async () => {
    const install = vi.fn().mockResolvedValue({ status: "installing" });
    const startAuth = vi.fn().mockResolvedValue({
      id: "auth-1", agent_id: "codex", status: "waiting", method: "device_code",
      verification_uri: "https://auth.openai.com/codex/device", user_code: "ABCD-12345", expires_at: null, error: "",
    });
    const uninstalled = { ...codexAgent, available: false, installed: false, authenticated: false, auth_state: "signed_out" as const, install_state: "not_installed" as const, version: "" };
    const { rerender } = render(<LLMSettingsDialog
      open
      language="zh"
      catalog={[]}
      providers={providers}
      settings={{ default_model: null, fast_model: null }}
      codingAgents={[uninstalled]}
      codingAgentSettings={codingSettings}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onInstallCodingAgent={install}
    />);

    fireEvent.click(screen.getByRole("button", { name: "安装" }));
    await waitFor(() => expect(install).toHaveBeenCalledWith("codex"));

    rerender(<LLMSettingsDialog
      open
      language="zh"
      catalog={[]}
      providers={providers}
      settings={{ default_model: null, fast_model: null }}
      codingAgents={[{ ...codexAgent, authenticated: false, auth_state: "signed_out" }]}
      codingAgentSettings={codingSettings}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onStartCodingAgentAuth={startAuth}
    />);

    fireEvent.click(screen.getByRole("button", { name: "使用 ChatGPT 登录" }));
    await waitFor(() => expect(screen.getByText("ABCD-12345")).toBeTruthy());
    expect(screen.getByRole("link", { name: "打开登录页面" }).getAttribute("href")).toBe("https://auth.openai.com/codex/device");
  });

  it("updates an OpenCode-specific model binding", async () => {
    const updateModel = vi.fn().mockResolvedValue({});
    render(<LLMSettingsDialog
      open
      language="en"
      catalog={[]}
      providers={providers}
      settings={{ default_model: null, fast_model: null }}
      codingAgents={[{ ...opencodeAgent, available: true, installed: true, install_state: "installed" }]}
      codingAgentSettings={codingSettings}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onUpdateCodingAgentModel={updateModel}
    />);

    fireEvent.change(screen.getByRole("combobox", { name: "Execution model" }), { target: { value: "openai-main:gpt-a" } });
    await waitFor(() => expect(updateModel).toHaveBeenCalledWith("opencode", { mode: "shared_binding", provider_id: "openai-main", model_id: "gpt-a" }));
  });

  it("loads the signed-in Codex model catalog and selects a native model", async () => {
    const listModels = vi.fn().mockResolvedValue(codexModels);
    const updateModel = vi.fn().mockResolvedValue({});
    render(<LLMSettingsDialog
      open
      language="zh"
      catalog={[]}
      providers={providers}
      settings={{ default_model: null, fast_model: null }}
      codingAgents={[codexAgent]}
      codingAgentSettings={{ ...codingSettings, default_agent: "codex" }}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onListCodingAgentModels={listModels}
      onUpdateCodingAgentModel={updateModel}
    />);

    await waitFor(() => expect(listModels).toHaveBeenCalledWith("codex"));
    const picker = await screen.findByRole("combobox", { name: "Codex 模型" });
    expect(screen.getByRole("option", { name: "Agent 默认 · GPT Default" })).toBeDefined();
    expect(screen.getByRole("option", { name: "GPT Default（当前默认） · Default model" })).toBeDefined();
    fireEvent.change(picker, { target: { value: "gpt-fast" } });
    await waitFor(() => expect(updateModel).toHaveBeenCalledWith("codex", { mode: "native", native_model: "gpt-fast" }));
  });
});
