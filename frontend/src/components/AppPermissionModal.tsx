import React from "react";

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
  if (!pendingRequest) return null;

  const isMcp = pendingRequest.permission_type === "mcp_spawn";

  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/80 backdrop-blur-md">
      <div className="bg-[#0b0b0e] border border-white/10 p-6 rounded-xl max-w-md w-full mx-4 shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <h3 className="text-base font-semibold text-white mb-1.5 flex items-center gap-2">
          🛡️ 后端服务授权请求 ({pendingRequest.app_id})
        </h3>
        <p className="text-slate-400 text-xs mb-4 leading-relaxed">
          此 Widget 正在请求执行以下敏感后端操作。请确认是否允许：
        </p>
        <div className="bg-black/40 border border-white/5 rounded-lg p-3 mb-5 font-mono text-xs text-yellow-400 break-all select-all">
          <span className="text-slate-500 font-sans block mb-1">
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
        <div className="flex items-center justify-end gap-3 font-medium">
          <button
            onClick={() => onResolve(false)}
            className="px-3.5 py-1.5 rounded-lg text-slate-400 hover:bg-white/5 transition-colors text-xs"
          >
            拒绝 (Deny)
          </button>
          <button
            onClick={() => onResolve(true)}
            className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 transition-all text-white text-xs shadow-md shadow-yellow-600/10"
          >
            允许 (Allow)
          </button>
        </div>
      </div>
    </div>
  );
};
