import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { LLMSettingsDialog, ModelPicker } from "../../frontend/src/components/LLMSettings";
import type { CodingAgentAuthSession, CodingAgentDefinition, CodingAgentModelCatalog, CodingAgentSettings } from "../../frontend/src/services/codingAgents";
import type { LLMProvider } from "../../frontend/src/services/llm";

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

const nativeProvider = { id: "local-codex", name: "Local Codex", preset: "codex_native", enabled: true, connection: {}, models: [{ id: "gpt-old", display_name: "GPT Old" }] };
const providerCatalog = [
  { id: "openai", name: "OpenAI", category: "global", fields: [] },
  { id: "codex_native", name: "Codex Native", category: "local", api_mode: "codex_native", fields: [] },
];
const dialogProps = {
  open: true,
  language: "en" as const,
  catalog: providerCatalog,
  providers,
  settings: { default_model: { provider_id: "openai-main", model_id: "gpt-a" }, fast_model: null },
  codingAgents: [codexAgent],
  codingAgentSettings: codingSettings,
  onClose: vi.fn(),
  onRefresh: vi.fn().mockResolvedValue(undefined),
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

const latestCodexModels: CodingAgentModelCatalog = {
  ...codexModels,
  models: [...codexModels.models, { ...codexModels.models[1], id: "gpt-new", model: "gpt-new", display_name: "GPT New" }],
};
const waitingAuth: CodingAgentAuthSession = {
  id: "auth-1", agent_id: "codex", status: "waiting", method: "device_code", verification_uri: "https://auth.openai.com/codex/device", user_code: "TEST-123", expires_at: null, error: "",
};

describe("shared Codex connection", () => {
  it("shows the same current models across all roles while allowing coding-only selection in Codex", async () => {
    const native: LLMProvider = { ...nativeProvider, models: [
      { id: "gpt-default", display_name: "GPT Default", availability: { native_inference: false, coding: true, reason: "native_catalog_missing" } },
      { id: "gpt-fast", display_name: "GPT Fast", availability: { native_inference: true, coding: true, reason: null } },
    ] };
    const updateModel = vi.fn().mockResolvedValue({});
    const updateSettings = vi.fn().mockResolvedValue({});
    render(<LLMSettingsDialog {...dialogProps} providers={[...providers, native]} onListCodingAgentModels={vi.fn().mockResolvedValue(codexModels)} onUpdateCodingAgentModel={updateModel} onUpdateSettings={updateSettings} />);
    await screen.findByRole("option", { name: /GPT Default \(current default\)/ });
    for (const trigger of ["GPT A", "Select model"]) {
      fireEvent.click(screen.getByRole("button", { name: trigger }));
      const menu = screen.getByRole("dialog", { name: "Select model" });
      expect((within(menu).getByRole("button", { name: /GPT Default/ }) as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(within(menu).getByRole("button", { name: /GPT Fast/ }));
      await waitFor(() => expect(updateSettings).toHaveBeenCalledTimes(trigger === "GPT A" ? 1 : 2));
    }
    expect(updateSettings).toHaveBeenNthCalledWith(1, { default_model: { provider_id: native.id, model_id: "gpt-fast" } });
    expect(updateSettings).toHaveBeenNthCalledWith(2, { fast_model: { provider_id: native.id, model_id: "gpt-fast" } });
    fireEvent.change(screen.getByLabelText("Codex model"), { target: { value: "gpt-default" } });
    await waitFor(() => expect(updateModel).toHaveBeenCalledWith("codex", { mode: "native", native_model: "gpt-default" }));
  });

  it("refreshes the coding selector after syncing the unified catalog", async () => {
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockResolvedValue(latestCodexModels);
    const sync = vi.fn().mockResolvedValue(nativeProvider);
    render(<LLMSettingsDialog {...dialogProps} onListCodingAgentModels={listModels} onSyncCodexConnection={sync} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    expect(listModels).toHaveBeenCalledTimes(2);
  });

  it("retains the previous coding catalog and reports a failed refresh after sync", async () => {
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockRejectedValueOnce(new Error("Coding catalog offline"));
    render(<LLMSettingsDialog {...dialogProps} onListCodingAgentModels={listModels} onSyncCodexConnection={vi.fn().mockResolvedValue(nativeProvider)} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/Coding catalog offline.*Use Refresh/));
    expect(screen.getByRole("option", { name: "GPT Fast · Fast model" })).toBeDefined();
  });

  it("syncs the inference catalog on demand and exposes it to both roles without changing bindings", async () => {
    const syncedProvider = { ...nativeProvider, models: [{ id: "gpt-primary", display_name: "GPT Primary" }] };
    const sync = vi.fn().mockResolvedValue(syncedProvider);
    const discover = vi.fn().mockResolvedValue({});
    const updateSettings = vi.fn();
    const updateAgent = vi.fn();
    const updateAgentModel = vi.fn();
    let refresh!: ReturnType<typeof vi.fn>;
    let view!: ReturnType<typeof render>;
    const props = { ...dialogProps, onSyncCodexConnection: sync, onDiscoverModels: discover, onUpdateSettings: updateSettings, onUpdateCodingAgent: updateAgent, onUpdateCodingAgentModel: updateAgentModel };
    refresh = vi.fn().mockImplementation(async () => {
      view.rerender(<LLMSettingsDialog {...props} providers={[...providers, syncedProvider]} onRefresh={refresh} />);
    });
    view = render(<LLMSettingsDialog {...props} onRefresh={refresh} />);
    expect(sync).not.toHaveBeenCalled();
    expect(screen.getByText(/share the managed Codex sign-in.*choose models independently/i)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "GPT A" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Select model" })).getByText("GPT Primary"));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ default_model: { provider_id: syncedProvider.id, model_id: "gpt-primary" } }));
    fireEvent.click(screen.getByRole("button", { name: "Select model" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Select model" })).getByText("GPT Primary"));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ fast_model: { provider_id: syncedProvider.id, model_id: "gpt-primary" } }));
    expect(sync).toHaveBeenCalledTimes(1);
    expect(discover).not.toHaveBeenCalled();
    expect(updateAgent).not.toHaveBeenCalled();
    expect(updateAgentModel).not.toHaveBeenCalled();
  });

  it("syncs once after a fresh login and does not repeat when parent authentication catches up", async () => {
    const sync = vi.fn().mockResolvedValue(nativeProvider);
    const discover = vi.fn().mockResolvedValue({});
    const startAuth = vi.fn().mockResolvedValue({ ...waitingAuth, status: "signed_in" });
    const props = { ...dialogProps, onSyncCodexConnection: sync, onDiscoverModels: discover, onStartCodingAgentAuth: startAuth, onUpdateCodingAgent: vi.fn() };
    const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
    const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1));
    rerender(<LLMSettingsDialog {...props} providers={[...providers, nativeProvider]} />);
    await act(async () => {});
    expect(sync).toHaveBeenCalledTimes(1);
    expect(discover).not.toHaveBeenCalled();
    expect(screen.getByRole("radio", { name: /^Codex/ }).hasAttribute("disabled")).toBe(false);
  });

  it("syncs once when a polled device login completes", async () => {
    vi.useFakeTimers();
    try {
      const sync = vi.fn().mockResolvedValue(nativeProvider);
      render(<LLMSettingsDialog {...dialogProps} codingAgents={[{ ...codexAgent, authenticated: false, auth_state: "signed_out" }]} onSyncCodexConnection={sync} onStartCodingAgentAuth={vi.fn().mockResolvedValue(waitingAuth)} onGetCodingAgentAuth={vi.fn().mockResolvedValue({ ...waitingAuth, status: "signed_in" })} />);
      fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
      await act(async () => {});
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(sync).toHaveBeenCalledTimes(1);
      await act(async () => vi.advanceTimersByTimeAsync(2400));
      expect(sync).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("syncs a fresh login when authoritative configuration reports completion before auth polling", async () => {
    const sync = vi.fn().mockResolvedValue(nativeProvider);
    const props = { ...dialogProps, onSyncCodexConnection: sync, onStartCodingAgentAuth: vi.fn().mockResolvedValue(waitingAuth), onGetCodingAgentAuth: vi.fn() };
    const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[{ ...codexAgent, authenticated: false, auth_state: "signed_out" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    await screen.findByText("TEST-123");
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1));
    rerender(<LLMSettingsDialog {...props} />);
    await act(async () => {});
    expect(sync).toHaveBeenCalledTimes(1);
    expect(props.onGetCodingAgentAuth).not.toHaveBeenCalled();
  });

  it("retains coding readiness and existing selections after a sync error, with explicit retry", async () => {
    const sync = vi.fn().mockRejectedValueOnce(new Error("Native Codex requires the supported Linux profile")).mockResolvedValue(nativeProvider);
    const refresh = vi.fn().mockResolvedValue(undefined);
    const updateSettings = vi.fn();
    const props = { ...dialogProps, onSyncCodexConnection: sync, onRefresh: refresh, onUpdateSettings: updateSettings, onUpdateCodingAgent: vi.fn() };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/Native Codex requires the supported Linux profile.*Sync primary\/fast models.*retry/));
    expect(screen.getByRole("button", { name: "GPT A" })).toBeDefined();
    expect(screen.getByRole("radio", { name: /^Codex/ }).hasAttribute("disabled")).toBe(false);
    expect(refresh).not.toHaveBeenCalled();
    expect(updateSettings).not.toHaveBeenCalled();
    rerender(<LLMSettingsDialog {...props} />);
    await act(async () => {});
    expect(sync).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(sync).toHaveBeenCalledTimes(2);
  });

  it.each(["closed", "reopened", "signed out", "new login"])("ignores a sync result after settings are %s", async (change) => {
    const request = deferred<typeof nativeProvider>();
    const sync = vi.fn().mockReturnValue(request.promise);
    const refresh = vi.fn().mockResolvedValue(undefined);
    const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
    const props = { ...dialogProps, onSyncCodexConnection: sync, onRefresh: refresh, onClearCodingAgentAuth: vi.fn().mockResolvedValue({}), onStartCodingAgentAuth: vi.fn().mockResolvedValue(waitingAuth) };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    expect(sync).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Sync primary/fast models" }).hasAttribute("disabled")).toBe(true);
    if (change === "closed" || change === "reopened") {
      rerender(<LLMSettingsDialog {...props} open={false} />);
      if (change === "reopened") rerender(<LLMSettingsDialog {...props} />);
    } else {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
      await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
      refresh.mockClear();
      rerender(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
      if (change === "new login") {
        fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
        await screen.findByText("TEST-123");
      }
    }
    await act(async () => request.resolve(nativeProvider));
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.queryByText(/Codex model catalog synced/)).toBeNull();
  });

  it("does not sync a missing connection merely by reopening settings", async () => {
    const sync = vi.fn().mockResolvedValue(nativeProvider);
    const props = { ...dialogProps, onSyncCodexConnection: sync };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await act(async () => {});
    expect(sync).not.toHaveBeenCalled();
  });

  it("blocks new sync requests while sign-out is pending, including after settings reopen", async () => {
    const logout = deferred<unknown>();
    const sync = vi.fn().mockResolvedValue(nativeProvider);
    const props = { ...dialogProps, onSyncCodexConnection: sync, onClearCodingAgentAuth: vi.fn().mockReturnValue(logout.promise) };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    let syncButton = screen.getByRole("button", { name: "Sync primary/fast models" });
    expect(syncButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(syncButton);
    expect(sync).not.toHaveBeenCalled();
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    syncButton = screen.getByRole("button", { name: "Sync primary/fast models" });
    expect(syncButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(syncButton);
    expect(sync).not.toHaveBeenCalled();
    await act(async () => logout.resolve({}));
    expect(screen.getByRole("button", { name: "Sync primary/fast models" }).hasAttribute("disabled")).toBe(true);
  });

  it("allows sign-out and sync retry when the sign-out request fails", async () => {
    const clearAuth = vi.fn().mockRejectedValueOnce(new Error("Sign-out unavailable")).mockResolvedValue({});
    const sync = vi.fn().mockResolvedValue(nativeProvider);
    render(<LLMSettingsDialog {...dialogProps} onSyncCodexConnection={sync} onClearCodingAgentAuth={clearAuth} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByText("Sign-out unavailable");
    expect(screen.getByRole("button", { name: "Sync primary/fast models" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "Sign out" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Sync primary/fast models" }));
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(clearAuth).toHaveBeenCalledTimes(2));
  });
});

describe("coding agent model saves", () => {
  it("uses the authoritative native binding and falls back to the agent snapshot when it is absent", async () => {
    const agent = { ...codexAgent, model_config: { mode: "native" as const, native_model: "gpt-default" } };
    const authoritative = { ...codingSettings, agent_models: { ...codingSettings.agent_models, codex: { mode: "native" as const, native_model: "gpt-fast" } } };
    const props = { ...dialogProps, codingAgents: [agent], onListCodingAgentModels: vi.fn().mockResolvedValue(codexModels), onUpdateCodingAgentModel: vi.fn() };
    const { rerender } = render(<LLMSettingsDialog {...props} codingAgentSettings={authoritative} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    expect((screen.getByRole("combobox", { name: "Codex model" }) as HTMLSelectElement).value).toBe("gpt-fast");
    rerender(<LLMSettingsDialog {...props} codingAgentSettings={{ ...codingSettings, agent_models: {} }} />);
    expect((screen.getByRole("combobox", { name: "Codex model" }) as HTMLSelectElement).value).toBe("gpt-default");
    rerender(<LLMSettingsDialog {...props} codingAgentSettings={undefined} />);
    expect((screen.getByRole("combobox", { name: "Codex model" }) as HTMLSelectElement).value).toBe("gpt-default");
  });

  it("uses authoritative shared bindings before older agent snapshots", () => {
    render(<LLMSettingsDialog {...dialogProps} codingAgents={[{ ...opencodeAgent, installed: true, model_config: { mode: "shared_binding", provider_id: "openai-main", model_id: "gpt-b" } }]} codingAgentSettings={{ ...codingSettings, agent_models: { opencode: { mode: "shared_binding", provider_id: "openai-main", model_id: "gpt-a" } } }} onUpdateCodingAgentModel={vi.fn()} />);
    expect((screen.getByRole("combobox", { name: "Execution model" }) as HTMLSelectElement).value).toBe("openai-main:gpt-a");
  });

  it("blocks duplicate saves and preserves the original native selection when saving fails", async () => {
    let rejectSave!: (error: Error) => void;
    const updateModel = vi.fn().mockReturnValue(new Promise((_resolve, reject) => { rejectSave = reject; }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<LLMSettingsDialog {...dialogProps} codingAgentSettings={{ ...codingSettings, agent_models: { codex: { mode: "native", native_model: "gpt-fast" } } }} onListCodingAgentModels={vi.fn().mockResolvedValue(latestCodexModels)} onUpdateCodingAgentModel={updateModel} onRefresh={refresh} />);
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    const picker = screen.getByRole("combobox", { name: "Codex model" }) as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: "gpt-new" } });
    expect(updateModel).toHaveBeenCalledWith("codex", { mode: "native", native_model: "gpt-new" });
    expect(picker.disabled).toBe(true);
    fireEvent.change(picker, { target: { value: "gpt-default" } });
    expect(updateModel).toHaveBeenCalledTimes(1);
    await act(async () => rejectSave(new Error("Model save unavailable")));
    expect(picker.value).toBe("gpt-fast");
    expect(picker.disabled).toBe(false);
    expect(screen.getByRole("status").textContent).toBe("Model save unavailable");
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe("Codex managed updates", () => {
  it.each([
    { language: "en" as const, action: "Update", target: "Update to 0.159.3", started: "Codex update started" },
    { language: "zh" as const, action: "更新", target: "更新至 0.159.3", started: "Codex 更新已开始" },
  ])("updates an installed Codex and displays its current and target versions in $language", async ({ language, action, target, started }) => {
    const pending = deferred<unknown>();
    const install = vi.fn().mockReturnValue(pending.promise);
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<LLMSettingsDialog {...dialogProps} language={language} codingAgents={[{ ...codexAgent, version: "codex-cli 0.145.0", update_available: true, target_version: "0.159.3" }]} onInstallCodingAgent={install} onRefresh={refresh} />);

    const button = screen.getByRole("button", { name: action });
    expect(screen.getByText(/codex-cli 0\.145\.0/).textContent).toContain(target);
    fireEvent.click(button);
    expect(install).toHaveBeenCalledWith("codex");
    expect(button.hasAttribute("disabled")).toBe(true);
    await act(async () => pending.resolve({ status: "installing" }));
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status").textContent).toBe(started);
  });

  it("disables Update while an installed Codex is updating and keeps polling with its current models", async () => {
    vi.useFakeTimers();
    try {
      const install = vi.fn();
      const refresh = vi.fn().mockResolvedValue(undefined);
      const listModels = vi.fn().mockResolvedValue(codexModels);
      const { unmount } = render(<LLMSettingsDialog {...dialogProps} codingAgents={[{ ...codexAgent, install_state: "installing", update_available: true, target_version: "0.159.3" }]} onInstallCodingAgent={install} onRefresh={refresh} onListCodingAgentModels={listModels} />);
      await act(async () => {});

      const button = screen.getByRole("button", { name: "Update" });
      expect(button.hasAttribute("disabled")).toBe(true);
      expect(screen.getByText("Updating")).toBeDefined();
      expect(screen.getByRole("option", { name: "GPT Fast · Fast model" })).toBeDefined();
      fireEvent.click(button);
      expect(install).not.toHaveBeenCalled();
      await act(async () => vi.advanceTimersByTime(1500));
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(listModels).toHaveBeenCalledTimes(1);
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("preserves models and bindings after an update fails, permits retry, and refreshes models on completion", async () => {
    const install = vi.fn().mockResolvedValue({ status: "installing" });
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockResolvedValue(latestCodexModels);
    const updateSettings = vi.fn();
    const updateAgent = vi.fn();
    const updateModel = vi.fn();
    const refresh = vi.fn().mockResolvedValue(undefined);
    const oldAgent = { ...codexAgent, version: "codex-cli 0.145.0", update_available: true, target_version: "0.159.3", model_config: { mode: "native" as const, native_model: "gpt-fast" } };
    const props = { ...dialogProps, codingAgentSettings: { ...codingSettings, agent_models: { ...codingSettings.agent_models, codex: oldAgent.model_config } }, onInstallCodingAgent: install, onListCodingAgentModels: listModels, onRefresh: refresh, onUpdateSettings: updateSettings, onUpdateCodingAgent: updateAgent, onUpdateCodingAgentModel: updateModel };
    const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[oldAgent]} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });

    rerender(<LLMSettingsDialog {...props} codingAgents={[{ ...oldAgent, install_state: "failed", install_operation: { id: "update-1", agent_id: "codex", status: "failed", created_at: 1, error: "Codex download interrupted" } }]} />);
    expect(screen.getByText("Codex download interrupted")).toBeDefined();
    expect((screen.getByRole("combobox", { name: "Codex model" }) as HTMLSelectElement).value).toBe("gpt-fast");
    expect(screen.getByRole("option", { name: "GPT Fast · Fast model" })).toBeDefined();
    expect(screen.getByRole("button", { name: "GPT A" })).toBeDefined();
    expect(listModels).toHaveBeenCalledTimes(1);
    const retry = screen.getByRole("button", { name: "Update" });
    expect(retry.hasAttribute("disabled")).toBe(false);
    fireEvent.click(retry);
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(install).toHaveBeenCalledWith("codex");

    rerender(<LLMSettingsDialog {...props} codingAgents={[{ ...oldAgent, version: "codex-cli 0.159.3", update_available: false, install_state: "installed", install_operation: null }]} />);
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    expect(listModels).toHaveBeenCalledTimes(2);
    expect((screen.getByRole("combobox", { name: "Codex model" }) as HTMLSelectElement).value).toBe("gpt-fast");
    expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
    expect(screen.queryByText(/Update to/)).toBeNull();
    expect(updateSettings).not.toHaveBeenCalled();
    expect(updateAgent).not.toHaveBeenCalled();
    expect(updateModel).not.toHaveBeenCalled();
  });

  it("keeps older definitions compatible and leaves externally managed Codex without an update action", () => {
    const { rerender } = render(<LLMSettingsDialog {...dialogProps} onInstallCodingAgent={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
    rerender(<LLMSettingsDialog {...dialogProps} codingAgents={[{ ...codexAgent, installable: false, update_available: true, target_version: "0.159.3" }]} onInstallCodingAgent={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
  });
});

describe("Codex model catalog refresh", () => {
  it("refreshes existing native providers together with a manual coding catalog refresh", async () => {
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockResolvedValue(latestCodexModels);
    const discover = vi.fn().mockResolvedValue({});
    const disabled = { ...nativeProvider, id: "disabled-codex", enabled: false };
    render(<LLMSettingsDialog {...dialogProps} providers={[...providers, nativeProvider, disabled]} onListCodingAgentModels={listModels} onDiscoverModels={discover} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Refresh Codex models" }));
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(2));
    expect(discover.mock.calls.every(([id]) => id === nativeProvider.id)).toBe(true);
  });

  it("ignores a manual provider refresh that completes after the dialog closes", async () => {
    const pending = deferred<unknown>();
    const discover = vi.fn().mockResolvedValueOnce({}).mockReturnValueOnce(pending.promise);
    const refresh = vi.fn().mockResolvedValue(undefined);
    const props = { ...dialogProps, providers: [nativeProvider], onListCodingAgentModels: vi.fn().mockResolvedValue(codexModels), onDiscoverModels: discover, onRefresh: refresh };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Refresh Codex models" }));
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(2));
    rerender(<LLMSettingsDialog {...props} open={false} />);
    await act(async () => pending.resolve({}));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("refreshes on every open and version change without refetching for new prop identities or changing selections", async () => {
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockResolvedValue(latestCodexModels);
    const updateSettings = vi.fn();
    const updateModel = vi.fn();
    const props = { ...dialogProps, onListCodingAgentModels: listModels, onUpdateSettings: updateSettings, onUpdateCodingAgentModel: updateModel };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    rerender(<LLMSettingsDialog {...props} providers={[...providers]} codingAgents={[{ ...codexAgent }]} onRefresh={vi.fn()} onListCodingAgentModels={(id) => listModels(id)} />);
    await act(async () => {});
    expect(listModels).toHaveBeenCalledTimes(1);
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    expect(listModels).toHaveBeenCalledTimes(2);
    rerender(<LLMSettingsDialog {...props} codingAgents={[{ ...codexAgent, version: "codex-cli 2.0" }]} />);
    await waitFor(() => expect(listModels).toHaveBeenCalledTimes(3));
    expect(updateSettings).not.toHaveBeenCalled();
    expect(updateModel).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "GPT A" })).toBeDefined();
  });

  it("preserves previous models on failure, explains retry, and retries manually or on the next open", async () => {
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockRejectedValueOnce(new Error("Codex offline")).mockResolvedValueOnce(latestCodexModels).mockResolvedValue(codexModels);
    const props = { ...dialogProps, onListCodingAgentModels: listModels };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/Codex offline.*Use Refresh or reopen settings to retry/));
    expect(screen.getByRole("option", { name: "GPT Fast · Fast model" })).toBeDefined();
    expect(listModels).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh Codex models" }));
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(listModels).toHaveBeenCalledTimes(4));
  });

  it("clears the signed-out catalog, ignores an old request, and refreshes after installation and login", async () => {
    const oldRequest = deferred<CodingAgentModelCatalog>();
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockReturnValueOnce(oldRequest.promise).mockResolvedValue(latestCodexModels);
    const props = { ...dialogProps, onListCodingAgentModels: listModels };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    fireEvent.click(screen.getByRole("button", { name: "Refresh Codex models" }));
    await waitFor(() => expect(listModels).toHaveBeenCalledTimes(2));
    const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
    rerender(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    expect(screen.queryByRole("option", { name: "GPT Fast · Fast model" })).toBeNull();
    await act(async () => oldRequest.resolve(codexModels));
    expect(screen.queryByRole("option", { name: "GPT Fast · Fast model" })).toBeNull();
    rerender(<LLMSettingsDialog {...props} codingAgents={[{ ...signedOut, installed: false }]} />);
    await act(async () => {});
    expect(listModels).toHaveBeenCalledTimes(2);
    rerender(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    await act(async () => {});
    expect(listModels).toHaveBeenCalledTimes(2);
    rerender(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    expect(listModels).toHaveBeenCalledTimes(3);
  });

  it("ignores a request from a previous opening when it finishes after a newer request", async () => {
    const oldRequest = deferred<CodingAgentModelCatalog>();
    const listModels = vi.fn().mockReturnValueOnce(oldRequest.promise).mockResolvedValue(latestCodexModels);
    const props = { ...dialogProps, onListCodingAgentModels: listModels };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(listModels).toHaveBeenCalledTimes(1));
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    await act(async () => oldRequest.resolve(codexModels));
    expect(screen.getByRole("option", { name: "GPT New · Fast model" })).toBeDefined();
  });

  it("clears native agent models after Sign out even before refreshed parent state arrives", async () => {
    const oldRequest = deferred<CodingAgentModelCatalog>();
    const oldDiscovery = deferred<unknown>();
    const listModels = vi.fn().mockResolvedValueOnce(codexModels).mockReturnValueOnce(oldRequest.promise);
    const clearAuth = vi.fn().mockResolvedValue({});
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<LLMSettingsDialog {...dialogProps} providers={[nativeProvider]} onRefresh={refresh} onListCodingAgentModels={listModels} onClearCodingAgentAuth={clearAuth} onDiscoverModels={vi.fn().mockReturnValue(oldDiscovery.promise)} />);
    await screen.findByRole("option", { name: "GPT Fast · Fast model" });
    expect(screen.getByRole("button", { name: "Discover" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Refresh Codex models" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(clearAuth).toHaveBeenCalledWith("codex"));
    await waitFor(() => expect(screen.queryByRole("option", { name: "GPT Fast · Fast model" })).toBeNull());
    expect(screen.getByRole("button", { name: "Discover" }).hasAttribute("disabled")).toBe(false);
    await act(async () => oldRequest.resolve(codexModels));
    await act(async () => oldDiscovery.resolve({}));
    expect(screen.queryByRole("option", { name: "GPT Fast · Fast model" })).toBeNull();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("discovers only enabled native providers when ready, and once per open, login, or new eligible provider", async () => {
    const discover = vi.fn().mockResolvedValue({});
    const refresh = vi.fn().mockResolvedValue(undefined);
    const disabled = { ...nativeProvider, id: "disabled-codex", enabled: false };
    const props = { ...dialogProps, providers: [...providers, nativeProvider, disabled], onDiscoverModels: discover, onRefresh: refresh };
    const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
    const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    await act(async () => {});
    expect(discover).not.toHaveBeenCalled();
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(discover).toHaveBeenCalledExactlyOnceWith("local-codex");
    rerender(<LLMSettingsDialog {...props} providers={props.providers.map((provider) => ({ ...provider }))} onDiscoverModels={(id) => discover(id)} onRefresh={() => refresh()} />);
    await act(async () => {});
    expect(discover).toHaveBeenCalledTimes(1);
    rerender(<LLMSettingsDialog {...props} providers={[...props.providers, { ...nativeProvider, id: "new-codex" }]} />);
    await waitFor(() => expect(discover).toHaveBeenCalledWith("new-codex"));
    expect(discover).toHaveBeenCalledTimes(2);
    rerender(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(3));
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(4));
    rerender(<LLMSettingsDialog {...props} providers={[...providers, { ...nativeProvider, enabled: false }, disabled]} />);
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(5));
    expect(discover.mock.calls.every(([id]) => id === "local-codex" || id === "new-codex")).toBe(true);
  });

  it("keeps native provider models after discovery fails and offers manual or next-open retry", async () => {
    const discover = vi.fn().mockRejectedValueOnce(new Error("Catalog unavailable")).mockResolvedValue({});
    const refresh = vi.fn().mockResolvedValue(undefined);
    const props = { ...dialogProps, providers: [nativeProvider], onDiscoverModels: discover, onRefresh: refresh };
    const { rerender } = render(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/Catalog unavailable.*Use Discover or reopen settings to retry/));
    expect(screen.getByText("GPT Old")).toBeDefined();
    expect(refresh).not.toHaveBeenCalled();
    rerender(<LLMSettingsDialog {...props} />);
    await act(async () => {});
    expect(discover).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(discover).toHaveBeenCalledTimes(2);
    rerender(<LLMSettingsDialog {...props} open={false} />);
    rerender(<LLMSettingsDialog {...props} />);
    await waitFor(() => expect(discover).toHaveBeenCalledTimes(3));
  });

  it("refreshes immediately after successful login and does not repeat when parent authentication catches up", async () => {
    const listModels = vi.fn().mockResolvedValue(latestCodexModels);
    const discover = vi.fn().mockResolvedValue({});
    const startAuth = vi.fn().mockResolvedValue({ ...waitingAuth, status: "signed_in" });
    const props = { ...dialogProps, providers: [nativeProvider], onListCodingAgentModels: listModels, onDiscoverModels: discover, onStartCodingAgentAuth: startAuth };
    const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
    const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    await screen.findByRole("option", { name: "GPT New · Fast model" });
    expect(discover).toHaveBeenCalledExactlyOnceWith(nativeProvider.id);
    rerender(<LLMSettingsDialog {...props} />);
    await act(async () => {});
    expect(listModels).toHaveBeenCalledTimes(1);
    expect(discover).toHaveBeenCalledTimes(1);
    rerender(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
    expect(screen.queryByRole("option", { name: "GPT New · Fast model" })).toBeNull();
  });

  it("accepts a new login poll after reopen while ignoring the previous opening's auth response", async () => {
    vi.useFakeTimers();
    try {
      const oldPoll = deferred<CodingAgentAuthSession>();
      const getAuth = vi.fn().mockReturnValueOnce(oldPoll.promise).mockResolvedValue({ ...waitingAuth, status: "signed_in" });
      const listModels = vi.fn().mockResolvedValue(latestCodexModels);
      const props = { ...dialogProps, codingAgents: [{ ...codexAgent, authenticated: false, auth_state: "signed_out" as const }], onStartCodingAgentAuth: vi.fn().mockResolvedValue(waitingAuth), onGetCodingAgentAuth: getAuth, onListCodingAgentModels: listModels };
      const { rerender } = render(<LLMSettingsDialog {...props} />);
      fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
      await act(async () => {});
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(getAuth).toHaveBeenCalledTimes(1);
      rerender(<LLMSettingsDialog {...props} open={false} />);
      rerender(<LLMSettingsDialog {...props} />);
      await act(async () => oldPoll.resolve({ ...waitingAuth, status: "signed_in" }));
      expect(listModels).not.toHaveBeenCalled();
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(getAuth).toHaveBeenCalledTimes(2);
      expect(listModels).toHaveBeenCalledExactlyOnceWith("codex");
      expect(screen.getByRole("option", { name: "GPT New · Fast model" })).toBeDefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a pending device login when parent status catches up and continues polling to signed in", async () => {
    vi.useFakeTimers();
    try {
      const listModels = vi.fn().mockResolvedValue(latestCodexModels);
      const getAuth = vi.fn().mockResolvedValue({ ...waitingAuth, status: "signed_in" });
      const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
      const props = { ...dialogProps, onStartCodingAgentAuth: vi.fn().mockResolvedValue(waitingAuth), onGetCodingAgentAuth: getAuth, onListCodingAgentModels: listModels };
      const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
      fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
      await act(async () => {});
      expect(screen.getByText("TEST-123")).toBeDefined();
      rerender(<LLMSettingsDialog {...props} codingAgents={[{ ...signedOut, auth_state: "waiting" }]} />);
      expect(screen.getByText("TEST-123")).toBeDefined();
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(getAuth).toHaveBeenCalledExactlyOnceWith("codex");
      expect(listModels).toHaveBeenCalledExactlyOnceWith("codex");
      expect(screen.getByRole("option", { name: "GPT New · Fast model" })).toBeDefined();
      rerender(<LLMSettingsDialog {...props} />);
      await act(async () => {});
      expect(listModels).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the login response when parent reports waiting before the device-code request finishes", async () => {
    vi.useFakeTimers();
    try {
      const start = deferred<CodingAgentAuthSession>();
      const listModels = vi.fn().mockResolvedValue(latestCodexModels);
      const getAuth = vi.fn().mockResolvedValue({ ...waitingAuth, status: "signed_in" });
      const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
      const props = { ...dialogProps, onStartCodingAgentAuth: vi.fn().mockReturnValue(start.promise), onGetCodingAgentAuth: getAuth, onListCodingAgentModels: listModels };
      const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
      fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
      rerender(<LLMSettingsDialog {...props} codingAgents={[{ ...signedOut, auth_state: "waiting" }]} />);
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(getAuth).not.toHaveBeenCalled();
      await act(async () => start.resolve(waitingAuth));
      expect(screen.getByText("TEST-123")).toBeDefined();
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(getAuth).toHaveBeenCalledExactlyOnceWith("codex");
      expect(listModels).toHaveBeenCalledExactlyOnceWith("codex");
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows retry after the initial device login request fails", async () => {
    const startAuth = vi.fn().mockRejectedValueOnce(new Error("Login unavailable")).mockResolvedValue(waitingAuth);
    render(<LLMSettingsDialog {...dialogProps} codingAgents={[{ ...codexAgent, authenticated: false, auth_state: "signed_out" }]} onStartCodingAgentAuth={startAuth} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Login unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    await screen.findByText("TEST-123");
    expect(startAuth).toHaveBeenCalledTimes(2);
  });

  it.each(["while closed", "after reopening"])("keeps the device login request when it completes %s", async (completion) => {
    vi.useFakeTimers();
    try {
      const start = deferred<CodingAgentAuthSession>();
      const listModels = vi.fn().mockResolvedValue(latestCodexModels);
      const getAuth = vi.fn().mockResolvedValue({ ...waitingAuth, status: "signed_in" });
      const refresh = vi.fn().mockResolvedValue(undefined);
      const props = { ...dialogProps, codingAgents: [{ ...codexAgent, authenticated: false, auth_state: "signed_out" as const }], onRefresh: refresh, onStartCodingAgentAuth: vi.fn().mockReturnValue(start.promise), onGetCodingAgentAuth: getAuth, onListCodingAgentModels: listModels };
      const { rerender } = render(<LLMSettingsDialog {...props} />);
      fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
      rerender(<LLMSettingsDialog {...props} open={false} />);
      if (completion === "after reopening") rerender(<LLMSettingsDialog {...props} />);
      await act(async () => start.resolve(waitingAuth));
      expect(refresh).not.toHaveBeenCalled();
      if (completion === "while closed") rerender(<LLMSettingsDialog {...props} />);
      expect(screen.getByText("TEST-123")).toBeDefined();
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      expect(getAuth).toHaveBeenCalledExactlyOnceWith("codex");
      expect(listModels).toHaveBeenCalledExactlyOnceWith("codex");
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores an old login poll after authoritative authentication changes to signed out", async () => {
    vi.useFakeTimers();
    try {
      const oldPoll = deferred<CodingAgentAuthSession>();
      const listModels = vi.fn().mockResolvedValue(latestCodexModels);
      const signedOut = { ...codexAgent, authenticated: false, auth_state: "signed_out" as const };
      const props = { ...dialogProps, onStartCodingAgentAuth: vi.fn().mockResolvedValue(waitingAuth), onGetCodingAgentAuth: vi.fn().mockReturnValue(oldPoll.promise), onListCodingAgentModels: listModels };
      const { rerender } = render(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
      fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
      await act(async () => {});
      await act(async () => vi.advanceTimersByTimeAsync(1200));
      rerender(<LLMSettingsDialog {...props} />);
      await act(async () => {});
      expect(listModels).toHaveBeenCalledTimes(1);
      rerender(<LLMSettingsDialog {...props} codingAgents={[signedOut]} />);
      await act(async () => oldPoll.resolve({ ...waitingAuth, status: "signed_in" }));
      expect(listModels).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("option", { name: "GPT New · Fast model" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("LLM provider settings", () => {
  it("explains managed native login and creates a Codex provider without API credentials", async () => {
    const create = vi.fn().mockResolvedValue({});
    render(<LLMSettingsDialog open language="en"
      catalog={[{ id: "codex_native", name: "Codex Native", category: "local", api_mode: "codex_native", fields: [], advanced_fields: [] }]}
      providers={[]} settings={{ default_model: null, fast_model: null }}
      onClose={vi.fn()} onRefresh={vi.fn()} onCreateProvider={create} />);

    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(screen.getByText("Uses the Codex login managed by Ambient. Sign in under Coding agent; no API key is needed.")).toBeDefined();
    expect(screen.queryByLabelText(/API key/)).toBeNull();
    expect(screen.queryByLabelText(/Base URL|Secret headers|Query parameters/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Local Codex" } });
    fireEvent.change(screen.getByLabelText("Provider ID"), { target: { value: "local-codex" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const [profile, credentials] = create.mock.calls[0];
    expect(profile).toMatchObject({ id: "local-codex", name: "Local Codex", preset: "codex_native", connection: {}, credential_refs: {} });
    expect(credentials).toEqual({});
  });

  it("keeps native models out of OpenCode bindings while preserving explicit API model choices", () => {
    const update = vi.fn().mockResolvedValue({});
    const native = { id: "local-codex", name: "Local Codex", preset: "codex_native", enabled: true, connection: {}, models: [{ id: "gpt-5.6-luna", display_name: "GPT Luna" }] };
    render(<LLMSettingsDialog open language="en"
      catalog={[{ id: "openai", name: "OpenAI", category: "global", fields: [] }, { id: "codex_native", name: "Codex Native", category: "local", api_mode: "codex_native", fields: [] }]}
      providers={[...providers, native]} settings={{ default_model: { provider_id: native.id, model_id: "gpt-5.6-luna" }, fast_model: null }}
      codingAgents={[{ ...opencodeAgent, installed: true, available: true }]}
      codingAgentSettings={codingSettings} onClose={vi.fn()} onRefresh={vi.fn()} onUpdateCodingAgentModel={update} />);

    const select = screen.getByRole("combobox", { name: "Execution model" });
    expect(within(select).queryByRole("option", { name: /GPT Luna/ })).toBeNull();
    expect((within(select).getByRole("option", { name: "Inherit Ambient primary" }) as HTMLOptionElement).disabled).toBe(true);
    expect(screen.getByText("OpenCode needs an API provider model. Choose an API model here or use Codex as the coding agent.")).toBeDefined();
    fireEvent.change(select, { target: { value: "openai-main:gpt-a" } });
    expect(update).toHaveBeenCalledWith("opencode", { mode: "shared_binding", provider_id: "openai-main", model_id: "gpt-a" });
  });

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

  it.each([
    { language: "zh" as const, reason: "native_catalog_missing" as const, hint: "仅编码可用；主/快速暂不支持" },
    { language: "en" as const, reason: "native_catalog_missing" as const, hint: "Coding only; primary/fast not supported yet" },
    { language: "zh" as const, reason: "native_profile_unsupported" as const, hint: "主/快速暂不支持此模型" },
    { language: "en" as const, reason: "native_profile_unsupported" as const, hint: "Primary/fast do not support this model yet" },
    { language: "zh" as const, reason: "coding_catalog_missing" as const, hint: "已不在当前 Codex 目录中" },
    { language: "en" as const, reason: "coding_catalog_missing" as const, hint: "No longer in the current Codex catalog" },
  ])("shows incompatible Codex models with a disabled reason in $language ($reason)", ({ language, reason, hint }) => {
    const native: LLMProvider = { ...nativeProvider, models: [
      { id: "gpt-6-luna", display_name: "GPT-6-Luna", availability: { native_inference: false, coding: reason !== "coding_catalog_missing", reason }, capabilities: { tool_calling: true } },
      { id: "gpt-5.6-luna", display_name: "GPT-5.6-Luna", availability: { native_inference: true, coding: true, reason: null } },
    ] };
    const select = vi.fn();
    render(<ModelPicker providers={[native]} value={{ provider_id: native.id, model_id: "gpt-6-luna" }} onChange={select} language={language} />);
    fireEvent.click(screen.getByRole("button", { name: "GPT-6-Luna" }));
    const menu = screen.getByRole("dialog", { name: language === "zh" ? "选择模型" : "Select model" });
    const unavailable = within(menu).getByRole("button", { name: new RegExp("GPT-6-Luna") }) as HTMLButtonElement;
    expect(unavailable.disabled).toBe(true);
    expect(within(unavailable).getByText(hint)).toBeDefined();
    fireEvent.click(unavailable);
    expect(select).not.toHaveBeenCalled();
    fireEvent.click(within(menu).getByText("GPT-5.6-Luna"));
    expect(select).toHaveBeenCalledExactlyOnceWith({ provider_id: native.id, model_id: "gpt-5.6-luna" });
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
