import React from "react";
import { SystemDialog } from "./system/SystemUI";

interface AppPermissionModalProps {
  pendingRequest: {
    request_id: string;
    app_id: string;
    permission_type: string;
    value: any;
  } | null;
  onResolve: (approved: boolean) => void;
}

export const AppPermissionModal: React.FC<AppPermissionModalProps> = ({
  pendingRequest,
  onResolve,
}) => {
  const isMcp = pendingRequest?.permission_type === "mcp_spawn";

  return (
    <SystemDialog
      open={Boolean(pendingRequest)}
      blocking
      size="compact"
      title={`后端服务授权请求${pendingRequest ? ` (${pendingRequest.app_id})` : ""}`}
      description="此 Widget 正在请求执行敏感后端操作。请确认是否允许。"
    >
      {pendingRequest ? <div className="system-dialog-body">
        <div className="system-dialog-code">
          <span className="system-dialog-code-label">
            【类型: {isMcp ? "启动 MCP 服务" : "连接外部 Agent"}】
          </span>
          {isMcp ? (
            <>
              命令: {pendingRequest.value?.command?.join(" ")}<br />
              参数: {pendingRequest.value?.args?.join(" ")}
            </>
          ) : (
            <>URL: {pendingRequest.value?.agent_url}</>
          )}
        </div>
        <div className="system-dialog-actions">
          <button
            onClick={() => onResolve(false)}
            className="system-button is-danger"
          >
            拒绝 (Deny)
          </button>
          <button
            onClick={() => onResolve(true)}
            className="system-button is-primary"
          >
            允许 (Allow)
          </button>
        </div>
      </div> : null}
    </SystemDialog>
  );
};
