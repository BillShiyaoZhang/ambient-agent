"""Isolated Chromium smoke verification for staged Widget artifacts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from backend.app_manifest import AppManifest
from backend.capabilities.policy import CapabilityAuthorizer
from backend.coding_agent_acp import CodingAgentStagedResult
from backend.graph_query_engine import execute_graph_query
from backend.widget_runtime import (
    RuntimeConnector,
    WidgetRuntimeBinding,
    WidgetRuntimeGateway,
)


class WidgetRuntimeSmokeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        runtime_code: str = "",
        classification: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.runtime_code = runtime_code
        self.classification = classification
        self.stage = "runtime_smoke"


class _StagedAppManager:
    def __init__(self, app: dict[str, Any]) -> None:
        self.app = app

    def get_app_files(self, app_id: str) -> dict[str, Any] | None:
        return self.app if self.app.get("id") == app_id else None


class WidgetRuntimeSmokeTester:
    """Starts the production Runtime protocol and requires an actual first frame."""

    def __init__(
        self,
        *,
        graph_db: Any,
        connector: RuntimeConnector | None = None,
        socket_path: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.graph_db = graph_db
        self.connector = connector
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _error_for_runtime(error: dict[str, Any]) -> WidgetRuntimeSmokeError:
        classification = str(error.get("classification") or "operator")
        runtime_code = str(error.get("code") or "widget_runtime_failed")
        message = str(error.get("message") or "Widget Runtime smoke test failed")
        if classification == "code_only":
            code = "widget_runtime_code_error"
        elif classification == "authorization_or_design":
            code = "design_change_required"
        elif classification == "abuse_or_budget":
            code = "widget_runtime_budget_exceeded"
        else:
            code = "widget_runtime_unavailable"
        return WidgetRuntimeSmokeError(
            message,
            code=code,
            runtime_code=runtime_code,
            classification=classification,
        )

    def _rpc_handler(self, manifest: AppManifest):
        authorizer = CapabilityAuthorizer(
            manifest_loader=lambda app_id: manifest if app_id == manifest.id else None,
            node_type_loader=lambda node_id: (
                str(node["type"])
                if (node := self.graph_db.get_node(node_id)) is not None and node.get("type")
                else None
            ),
        )

        async def handle(
            binding: WidgetRuntimeBinding,
            method: str,
            params: dict[str, Any],
        ) -> Any:
            if method == "graph.subscribe":
                query = params.get("query")
                if not isinstance(query, dict):
                    raise ValueError("graph.subscribe requires a query object")
                authorizer.authorize_graph_query(
                    binding.app_id,
                    query,
                    binding.manifest_revision,
                    binding.grants_digest,
                )
                return execute_graph_query(query, self.graph_db)
            if method == "graph.unsubscribe":
                return {"status": "ok"}
            if method == "graph.mutate":
                actions = params.get("actions")
                if not isinstance(actions, list):
                    raise ValueError("graph.mutate requires an actions array")
                authorizer.authorize_graph_mutation(
                    binding.app_id,
                    actions,
                    binding.manifest_revision,
                    binding.grants_digest,
                )
                return {"status": "validated", "actions": []}
            if method == "net.request":
                source_id = str(params.get("source_id") or "")
                request = params.get("request")
                if not isinstance(request, dict):
                    raise ValueError("net.request requires a request object")
                authorizer.authorize_network_request(
                    binding.app_id,
                    source_id,
                    path=request.get("path"),
                    method=str(request.get("method") or "GET"),
                    manifest_revision=binding.manifest_revision,
                    grants_digest=binding.grants_digest,
                )
                return {}
            if method in {"files.read", "files.list", "files.write", "files.delete"}:
                operation = method.removeprefix("files.")
                text = params.get("text")
                size = len(text.encode("utf-8")) if operation == "write" and isinstance(text, str) else None
                authorizer.authorize_file(
                    binding.app_id,
                    operation,
                    str(params.get("path") or ""),
                    size=size,
                    manifest_revision=binding.manifest_revision,
                    grants_digest=binding.grants_digest,
                )
                if operation == "read":
                    return ""
                if operation == "list":
                    return []
                return {"status": "validated"}
            if method == "capabilities.invoke":
                runtime_request_id = str(params.get("_runtime_request_id") or "")
                if not runtime_request_id or len(runtime_request_id) > 200:
                    raise ValueError("capabilities.invoke requires a bounded Runtime request ID")
                authorizer.authorize_invocation(
                    binding.app_id,
                    str(params.get("catalog_id") or ""),
                    str(params.get("action_id") or ""),
                    binding.manifest_revision,
                    binding.grants_digest,
                )
                return {"status": "succeeded", "result": {}}
            raise ValueError(f"Unsupported Widget Runtime smoke RPC method: {method}")

        return handle

    async def _preflight_dependencies(self, manifest: AppManifest) -> None:
        """Separate host dependency failures from generated Controller failures."""

        graph_query = next(
            (capability for capability in manifest.capabilities if capability.id == "graph.query"),
            None,
        )
        if graph_query is None:
            return
        entities = graph_query.scope.get("entities")
        query = {"type": entities[0]} if isinstance(entities, list) and entities else {}
        try:
            await asyncio.to_thread(execute_graph_query, query, self.graph_db)
        except Exception as exc:
            raise WidgetRuntimeSmokeError(
                f"Widget Runtime Graph dependency could not run: {exc!s}",
                code="widget_runtime_unavailable",
                runtime_code=type(exc).__name__,
                classification="operator",
            ) from exc

    async def verify(self, result: CodingAgentStagedResult) -> dict[str, Any]:
        staging_dir = Path(result.staging_dir)
        manifest = AppManifest.read(
            staging_dir / "manifest.json",
            expected_app_id=result.app_id,
        )
        await self._preflight_dependencies(manifest)
        source = (staging_dir / "controller.js").read_text(encoding="utf-8")
        app = {
            **manifest.to_dict(),
            "manifest_revision": manifest.revision,
            "grants_digest": manifest.grants_digest,
            "js": source,
        }
        gateway = WidgetRuntimeGateway(
            app_manager=_StagedAppManager(app),
            connector=self.connector,
            rpc_handler=self._rpc_handler(manifest),
            socket_path=self.socket_path,
        )
        binding: WidgetRuntimeBinding | None = None
        try:
            binding = await gateway.open_session(
                result.app_id,
                {"width": 640, "height": 480, "device_scale_factor": 1},
            )
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    message = await gateway.receive_runtime_message(binding.session_id)
                    projected = await gateway.handle_runtime_message(binding.session_id, message)
                    if projected is None or projected.get("type") == "ready":
                        continue
                    if projected.get("type") == "runtime_error":
                        raise self._error_for_runtime(projected["error"])
                    if projected.get("type") == "frame":
                        return {"status": "rendered", "frame": projected}
        except TimeoutError as exc:
            raise WidgetRuntimeSmokeError(
                "Widget Runtime did not produce a first frame before the smoke-test deadline",
                code="widget_runtime_unavailable",
                runtime_code="widget_runtime_smoke_timeout",
                classification="operator",
            ) from exc
        except WidgetRuntimeSmokeError:
            raise
        except Exception as exc:
            raise WidgetRuntimeSmokeError(
                f"Widget Runtime smoke test could not run: {exc!s}",
                code="widget_runtime_unavailable",
                runtime_code=type(exc).__name__,
                classification="operator",
            ) from exc
        finally:
            if binding is not None:
                await gateway.close_session(binding.session_id)
