import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RemoteWorkspaceDialog } from "../../frontend/src/components/RemoteWorkspace";
import * as remote from "../../frontend/src/services/remoteWorkspace";
import { getApiBaseUrl, getRemoteWorkspaceContext } from "../../frontend/src/services/apiBase";

vi.mock("../../frontend/src/services/remoteWorkspace", () => ({
  loadRemoteWorkspace: vi.fn(), pairRemoteWorkspace: vi.fn(),
  approveRemoteWorkspace: vi.fn(), revokeRemoteWorkspace: vi.fn(),
}));

const claimed = {
  status: "claimed", node_id: "node-one", name: "我的电脑", online: false,
  gateway_url: "http://localhost:8788", portal_url: "http://localhost:3001",
  scopes: ["workspace.control"], account_id: "account-one", account_label: "owner@example.test",
  grant_id: "grant-one", expires_at: "2099-01-01T00:00:00Z", pairing_code: "one-time-code",
  pairing_expires_at: "2099-01-01T00:00:00Z", workspace_origin: "http://node-one.localhost:8788",
  last_error: null,
};

beforeEach(() => { vi.resetAllMocks(); vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue(claimed); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete window.__AMBIENT_REMOTE__; });

describe("local remote workspace consent", () => {
  it("shows the claimed account and submits the exact displayed grant only after confirmation", async () => {
    vi.mocked(remote.approveRemoteWorkspace).mockResolvedValue({ ...claimed, status: "paired", online: true });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("owner@example.test");
    expect(remote.approveRemoteWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认允许此账户访问" }));
    await waitFor(() => expect(remote.approveRemoteWorkspace).toHaveBeenCalledWith({ account_id: "account-one", grant_id: "grant-one" }));
    await screen.findByText("已连接");
  });

  it("revokes from the local UI and removes the old authorization view", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ ...claimed, status: "paired", online: true });
    vi.mocked(remote.revokeRemoteWorkspace).mockResolvedValue({ ...claimed, status: "revoked", online: false, account_id: null, account_label: null });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("已连接");
    fireEvent.click(screen.getByRole("button", { name: "撤销远程访问" }));
    await screen.findByText("已撤销");
    expect(screen.queryByText("owner@example.test")).toBeNull();
  });

  it("creates an explicit bounded grant with management disabled by default", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ status: "disconnected", online: false, scopes: [] });
    vi.mocked(remote.pairRemoteWorkspace).mockResolvedValue({ ...claimed, status: "pending", account_id: null, account_label: null });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("尚未连接");
    fireEvent.change(screen.getByLabelText("平台网址"), { target: { value: "http://localhost:3001" } });
    fireEvent.change(screen.getByLabelText("连接地址"), { target: { value: "http://localhost:8788" } });
    fireEvent.change(screen.getByLabelText("这台电脑的名称"), { target: { value: "我的电脑" } });
    fireEvent.click(screen.getByRole("button", { name: "生成连接链接" }));
    await waitFor(() => expect(remote.pairRemoteWorkspace).toHaveBeenCalledWith({
      portal_url: "http://localhost:3001", gateway_url: "http://localhost:8788", name: "我的电脑",
      scopes: ["workspace.control"], expires_in: 86400,
    }));
    const link = await screen.findByRole("link", { name: "到平台领取连接" });
    expect(link.getAttribute("href")).toBe("http://localhost:3001/connect-workspace?code=one-time-code");
  });

  it.each([
    { language: "zh" as const, statusLabel: "连接记录无法读取", portalLabel: "平台网址", gatewayLabel: "连接地址", nameLabel: "这台电脑的名称", pairLabel: "生成连接链接", claimLabel: "到平台领取连接" },
    { language: "en" as const, statusLabel: "Saved connection cannot be read", portalLabel: "Platform URL", gatewayLabel: "Connection URL", nameLabel: "Computer name", pairLabel: "Create connection link", claimLabel: "Claim connection on the platform" },
  ])("permits explicit recovery from an invalid saved connection in $language", async (labels) => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({
      status: "invalid", online: false, scopes: [], last_error: "Saved connection cannot be read safely",
    });
    vi.mocked(remote.pairRemoteWorkspace).mockResolvedValue({ ...claimed, status: "pending", account_id: null, account_label: null });
    render(<RemoteWorkspaceDialog open language={labels.language} onClose={vi.fn()} />);
    await screen.findByText(labels.statusLabel);
    expect(remote.pairRemoteWorkspace).not.toHaveBeenCalled();
    expect(remote.approveRemoteWorkspace).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(labels.portalLabel), { target: { value: "http://localhost:3001" } });
    fireEvent.change(screen.getByLabelText(labels.gatewayLabel), { target: { value: "http://localhost:8788" } });
    fireEvent.change(screen.getByLabelText(labels.nameLabel), { target: { value: "Recovered computer" } });
    fireEvent.click(screen.getByRole("button", { name: labels.pairLabel }));
    await waitFor(() => expect(remote.pairRemoteWorkspace).toHaveBeenCalledWith({
      portal_url: "http://localhost:3001", gateway_url: "http://localhost:8788", name: "Recovered computer",
      scopes: ["workspace.control"], expires_in: 86400,
    }));
    const link = await screen.findByRole("link", { name: labels.claimLabel });
    expect(link.getAttribute("href")).toBe("http://localhost:3001/connect-workspace?code=one-time-code");
  });

  it("does not poll a closed dialog", () => {
    render(<RemoteWorkspaceDialog open={false} language="zh" onClose={vi.fn()} />);
    expect(remote.loadRemoteWorkspace).not.toHaveBeenCalled();
  });
});

describe("workspace gateway runtime configuration", () => {
  it("uses the visiting origin for both HTTP and websocket URL resolution", () => {
    window.__AMBIENT_REMOTE__ = { apiBaseUrl: "/", nodeId: "node-one" };
    expect(getRemoteWorkspaceContext()?.nodeId).toBe("node-one");
    expect(getApiBaseUrl()).toBe(window.location.origin);
  });
  it("rejects unsupported injected gateway routing", () => {
    window.__AMBIENT_REMOTE__ = { apiBaseUrl: "https://other.example", nodeId: "node-one" };
    expect(() => getRemoteWorkspaceContext()).toThrow();
  });
});
