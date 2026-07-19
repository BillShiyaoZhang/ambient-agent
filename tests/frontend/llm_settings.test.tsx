import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LLMSettingsDialog, ModelPicker } from "../../frontend/src/components/LLMSettings";

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
});
