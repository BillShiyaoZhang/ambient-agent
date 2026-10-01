import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RemoteWorkspaceDialog } from "../../frontend/src/components/RemoteWorkspace";
import * as remote from "../../frontend/src/services/remoteWorkspace";
import { getApiBaseUrl, getRemoteWorkspaceContext } from "../../frontend/src/services/apiBase";

vi.mock("../../frontend/src/services/remoteWorkspace", async () => ({
  ...await vi.importActual<typeof import("../../frontend/src/services/remoteWorkspace")>("../../frontend/src/services/remoteWorkspace"),
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
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); delete window.__AMBIENT_REMOTE__; });

const enrollment = "synthetic-enrollment-token-one";
function fillPairForm(token = enrollment) {
  fireEvent.change(screen.getByLabelText("平台网址"), { target: { value: "http://localhost:3001" } });
  fireEvent.change(screen.getByLabelText("连接地址"), { target: { value: "http://localhost:8788" } });
  fireEvent.change(screen.getByLabelText("平台接入码"), { target: { value: token } });
}

describe("local remote workspace consent", () => {
  it("requires explicit until-revoked selection without adding management scope", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ status: "disconnected", online: false, scopes: [] });
    vi.mocked(remote.pairRemoteWorkspace).mockResolvedValue({ ...claimed, status: "pending", expires_at: "9999-01-01T00:00:00Z" });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("尚未连接");
    fillPairForm();
    fireEvent.change(screen.getByLabelText("允许访问的时间"), { target: { value: "until_revoked" } });
    expect(screen.getByText(/持续允许访问，直到你在本机或平台撤销/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "生成连接链接" }));
    await waitFor(() => expect(remote.pairRemoteWorkspace).toHaveBeenCalledWith({
      portal_url: "http://localhost:3001", gateway_url: "http://localhost:8788", name: "我的电脑",
      scopes: ["workspace.control"], expires_in: 86400, until_revoked: true, enrollment_token: enrollment,
    }));
  });

  it.each([
    { language: "zh" as const, duration: "直到撤销", notice: /持续允许访问，直到你在本机或平台撤销/, confirm: "确认允许此账户访问" },
    { language: "en" as const, duration: "Until revoked", notice: /Access continues until you revoke it locally or on the platform/, confirm: "Confirm access for this account" },
  ])("reviews the explicit long permission before approval in $language", async (labels) => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ ...claimed, expires_at: "9999-01-01T00:00:00Z" });
    vi.mocked(remote.approveRemoteWorkspace).mockResolvedValue({ ...claimed, status: "paired", online: true, expires_at: "9999-01-01T00:00:00Z" });
    render(<RemoteWorkspaceDialog open language={labels.language} onClose={vi.fn()} />);
    await screen.findByText(labels.duration);
    expect(screen.getByText(labels.notice)).toBeTruthy();
    expect(screen.queryByText(/9999/)).toBeNull();
    expect(remote.approveRemoteWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: labels.confirm }));
    await waitFor(() => expect(remote.approveRemoteWorkspace).toHaveBeenCalledWith({ account_id: "account-one", grant_id: "grant-one" }));
  });

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
    fireEvent.change(screen.getByLabelText("平台接入码"), { target: { value: ` ${enrollment} ` } });
    fireEvent.change(screen.getByLabelText("这台电脑的名称"), { target: { value: "我的电脑" } });
    fireEvent.click(screen.getByRole("button", { name: "生成连接链接" }));
    await waitFor(() => expect(remote.pairRemoteWorkspace).toHaveBeenCalledWith({
      portal_url: "http://localhost:3001", gateway_url: "http://localhost:8788", name: "我的电脑",
      scopes: ["workspace.control"], expires_in: 86400, enrollment_token: enrollment,
    }));
    const link = await screen.findByRole("link", { name: "到平台领取连接" });
    expect(link.getAttribute("href")).toBe("http://localhost:3001/connect-workspace?code=one-time-code");
  });

  it.each([
    { language: "zh" as const, statusLabel: "连接记录无法读取", portalLabel: "平台网址", gatewayLabel: "连接地址", enrollmentLabel: "平台接入码", nameLabel: "这台电脑的名称", pairLabel: "生成连接链接", claimLabel: "到平台领取连接" },
    { language: "en" as const, statusLabel: "Saved connection cannot be read", portalLabel: "Platform URL", gatewayLabel: "Connection URL", enrollmentLabel: "Platform enrollment code", nameLabel: "Computer name", pairLabel: "Create connection link", claimLabel: "Claim connection on the platform" },
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
    fireEvent.change(screen.getByLabelText(labels.enrollmentLabel), { target: { value: enrollment } });
    fireEvent.change(screen.getByLabelText(labels.nameLabel), { target: { value: "Recovered computer" } });
    fireEvent.click(screen.getByRole("button", { name: labels.pairLabel }));
    await waitFor(() => expect(remote.pairRemoteWorkspace).toHaveBeenCalledWith({
      portal_url: "http://localhost:3001", gateway_url: "http://localhost:8788", name: "Recovered computer",
      scopes: ["workspace.control"], expires_in: 86400, enrollment_token: enrollment,
    }));
    const link = await screen.findByRole("link", { name: labels.claimLabel });
    expect(link.getAttribute("href")).toBe("http://localhost:3001/connect-workspace?code=one-time-code");
  });

  it("does not poll a closed dialog", () => {
    render(<RemoteWorkspaceDialog open={false} language="zh" onClose={vi.fn()} />);
    expect(remote.loadRemoteWorkspace).not.toHaveBeenCalled();
  });

  it("links to explicit portal enrollment without putting the code in the URL", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ status: "disconnected", online: false, scopes: [] });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("尚未连接");
    fillPairForm();
    const input = screen.getByLabelText("平台接入码");
    expect(input.getAttribute("type")).toBe("password");
    expect(input.getAttribute("autocomplete")).toBe("off");
    expect(screen.getByText(/五分钟/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "到平台获取接入码" }).getAttribute("href"))
      .toBe("http://localhost:3001/dashboard/workspaces");
    expect(remote.pairRemoteWorkspace).not.toHaveBeenCalled();
  });

  it("clears the code after a failed attempt without exposing the exception or replaying", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ status: "disconnected", online: false, scopes: [] });
    vi.mocked(remote.pairRemoteWorkspace).mockRejectedValue(new Error(`upstream echoed ${enrollment}`));
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("尚未连接");
    fillPairForm();
    fireEvent.click(screen.getByRole("button", { name: "生成连接链接" }));
    await screen.findByRole("alert");
    expect((screen.getByLabelText("平台接入码") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("alert").textContent).not.toContain(enrollment);
    expect(remote.pairRemoteWorkspace).toHaveBeenCalledTimes(1);
  });

  it("clears an unsubmitted enrollment when the dialog closes", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ status: "disconnected", online: false, scopes: [] });
    const props = { language: "zh" as const, onClose: vi.fn() };
    const view = render(<RemoteWorkspaceDialog open {...props} />);
    await screen.findByText("尚未连接");
    fillPairForm();
    view.rerender(<RemoteWorkspaceDialog open={false} {...props} />);
    view.rerender(<RemoteWorkspaceDialog open {...props} />);
    await screen.findByText("尚未连接");
    expect((screen.getByLabelText("平台接入码") as HTMLInputElement).value).toBe("");
    expect(remote.pairRemoteWorkspace).not.toHaveBeenCalled();
  });

  it("does not treat a pending bound account as claimed or approved", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ ...claimed, status: "pending", account_label: null });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("等待平台领取");
    expect(screen.queryByRole("button", { name: "确认允许此账户访问" })).toBeNull();
    expect(screen.queryByText(/访问账户/)).toBeNull();
    expect(remote.approveRemoteWorkspace).not.toHaveBeenCalled();
  });

  it("waits for Retry-After and never automatically resubmits pairing", async () => {
    vi.useFakeTimers();
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ status: "disconnected", online: false, scopes: [] });
    vi.mocked(remote.pairRemoteWorkspace).mockRejectedValue(new remote.RemoteWorkspaceRequestError(429, 3));
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fillPairForm();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "生成连接链接" })); });
    const submit = screen.getByRole("button", { name: "生成连接链接" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect((screen.getByLabelText("平台接入码") as HTMLInputElement).value).toBe("");
    await act(async () => { await vi.advanceTimersByTimeAsync(2999); });
    expect(submit.disabled).toBe(true);
    expect(remote.pairRemoteWorkspace).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
    expect(submit.disabled).toBe(false);
    expect(remote.pairRemoteWorkspace).toHaveBeenCalledTimes(1);
  });

  it("keeps local revocation usable during a gateway cooldown", async () => {
    vi.mocked(remote.loadRemoteWorkspace).mockResolvedValue({ ...claimed, status: "paired", online: true, retry_after: 60 });
    vi.mocked(remote.revokeRemoteWorkspace).mockResolvedValue({ status: "revoked", online: false, scopes: [] });
    render(<RemoteWorkspaceDialog open language="zh" onClose={vi.fn()} />);
    await screen.findByText("已连接");
    const revoke = screen.getByRole("button", { name: "撤销远程访问" }) as HTMLButtonElement;
    expect(revoke.disabled).toBe(false);
    fireEvent.click(revoke);
    await screen.findByText("已撤销");
    expect(remote.revokeRemoteWorkspace).toHaveBeenCalledTimes(1);
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
