import React, { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppCenter } from "../../frontend/src/components/AppCenter";
import wsService from "../../frontend/src/services/websocket";

vi.mock("../../frontend/src/services/websocket", () => ({
  default: { sendMessage: vi.fn() },
}));

const state = {
  version: 1,
  revision: 2,
  items: [
    {
      catalog_id: "app:weather",
      kind: "generated_app",
      title: "Weather",
      description: "Local forecast",
      version: "1.0.0",
      provider: "Ambient Agent",
      tags: ["forecast"],
      ui_app_id: "weather",
      status: "ready",
    },
    {
      catalog_id: "mcp:acme:calendar",
      kind: "mcp",
      title: "Calendar Tools",
      description: "Manage events",
      version: "2.0.0",
      provider: "Acme",
      tags: ["events"],
      ui_app_id: null,
      status: "needs_ui",
    },
  ],
  root: ["app:weather", "mcp:acme:calendar"],
  folders: [],
};

const instructionSkill = {
  catalog_id: "agent-skill:ambient:research-notes",
  kind: "skill",
  title: "Research Notes",
  description: "Guidance for evidence-backed research.",
  version: "1.2.0",
  provider: "Ambient",
  tags: ["research", "writing"],
  ui_app_id: null,
  launch_mode: "details",
  surfaces: ["agent_context"],
  status: "ready",
  skill: {
    enabled: true,
    digest: "sha256:installed-research",
    source: "registry://ambient/research-notes",
    verified: true,
    installed_at: "2026-07-29T10:00:00Z",
    ontology_refs: ["Document", "Note"],
    license: "MIT",
    compatibility: "ambient-agent >= 0.1",
    registry_revision: 7,
    authorization: {
      state: "trusted",
      activation_policy: "implicit",
      authorized_digest: null,
      requires_reauthorization: false,
    },
  },
};

const skillState = {
  version: 1,
  revision: 4,
  items: [instructionSkill],
  root: [instructionSkill.catalog_id],
  folders: [],
};

const marketState = {
  version: 1,
  revision: 7,
  items: [
    {
      market_id: "ambient/research-notes",
      catalog_id: "agent-skill:ambient:research-notes",
      name: "research-notes",
      title: "Research Notes",
      description: "Guidance for evidence-backed research.",
      version: "1.3.0",
      provider: "Ambient",
      tags: ["research", "writing"],
      license: "MIT",
      compatibility: "ambient-agent >= 0.1",
      ontology_refs: ["Document", "Note"],
      surfaces: ["agent_context"],
      provenance: {
        source: "registry://ambient/research-notes",
        digest: "sha256:market-research",
        verified: true,
      },
      install_state: "update_available",
      installed_version: "1.2.0",
      enabled: true,
      authorization: {
        state: "trusted",
        activation_policy: "implicit",
        authorized_digest: null,
        requires_reauthorization: false,
      },
    },
    {
      market_id: "acme/meeting-brief",
      catalog_id: "agent-skill:acme:meeting-brief",
      name: "meeting-brief",
      title: "Meeting Brief",
      description: "Prepare concise meeting briefs.",
      version: "1.0.0",
      provider: "Acme",
      tags: ["meetings"],
      ontology_refs: ["Event"],
      surfaces: ["agent_context"],
      provenance: {
        source: "registry://acme/meeting-brief",
        digest: "sha256:market-meeting",
        verified: false,
      },
      install_state: "not_installed",
      authorization: {
        state: "quarantined",
        activation_policy: "none",
        authorized_digest: null,
        requires_reauthorization: false,
      },
    },
  ],
};

const externalInstructionSkill = {
  catalog_id: "agent-skill:acme:meeting-brief",
  kind: "skill",
  title: "Meeting Brief",
  description: "Prepare concise meeting briefs.",
  version: "1.0.0",
  provider: "Acme",
  tags: ["meetings"],
  ui_app_id: null,
  launch_mode: "details",
  surfaces: ["agent_context"],
  status: "ready",
  skill: {
    enabled: false,
    digest: "sha256:external-meeting",
    source: "registry://acme/meeting-brief",
    verified: false,
    installed_at: "2026-07-29T11:00:00Z",
    ontology_refs: ["Event"],
    registry_revision: 11,
    authorization: {
      state: "quarantined",
      activation_policy: "none",
      authorized_digest: null,
      requires_reauthorization: false,
      principal_id: "agent-skill:acme:meeting-brief",
      grant_digest: null,
    },
  },
};

