import { useEffect, useRef, useState } from "react";
import { SystemDialog } from "./system/SystemUI";
import {
  approveRemoteWorkspace, loadRemoteWorkspace, pairRemoteWorkspace, revokeRemoteWorkspace,
  type RemoteWorkspaceStatus,
} from "../services/remoteWorkspace";
import "./RemoteWorkspace.css";

export function RemoteWorkspaceDialog({ open, language, onClose }: {
  open: boolean; language: "zh" | "en"; onClose: () => void;
}) {
  const zh = language === "zh";
  const [status, setStatus] = useState<RemoteWorkspaceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [portalUrl, setPortalUrl] = useState("");
  const [gatewayUrl, setGatewayUrl] = useState("");
  const [name, setName] = useState(zh ? "我的电脑" : "My computer");
  const [manage, setManage] = useState(false);
  const [duration, setDuration] = useState(86400);
  const epoch = useRef(0);
  const busyRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    const requestEpoch = epoch;
    let active = true;
    let loading = false;
    const refresh = async () => {
      if (loading || busyRef.current) return;
      loading = true;
      const current = epoch.current;
      try {
        const next = await loadRemoteWorkspace();
        if (active && current === epoch.current) { setStatus(next); setError(null); }
      } catch (reason) {
        if (active && current === epoch.current) setError(reason instanceof Error ? reason.message : String(reason));
      } finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => { active = false; window.clearInterval(timer); requestEpoch.current++; };
  }, [open]);

  const mutate = async (action: () => Promise<RemoteWorkspaceStatus>) => {
    if (busyRef.current) return;
    const current = ++epoch.current;
    busyRef.current = true; setBusy(true); setError(null);
    try {
      const next = await action();
      if (current === epoch.current) setStatus(next);
    } catch (reason) {
      if (current === epoch.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { busyRef.current = false; setBusy(false); }
  };

  const stateLabel = status?.status === "paired"
    ? status.online ? (zh ? "已连接" : "Connected") : (zh ? "连接暂不可用" : "Connection unavailable")
    : status?.status === "claimed" ? (zh ? "等待本机确认" : "Awaiting local confirmation")
    : status?.status === "pending" ? (zh ? "等待平台领取" : "Awaiting cloud claim")
    : status?.status === "revoked" ? (zh ? "已撤销" : "Revoked")
    : status?.status === "expired" ? (zh ? "授权已到期" : "Grant expired")
    : status?.status === "invalid" ? (zh ? "连接记录无法读取" : "Saved connection cannot be read")
    : status ? (zh ? "尚未连接" : "Not connected") : (zh ? "正在读取连接状态…" : "Loading connection…");
  let claimUrl: string | null = null;
  if (status?.pairing_code && status.portal_url && status.status === "pending") {
    try {
      const url = new URL("/connect-workspace", status.portal_url);
      url.searchParams.set("code", status.pairing_code);
      claimUrl = url.toString();
    } catch { /* The backend validates platform URLs; ignore malformed old state. */ }
  }
  const canPair = status && ["disconnected", "revoked", "expired", "invalid"].includes(status.status);

  return <SystemDialog open={open} title={zh ? "连接云平台" : "Connect a cloud platform"}
    description={zh ? "Agent 和工作区数据留在这台电脑，平台提供远程入口。" : "Agents and workspace data stay on this computer. The platform provides remote access."}
    onClose={onClose} size="medium">
    <div className="system-dialog-body remote-workspace-body">
      <p role="status" className="remote-workspace-status">{stateLabel}</p>
      {error ? <p role="alert" className="remote-workspace-error">{error}</p> : null}
      {status?.last_error && !error ? <p className="remote-workspace-error">{status.last_error}</p> : null}
      {canPair ? <form onSubmit={(event) => {
        event.preventDefault();
        void mutate(() => pairRemoteWorkspace({ portal_url: portalUrl.trim(), gateway_url: gatewayUrl.trim(),
          name: name.trim(), scopes: manage ? ["workspace.control", "workspace.manage"] : ["workspace.control"], expires_in: duration }));
      }}>
        <label>{zh ? "平台网址" : "Platform URL"}<input type="url" required value={portalUrl} onChange={(event) => setPortalUrl(event.target.value)} placeholder="https://platform.example.com" disabled={busy} /></label>
        <label>{zh ? "连接地址" : "Connection URL"}<input type="url" required value={gatewayUrl} onChange={(event) => setGatewayUrl(event.target.value)} placeholder="https://workspace.example.com" disabled={busy} /></label>
        <p className="remote-workspace-hint">{zh ? "平台会提供这两个地址。本地测试可使用 localhost。" : "Use the two addresses provided by the platform. Local tests can use localhost."}</p>
        <label>{zh ? "这台电脑的名称" : "Computer name"}<input required maxLength={64} value={name} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
        <label>{zh ? "允许访问的时间" : "Access duration"}<select value={duration} onChange={(event) => setDuration(Number(event.target.value))} disabled={busy}>
          <option value={3600}>{zh ? "1 小时" : "1 hour"}</option><option value={86400}>{zh ? "1 天" : "1 day"}</option><option value={604800}>{zh ? "7 天" : "7 days"}</option>
        </select></label>
        <p>{zh ? "允许查看和操作此工作区、与 Agent 对话及执行任务。" : "Allow viewing and operating this workspace, chatting with agents, and running tasks."}</p>
        <label className="remote-workspace-check"><input type="checkbox" checked={manage} onChange={(event) => setManage(event.target.checked)} disabled={busy} />{zh ? "同时允许管理模型、Coding Agent 和技能" : "Also allow model, coding-agent and skill administration"}</label>
        <button type="submit" disabled={busy}>{zh ? "生成连接链接" : "Create connection link"}</button>
      </form> : null}
      {claimUrl ? <div>
        <p>{zh ? "在平台登录并领取此连接，然后回到这里确认账户。" : "Sign in and claim this connection on the platform, then return here to confirm the account."}</p>
        <a href={claimUrl} target="_blank" rel="noopener noreferrer">{zh ? "到平台领取连接" : "Claim connection on the platform"}</a>
      </div> : null}
      {status?.account_id && ["claimed", "paired"].includes(status.status) ? <div className="remote-workspace-grant">
        <p>{zh ? "访问账户" : "Account"}: <strong>{status.account_label ?? status.account_id}</strong></p>
        <p>{status.scopes.includes("workspace.manage") ? (zh ? "范围：工作区操作与管理" : "Scope: workspace operation and administration") : (zh ? "范围：工作区操作；不包含模型、Coding Agent 和技能管理" : "Scope: workspace operation; model, coding-agent and skill administration excluded")}</p>
        {status.expires_at ? <p>{zh ? "到期时间" : "Expires"}: {new Date(status.expires_at).toLocaleString(zh ? "zh-CN" : "en-US")}</p> : null}
        {status.status === "claimed" && status.grant_id ? <button disabled={busy} onClick={() => {
          const account_id = status.account_id!; const grant_id = status.grant_id!;
          void mutate(() => approveRemoteWorkspace({ account_id, grant_id }));
        }}>{zh ? "确认允许此账户访问" : "Confirm access for this account"}</button> : null}
      </div> : null}
      {status && ["pending", "claimed", "paired"].includes(status.status) ? <button className="remote-workspace-revoke" disabled={busy} onClick={() => { void mutate(revokeRemoteWorkspace); }}>{zh ? "撤销远程访问" : "Revoke remote access"}</button> : null}
      <button className="remote-workspace-close" onClick={onClose}>{zh ? "关闭" : "Close"}</button>
    </div>
  </SystemDialog>;
}
