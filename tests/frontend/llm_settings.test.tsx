import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { LLMSettingsDialog, ModelPicker } from "../../frontend/src/components/LLMSettings";
import type { CodingAgentAuthSession, CodingAgentDefinition, CodingAgentModelCatalog, CodingAgentSettings } from "../../frontend/src/services/codingAgents";

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

describe("Codex model catalog refresh", () => {
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