describe("App Center", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(state) })
    );
  });

  it("searches and filters the unified catalog", async () => {
    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    await screen.findByText("Weather");
    fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "events" } });
    expect(screen.getByText("Calendar Tools")).toBeDefined();
    expect(screen.queryByText("Weather")).toBeNull();

    fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.getByText("No results found")).toBeDefined();
  });

  it("launches ready apps directly", async () => {
    const run = vi.fn();
    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={run}
        language="en"
      />
    );
    fireEvent.click(await screen.findByRole("button", { name: "Open Weather" }));
    expect(run).toHaveBeenCalledWith("weather");
  });

  it("opens capability details and requests UI generation", async () => {
    const close = vi.fn();
    render(
      <AppCenter
        isOpen
        onClose={close}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );
    fireEvent.click(await screen.findByRole("button", { name: "Open Calendar Tools" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate interface" }));
    expect(wsService.sendMessage).toHaveBeenCalledWith({
      type: "generate_capability_ui",
      catalog_id: "mcp:acme:calendar",
    });
    expect(close).toHaveBeenCalled();
  });

  it("opens details from the contextual management menu", async () => {
    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );
    await screen.findByText("Weather");
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather" }), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "View details" }));
    await waitFor(() => expect(screen.getByText("Local forecast")).toBeDefined());
  });

  it("opens the management menu by holding the app icon without launching it", async () => {
    vi.useFakeTimers();
    const run = vi.fn();
    try {
      const { container } = render(
        <AppCenter
          isOpen
          onClose={vi.fn()}
          pinnedWidgetIds={[]}
          onPinWidget={vi.fn()}
          onUnpinWidget={vi.fn()}
          onRunFullscreen={run}
          language="en"
        />
      );
      await act(async () => {});
      const tile = screen.getByRole("button", { name: "Open Weather" });
      const icon = container.querySelector('[data-app-icon="app:weather"]');
      expect(icon).not.toBeNull();

      fireEvent.pointerDown(icon!, { pointerId: 1, pointerType: "touch", clientX: 32, clientY: 48 });
      act(() => vi.advanceTimersByTime(520));
      fireEvent.pointerUp(icon!, { pointerId: 1, pointerType: "touch", clientX: 32, clientY: 48 });
      fireEvent.click(tile);

      expect(screen.getByRole("menu", { name: "Manage Weather" })).toBeDefined();
      expect(screen.getByRole("menuitem", { name: "Configure properties" })).toBeDefined();
      expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDefined();
      expect(screen.getByRole("menuitem", { name: "Uninstall app" })).toBeDefined();
      expect(run).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("cancels an icon hold when the pointer moves beyond the gesture tolerance", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <AppCenter
          isOpen
          onClose={vi.fn()}
          pinnedWidgetIds={[]}
          onPinWidget={vi.fn()}
          onUnpinWidget={vi.fn()}
          onRunFullscreen={vi.fn()}
          language="en"
        />
      );
      await act(async () => {});
      const icon = container.querySelector('[data-app-icon="app:weather"]');
      fireEvent.pointerDown(icon!, { pointerId: 1, pointerType: "touch", clientX: 32, clientY: 48 });
      fireEvent.pointerMove(icon!, { pointerId: 1, pointerType: "touch", clientX: 48, clientY: 48 });
      act(() => vi.advanceTimersByTime(520));
      expect(screen.queryByRole("menu", { name: "Manage Weather" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renames and configures generated app properties through partial updates", async () => {
    let currentState = structuredClone(state);
    const appUpdated = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/apps/weather") && init?.method === "PATCH") {
        const update = JSON.parse(String(init.body));
        const item = currentState.items.find((candidate) => candidate.catalog_id === "app:weather")!;
        Object.assign(item, {
          title: update.title ?? item.title,
          description: update.description ?? item.description,
          version: update.app_version ?? item.version,
          tags: update.intents ?? item.tags,
        });
        return { ok: true, status: 200, json: async () => ({
          id: "weather",
          title: item.title,
          description: item.description,
          app_version: item.version,
          intents: item.tags,
        }) } as Response;
      }
      return { ok: true, status: 200, json: async () => currentState } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        onAppUpdated={appUpdated}
        language="en"
      />
    );
    await screen.findByText("Weather");

    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather" }), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("App name"), { target: { value: "Weather Desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await screen.findByRole("button", { name: "Open Weather Desk" });

    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather Desk" }), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Configure properties" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Forecast dashboard" } });
    fireEvent.change(screen.getByLabelText("Version"), { target: { value: "2.0.0" } });
    fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "weather, local" } });
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH");
      expect(patchCalls).toHaveLength(2);
      expect(JSON.parse(String(patchCalls[0][1]?.body))).toEqual({ title: "Weather Desk" });
      expect(JSON.parse(String(patchCalls[1][1]?.body))).toEqual({
        description: "Forecast dashboard",
        app_version: "2.0.0",
        intents: ["weather", "local"],
      });
      expect(appUpdated).toHaveBeenCalledTimes(2);
      expect(appUpdated).toHaveBeenLastCalledWith("weather");
    });
  });

  it("keeps marketplace skills out of the installed launcher and installs or updates by market id", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/skill-market")) {
        return { ok: true, status: 200, json: async () => marketState } as Response;
      }
      if (url.endsWith("/api/skills/install") && init?.method === "POST") {
        return { ok: true, status: 200, json: async () => ({ status: "installed" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => skillState } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    expect(await screen.findByRole("button", { name: "View skill Research Notes" })).toBeDefined();
    expect(screen.queryByText("Meeting Brief")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Discover Skills" }));
    expect(await screen.findByText("Meeting Brief")).toBeDefined();
    expect(screen.getByText("Update available")).toBeDefined();
    expect(screen.getByText("Not verified")).toBeDefined();
    expect(screen.getByText(/External skills install disabled and quarantined/i)).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Install Meeting Brief" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/skills\/install$/),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          market_id: "acme/meeting-brief",
          expected_revision: 7,
        }),
      })
    ));

    fireEvent.click(screen.getByRole("button", { name: "Update Research Notes" }));
    await waitFor(() => {
      const installCalls = fetchMock.mock.calls.filter(([url, init]) =>
        String(url).endsWith("/api/skills/install") && init?.method === "POST"
      );
      expect(installCalls).toHaveLength(2);
      expect(JSON.parse(String(installCalls[1][1]?.body))).toEqual({
        market_id: "ambient/research-notes",
        expected_revision: 7,
      });
    });

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
  });

  it("shows catalog source health separately from package trust and pinned provenance", async () => {
    const commit = "a".repeat(40);
    const remoteMarket = {
      ...marketState,
      sources: [
        {
          id: "bundled",
          kind: "bundled",
          required: true,
          enabled: true,
          status: "available",
          entry_count: 1,
        },
        {
          id: "community-search",
          kind: "registry",
          required: false,
          enabled: true,
          status: "unavailable",
          entry_count: 0,
          error: "registry timed out",
        },
      ],
      items: [
        {
          ...marketState.items[1],
          catalog_source: {
            id: "anthropic-official",
            kind: "github",
            source_uri: `https://github.com/anthropics/skills/tree/${commit}/skills/meeting-brief`,
            source_revision: commit,
            upstream_hash: `sha256:${"b".repeat(64)}`,
            update_strategy: "content_hash",
          },
          package_compatibility: {
            profile: "context-only-v1",
            status: "compatible",
            reasons: [],
          },
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => ({
        ok: true,
        status: 200,
        json: async () => String(input).endsWith("/api/skill-market")
          ? remoteMarket
          : skillState,
      })),
    );

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    fireEvent.click(await screen.findByRole("tab", { name: "Discover Skills" }));
    expect(
      await screen.findByText("community-search is unavailable: registry timed out"),
    ).toBeDefined();
    expect(screen.getByText("GitHub · anthropic-official")).toBeDefined();
    expect(screen.getByText("context-only-v1")).toBeDefined();
    expect(screen.getByText("a".repeat(12))).toBeDefined();
    expect(screen.getByText("Not verified")).toBeDefined();
  });

  it("lets users disable and re-enable configured skill sources", async () => {
    let sourceEnabled = true;
    let revision = 7;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith("/api/skill-market/sources/anthropic-official")
        && init?.method === "PATCH"
      ) {
        sourceEnabled = JSON.parse(String(init.body)).enabled;
        revision += 1;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            source_id: "anthropic-official",
            enabled: sourceEnabled,
            revision,
          }),
        } as Response;
      }
      if (url.endsWith("/api/skill-market")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ...marketState,
            revision,
            sources: [
              {
                id: "anthropic-official",
                kind: "github",
                required: false,
                enabled: sourceEnabled,
                status: sourceEnabled ? "available" : "disabled",
                entry_count: sourceEnabled ? 1 : 0,
              },
            ],
            items: sourceEnabled ? [marketState.items[1]] : [],
          }),
        } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => skillState,
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    fireEvent.click(await screen.findByRole("tab", { name: "Discover Skills" }));
    const disable = await screen.findByRole("switch", {
      name: "Disable source anthropic-official",
    });
    expect(disable.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(disable);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/skill-market\/sources\/anthropic-official$/),
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ enabled: false, expected_revision: 7 }),
      }),
    ));
    const enable = await screen.findByRole("switch", {
      name: "Enable source anthropic-official",
    });
    expect(enable.getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByText("Meeting Brief")).toBeNull();

    fireEvent.click(enable);
    await screen.findByRole("switch", {
      name: "Disable source anthropic-official",
    });
    expect(await screen.findByText("Meeting Brief")).toBeDefined();
  });

  it("quarantines external skills and confirms digest-bound authorization or revocation", async () => {
    let enabled = false;
    let registryRevision = 11;
    let authorization = structuredClone(externalInstructionSkill.skill.authorization);
    const confirm = vi.spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValue(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          `/api/skills/${encodeURIComponent(externalInstructionSkill.catalog_id)}/authorization`,
        )
        && init?.method === "PATCH"
      ) {
        const update = JSON.parse(String(init.body));
        registryRevision += 1;
        enabled = update.activation_policy !== "none";
        authorization = update.activation_policy === "none"
          ? {
              ...authorization,
              state: "quarantined",
              activation_policy: "none",
              authorized_digest: null,
              requires_reauthorization: false,
              grant_digest: null,
            }
          : {
              ...authorization,
              state: "authorized",
              activation_policy: update.activation_policy,
              authorized_digest: externalInstructionSkill.skill.digest,
              requires_reauthorization: false,
              grant_digest: `sha256:grant-${update.activation_policy}`,
            };
        return {
          ok: true,
          status: 200,
          json: async () => ({ enabled, registry_revision: registryRevision, authorization }),
        } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          version: 1,
          revision: 4,
          items: [{
            ...externalInstructionSkill,
            skill: {
              ...externalInstructionSkill.skill,
              enabled,
              registry_revision: registryRevision,
              authorization,
            },
          }],
          root: [externalInstructionSkill.catalog_id],
          folders: [],
        }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    try {
      render(
        <AppCenter
          isOpen
          onClose={vi.fn()}
          pinnedWidgetIds={[]}
          onPinWidget={vi.fn()}
          onUnpinWidget={vi.fn()}
          onRunFullscreen={vi.fn()}
          language="en"
        />
      );

      fireEvent.click(await screen.findByRole("button", { name: "View skill Meeting Brief" }));
      expect(screen.getByText("Disabled and quarantined")).toBeDefined();
      expect(screen.getByText("External skill quarantined")).toBeDefined();
      expect(screen.getByRole("button", { name: "Authorize explicit /skill use" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Allow automatic matching" })).toBeDefined();
      expect(screen.queryByRole("button", { name: "Enable skill" })).toBeNull();
      expect(screen.queryByRole("button", { name: /Run in background/i })).toBeNull();
      expect(screen.queryByRole("button", { name: /Generate/i })).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Authorize explicit /skill use" }));
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(0);

      fireEvent.click(screen.getByRole("button", { name: "Authorize explicit /skill use" }));
      await screen.findByText("Authorized: explicit /skill only");
      expect(screen.getByText("agent-skill:acme:meeting-brief")).toBeDefined();
      expect(screen.getByText("sha256:grant-explicit_only")).toBeDefined();

      fireEvent.click(screen.getByRole("button", { name: "Allow automatic matching" }));
      await screen.findByText("Authorized: automatic matching");

      fireEvent.click(screen.getByRole("button", { name: "Revoke skill authorization" }));
      await screen.findByText("Disabled and quarantined");

      const authorizationCalls = fetchMock.mock.calls.filter(([url, init]) =>
        String(url).endsWith("/authorization") && init?.method === "PATCH"
      );
      expect(authorizationCalls.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
        {
          activation_policy: "explicit_only",
          expected_digest: "sha256:external-meeting",
          expected_revision: 11,
        },
        {
          activation_policy: "implicit",
          expected_digest: "sha256:external-meeting",
          expected_revision: 12,
        },
        {
          activation_policy: "none",
          expected_digest: "sha256:external-meeting",
          expected_revision: 13,
        },
      ]);
      expect(confirm).toHaveBeenCalledTimes(4);
    } finally {
      confirm.mockRestore();
    }
  });

  it("shows that a changed external skill digest must be authorized again", async () => {
    const changedSkill = {
      ...externalInstructionSkill,
      version: "1.1.0",
      skill: {
        ...externalInstructionSkill.skill,
        digest: "sha256:external-meeting-v2",
        registry_revision: 12,
        authorization: {
          ...externalInstructionSkill.skill.authorization,
          authorized_digest: "sha256:external-meeting",
          requires_reauthorization: true,
        },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          version: 1,
          revision: 4,
          items: [changedSkill],
          root: [changedSkill.catalog_id],
          folders: [],
        }),
      }),
    );

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    fireEvent.click(await screen.findByRole("button", { name: "View skill Meeting Brief" }));
    expect(screen.getByText("Content changed; reauthorization required")).toBeDefined();
    expect(screen.getByText("Content digest changed")).toBeDefined();
    expect(screen.getByText(/prior grant does not carry over/i)).toBeDefined();
    expect(screen.getByRole("button", { name: "Authorize explicit /skill use" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Allow automatic matching" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Revoke skill authorization" })).toBeDefined();
  });

  it("manages an installed instruction-only skill without offering actions or generated UI", async () => {
    let enabled = true;
    let installed = true;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/api/skills/${encodeURIComponent(instructionSkill.catalog_id)}`) && init?.method === "PATCH") {
        enabled = JSON.parse(String(init.body)).enabled;
        return { ok: true, status: 200, json: async () => ({ enabled }) } as Response;
      }
      if (url.includes(`/api/skills/${encodeURIComponent(instructionSkill.catalog_id)}?`) && init?.method === "DELETE") {
        installed = false;
        return { ok: true, status: 200, json: async () => ({ status: "ok" }) } as Response;
      }
      if (url.endsWith("/api/skill-market")) {
        return { ok: true, status: 200, json: async () => marketState } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => installed ? {
          ...skillState,
          items: [{ ...instructionSkill, skill: { ...instructionSkill.skill, enabled } }],
          root: [instructionSkill.catalog_id],
        } : { ...skillState, items: [], root: [] },
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    fireEvent.click(await screen.findByRole("button", { name: "View skill Research Notes" }));
    expect(screen.getByText("Available to agent")).toBeDefined();
    expect(screen.getByText("registry://ambient/research-notes")).toBeDefined();
    expect(screen.getByText("sha256:installed-research")).toBeDefined();
    expect(screen.getByText("Document")).toBeDefined();
    expect(screen.getByText("Note")).toBeDefined();
    expect(screen.queryByRole("button", { name: /Run in background/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Generate/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Disable skill" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(encodeURIComponent(instructionSkill.catalog_id)),
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ enabled: false, expected_revision: 7 }),
      })
    ));
    expect(await screen.findByRole("button", { name: "Enable skill" })).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Uninstall skill" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        `${encodeURIComponent(instructionSkill.catalog_id)}?expected_revision=7`,
      ),
      expect.objectContaining({ method: "DELETE" })
    ));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText("Research Notes")).toBeNull());
  });
});
