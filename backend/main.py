import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.app_data_sources import AppDataSourceError, AppDataSourceGateway
from backend.app_manager import AppManager
from backend.app_store import AppStoreService, CapabilityManifest, LayoutConflictError
from backend.capabilities.files import AppFileError, AppFileGateway
from backend.capabilities.catalog import SystemCapabilityCatalog
from backend.capabilities.policy import CapabilityAuthorizer, CapabilityDenied
from backend.coding_agent import (
    AgentModelConfig,
    CodingAgentConfigError,
    CodingAgentConfigStore,
    run_coding_agent,
)
from backend.coding_agent_runtime import CodingAgentRuntimeError, spec_for
from backend.models import ChatMessage, ChatSession
from backend.llm_config import LLMConfigError, LLMConfigStore, ModelSelection
from backend.llm_discovery import discover_models, test_provider
from backend.llm_service import set_default_llm_store
from backend.opencode_service import (
    cleanup_orphaned_opencode_staging,
    recover_interrupted_opencode_promotions,
    run_opencode_agent_acp,
)
from backend.run_service import ACTIVE_STATUSES, AgentRunState, RunCoordinator, RunStore
from backend.session_title import is_placeholder_title, sanitize_title
from backend.workspace_storage import WorkspaceStorage, migrate_old_data

# Global registry of active WebSockets mapping session_id -> Set[WebSocket]
active_websockets: dict[str, set[WebSocket]] = {}
legacy_run_projection_websockets: set[WebSocket] = set()

# Set of active session IDs currently running generation tasks
active_running_sessions: set[str] = set()


async def send_to_session(session_id: str, data: Any):
    """Sends JSON data to all active websockets connected to a specific session."""
    sockets = active_websockets.get(session_id, set())
    for ws in list(sockets):
        try:
            await ws.send_json(data)
        except Exception:
            pass


async def send_legacy_run_projection(session_id: str, data: Any):
    """Compatibility projection; the bundled frontend consumes /ws/runs."""

    sockets = active_websockets.get(session_id, set())
    for ws in list(sockets):
        if ws not in legacy_run_projection_websockets:
            continue
        try:
            await ws.send_json(data)
        except Exception:
            pass


async def broadcast_global(data: Any):
    """Sends JSON data to all connected websockets across all sessions."""
    for sockets in list(active_websockets.values()):
        for ws in list(sockets):
            try:
                await ws.send_json(data)
            except Exception:
                pass


app_manager = AppManager()

# Initialize workspace storage
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "workspace")
app_data_source_gateway = AppDataSourceGateway(app_manager, WORKSPACE_DIR)
db_storage = WorkspaceStorage(WORKSPACE_DIR)
llm_config_store = LLMConfigStore(WORKSPACE_DIR)
coding_agent_config_store = CodingAgentConfigStore(WORKSPACE_DIR)
set_default_llm_store(llm_config_store)
app_store = AppStoreService(WORKSPACE_DIR, app_manager)


def _system_capability_catalog() -> SystemCapabilityCatalog:
    from backend.agent.tools import ApprovalPolicy, registry as tool_registry

    model_tools = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "effect": spec.effect.value,
            "scopes": sorted(spec.scopes),
            "approval_required": spec.approval_policy == ApprovalPolicy.ALWAYS,
            "available": True,
        }
        for spec in tool_registry.gateway.specs()
    ]
    return SystemCapabilityCatalog.build(
        installed_capabilities=app_store.list_capabilities(),
        model_tools=model_tools,
        coding_agents=coding_agent_config_store.catalog(),
    )

from backend.graph_db import create_graph_database

graph_db = create_graph_database(WORKSPACE_DIR)


def _graph_node_type(node_id: str) -> str | None:
    node = graph_db.get_node(node_id)
    return str(node["type"]) if node and node.get("type") else None


capability_authorizer = CapabilityAuthorizer(
    manifest_loader=app_manager.get_manifest,
    node_type_loader=_graph_node_type,
)
app_file_gateway = AppFileGateway(app_manager)


def get_db():
    yield db_storage


from backend.backend_manager import BackendManager

backend_manager = BackendManager()
run_store = RunStore(WORKSPACE_DIR)
run_coordinator = RunCoordinator(run_store, app_store, app_manager, backend_manager)


async def _run_coding_agent_staged(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Any = None,
    promote: bool = True,
    coding_agent: str | None = None,
    coding_agent_model: dict[str, Any] | None = None,
):
    selected = coding_agent or coding_agent_config_store.get_settings()["default_agent"]
    # Keep the long-standing module injection point for local hosts and tests.
    if selected == "opencode":
        return await run_opencode_agent_acp(
            app_id,
            instruction,
            language=language,
            on_update=on_update,
            promote=promote,
        )
    return await run_coding_agent(
        app_id,
        instruction,
        language=language,
        on_update=on_update,
        promote=promote,
        coding_agent=selected,
        runtime=coding_agent_config_store.runtime,
        model_config=coding_agent_model or coding_agent_config_store.model_config(selected),
    )


durable_agent_workflow = DurableAgentWorkflow(
    workspace_dir=WORKSPACE_DIR,
    run_store=run_store,
    app_manager=app_manager,
    graph_db=graph_db,
    llm_config_store=lambda: llm_config_store,
    coding_agent_runner=_run_coding_agent_staged,
    event_sink=send_legacy_run_projection,
    app_diagnostic_loader=app_data_source_gateway.recent_diagnostics,
    capability_catalog_factory=_system_capability_catalog,
)
run_coordinator.register_internal_agent_executor(durable_agent_workflow)


def _snapshot_model_config(chat_session: ChatSession) -> dict[str, Any]:
    """Resolve and freeze the model choices used by a newly submitted Run."""

    settings = llm_config_store.get_settings()
    primary_data = chat_session.model_selection or settings.get("default_model")
    if not primary_data:
        return {}
    primary = ModelSelection.model_validate(primary_data)
    fast = ModelSelection.model_validate(settings.get("fast_model") or primary)
    llm_config_store.resolve(primary)
    llm_config_store.resolve(fast)
    coding_settings = coding_agent_config_store.get_settings()
    coding_agent = coding_settings["default_agent"]
    coding_config = coding_settings["agent_models"][coding_agent]
    coding_model: ModelSelection | None = None
    if coding_config["mode"] == "shared_binding":
        coding_model = (
            primary
            if coding_config.get("inherit") == "ambient.primary"
            else ModelSelection(
                provider_id=str(coding_config.get("provider_id") or ""),
                model_id=str(coding_config.get("model_id") or ""),
            )
        )
        llm_config_store.resolve(coding_model)
    return {
        "primary": primary.model_dump(mode="json"),
        "fast": fast.model_dump(mode="json"),
        "coding_agent": coding_agent,
        "coding_agent_config": coding_config,
        "coding_model": coding_model.model_dump(mode="json") if coding_model else None,
    }


def _active_chat_session_ids() -> set[str]:
    active = run_store.list_runs(status=",".join(sorted(ACTIVE_STATUSES)), limit=500)
    return {
        str(run["source_id"])
        for run in active
        if run.get("source_type") == "chat" and run.get("source_id") and run.get("status") != "needs_attention"
    }


async def _project_agent_run_status(run: dict[str, Any]) -> None:
    if run.get("adapter_type") != "internal_agent" or run.get("source_type") != "chat":
        return
    session_id = str(run.get("source_id") or "")
    if not session_id:
        return
    run_state = run.get("state") if isinstance(run.get("state"), dict) else {}
    state_data = run_state.get("data") if isinstance(run_state.get("data"), dict) else {}
    capability_catalog_id = state_data.get("capability_catalog_id")
    capability_app_id = state_data.get("capability_app_id")
    if capability_catalog_id and run.get("status") in {"succeeded", "failed", "cancelled", "needs_attention"}:
        app_store.generating_ids.discard(str(capability_catalog_id))
        if run.get("status") == "succeeded" and capability_app_id:
            app_store.bind_ui(str(capability_catalog_id), str(capability_app_id))
            await send_to_session(
                session_id,
                {
                    "type": "capability_ui_generation_completed",
                    "catalog_id": capability_catalog_id,
                    "app_id": capability_app_id,
                    "run_id": run["id"],
                },
            )
        else:
            error = run.get("error") or {}
            await send_to_session(
                session_id,
                {
                    "type": "capability_ui_generation_failed",
                    "catalog_id": capability_catalog_id,
                    "app_id": capability_app_id,
                    "run_id": run["id"],
                    "error": error.get("message") or run.get("summary") or run.get("status"),
                },
            )
    if run.get("status") in ACTIVE_STATUSES - {"needs_attention"}:
        if session_id not in active_running_sessions:
            active_running_sessions.add(session_id)
            await broadcast_global(
                {"type": "session_status_update", "session_id": session_id, "status": "running", "run_id": run["id"]}
            )
        return
    if session_id in _active_chat_session_ids():
        return
    active_running_sessions.discard(session_id)
    if run.get("status") in {"failed", "needs_attention"}:
        error = run.get("error") or {}
        code = error.get("code") or "agent_run_failed"
        payload_type = "llm_error" if str(code).startswith("llm_") else "error"
        await send_to_session(
            session_id,
            {
                "type": payload_type,
                "run_id": run["id"],
                "code": code,
                "message": error.get("message") or run.get("summary") or "Agent task failed",
                **({"action": "open_llm_settings"} if payload_type == "llm_error" else {}),
            },
        )
    await broadcast_global(
        {"type": "session_status_update", "session_id": session_id, "status": "idle", "run_id": run["id"]}
    )


run_coordinator.register_status_listener(_project_agent_run_status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Perform automated migration from db.sqlite3 and backend/apps to workspace
    migrate_old_data(WORKSPACE_DIR)
    active_running_sessions.clear()
    active_running_sessions.update(_active_chat_session_ids())
    db_storage.cleanup_audit_logs()
    try:
        recover_interrupted_opencode_promotions(app_manager.apps_dir)
    except (OSError, ValueError):
        pass
    staging_references: set[str] = set()
    for active_run in run_store.list_runs(status=",".join(sorted(ACTIVE_STATUSES)), limit=500):
        active_state = active_run.get("state") if isinstance(active_run.get("state"), dict) else {}
        active_data = active_state.get("data") if isinstance(active_state.get("data"), dict) else {}
        for handle_key in ("staged_app", "staged_app_cleanup_pending"):
            staged_app = active_data.get(handle_key) if isinstance(active_data.get(handle_key), dict) else {}
            if staged_app.get("staging_dir"):
                staging_references.add(str(staged_app["staging_dir"]))
    try:
        staging_grace = float(os.getenv("OPENCODE_STAGING_GRACE_SECONDS", "3600"))
        cleanup_orphaned_opencode_staging(
            app_manager.apps_dir,
            referenced_staging_paths=staging_references,
            grace_seconds=staging_grace,
        )
    except (OSError, ValueError):
        # Invalid cleanup configuration must never make startup delete more
        # aggressively; it simply disables this best-effort reaper pass.
        pass
    app_store.generating_ids.clear()
    for active_run in run_store.list_runs(status=",".join(sorted(ACTIVE_STATUSES)), limit=500):
        active_state = active_run.get("state") if isinstance(active_run.get("state"), dict) else {}
        active_data = active_state.get("data") if isinstance(active_state.get("data"), dict) else {}
        if active_data.get("capability_catalog_id"):
            app_store.generating_ids.add(str(active_data["capability_catalog_id"]))
    await run_coordinator.start()
    yield
    await run_coordinator.shutdown()
    await coding_agent_config_store.runtime.shutdown()
    await backend_manager.shutdown()


app = FastAPI(title="Ambient Agent API", lifespan=lifespan)

# Allow CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Ambient Agent is running"}


@app.get("/api/audit-logs")
async def get_audit_logs(session: WorkspaceStorage = Depends(get_db)):
    return session.get_audit_logs()


# --- Multi-Session REST endpoints ---


class SessionCreate(BaseModel):
    id: str
    title: str
    language: str = "zh"


class SessionUpdateLanguage(BaseModel):
    language: str


class ProviderCreateRequest(BaseModel):
    profile: dict[str, Any]
    credentials: dict[str, Any] = {}


class ProviderUpdateRequest(BaseModel):
    profile: dict[str, Any] = {}
    credentials: dict[str, Any] | None = None


class LLMSettingsUpdateRequest(BaseModel):
    default_model: ModelSelection | None = None
    fast_model: ModelSelection | None = None


class CodingAgentSettingsUpdateRequest(BaseModel):
    default_agent: str


class CodingAgentAuthRequest(BaseModel):
    method: str = "device_code"


class ProviderTestRequest(BaseModel):
    model_id: str | None = None
    mode: str = "connection"


def _llm_error_status(code: str) -> int:
    return {
        "llm_auth_failed": 401,
        "llm_model_not_found": 404,
        "llm_rate_limited": 429,
        "llm_timeout": 504,
        "llm_provider_error": 502,
        "llm_provider_in_use": 409,
        "llm_model_in_use": 409,
    }.get(code, 422)


@app.get("/api/sessions")
async def get_sessions(session: WorkspaceStorage = Depends(get_db)):
    return session.get_sessions()


@app.post("/api/sessions")
async def create_session(data: SessionCreate, session: WorkspaceStorage = Depends(get_db)):
    db_sess = session.get(ChatSession, data.id)
    if not db_sess:
        db_sess = ChatSession(id=data.id, title=data.title, language=data.language)
        session.add(db_sess)
        session.commit()
    return db_sess


@app.post("/api/sessions/{session_id}/language")
async def update_session_language(
    session_id: str, data: SessionUpdateLanguage, session: WorkspaceStorage = Depends(get_db)
):
    db_sess = session.get(ChatSession, session_id)
    if not db_sess:
        raise HTTPException(status_code=404, detail="Session not found")
    db_sess.language = data.language
    session.add(db_sess)
    session.commit()
    return db_sess


@app.put("/api/sessions/{session_id}/model")
async def update_session_model(session_id: str, data: ModelSelection, session: WorkspaceStorage = Depends(get_db)):
    try:
        llm_config_store.resolve(data)
    except LLMConfigError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    db_sess = session.get(ChatSession, session_id)
    if not db_sess:
        raise HTTPException(status_code=404, detail="Session not found")
    db_sess.model_selection = data
    session.add(db_sess)
    session.commit()
    payload = {"type": "session_model_updated", "session_id": session_id, "model_selection": data.model_dump()}
    await broadcast_global(payload)
    return payload


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, session: WorkspaceStorage = Depends(get_db)):
    return session.get_messages(session_id)


# --- LLM Provider Registry endpoints ---


@app.get("/api/coding-agents")
async def get_coding_agents():
    return {
        "agents": await coding_agent_config_store.runtime_catalog(),
        "settings": coding_agent_config_store.get_settings(),
    }


@app.patch("/api/coding-agents/settings")
async def update_coding_agent_settings(data: CodingAgentSettingsUpdateRequest):
    try:
        spec = spec_for(data.default_agent)
        status = await coding_agent_config_store.runtime.status(data.default_agent)
        if not status["installed"]:
            raise CodingAgentRuntimeError(
                "Install the coding agent before selecting it", code="coding_agent_not_installed"
            )
        if spec.auth_methods and not status["authenticated"]:
            raise CodingAgentRuntimeError(
                "Sign in to the coding agent before selecting it", code="coding_agent_auth_required"
            )
        return coding_agent_config_store.update_settings(data.model_dump())
    except (CodingAgentConfigError, CodingAgentRuntimeError) as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.patch("/api/coding-agents/{agent_id}/model")
async def update_coding_agent_model(agent_id: str, data: AgentModelConfig):
    try:
        if data.mode == "shared_binding" and not data.inherit:
            llm_config_store.resolve(ModelSelection(provider_id=data.provider_id or "", model_id=data.model_id or ""))
        return coding_agent_config_store.update_agent_model(agent_id, data.model_dump())
    except (CodingAgentConfigError, CodingAgentRuntimeError, LLMConfigError) as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/api/coding-agents/{agent_id}/install", status_code=202)
async def install_coding_agent(agent_id: str):
    try:
        return await coding_agent_config_store.runtime.start_install(agent_id)
    except CodingAgentRuntimeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/coding-agents/{agent_id}/operations/{operation_id}")
async def get_coding_agent_operation(agent_id: str, operation_id: str):
    try:
        return coding_agent_config_store.runtime.operation(agent_id, operation_id)
    except CodingAgentRuntimeError as exc:
        status_code = 404 if exc.code == "operation_not_found" else 422
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/api/coding-agents/{agent_id}/auth", status_code=202)
async def start_coding_agent_auth(agent_id: str, data: CodingAgentAuthRequest):
    try:
        return await coding_agent_config_store.runtime.start_auth(agent_id, data.method)
    except CodingAgentRuntimeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/coding-agents/{agent_id}/auth")
async def get_coding_agent_auth(agent_id: str):
    try:
        return coding_agent_config_store.runtime.auth_session(agent_id)
    except CodingAgentRuntimeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/coding-agents/{agent_id}/models")
async def get_coding_agent_models(agent_id: str):
    try:
        return await coding_agent_config_store.runtime.models(agent_id)
    except CodingAgentRuntimeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.delete("/api/coding-agents/{agent_id}/auth")
async def clear_coding_agent_auth(agent_id: str):
    try:
        status = await coding_agent_config_store.runtime.status(agent_id)
        if status["authenticated"]:
            return await coding_agent_config_store.runtime.logout(agent_id)
        return await coding_agent_config_store.runtime.cancel_auth(agent_id)
    except CodingAgentRuntimeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/llm/catalog")
async def get_llm_catalog():
    return llm_config_store.catalog()


@app.get("/api/llm/providers")
async def get_llm_providers():
    return llm_config_store.list_providers()


@app.post("/api/llm/providers", status_code=201)
async def create_llm_provider(data: ProviderCreateRequest):
    try:
        return llm_config_store.create_provider(data.profile, data.credentials)
    except LLMConfigError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.patch("/api/llm/providers/{provider_id}")
async def update_llm_provider(
    provider_id: str,
    data: ProviderUpdateRequest,
    session: WorkspaceStorage = Depends(get_db),
):
    try:
        if "models" in data.profile:
            existing = llm_config_store.get_provider(provider_id)
            next_ids = {str(item.get("id")) for item in (data.profile.get("models") or [])}
            removed_ids = {model.id for model in existing.models} - next_ids
            if any(
                chat.model_selection
                and chat.model_selection.provider_id == provider_id
                and chat.model_selection.model_id in removed_ids
                for chat in session.get_sessions()
            ):
                raise LLMConfigError("Model is referenced by a session", code="llm_model_in_use")
        return llm_config_store.update_provider(provider_id, data.profile, data.credentials)
    except LLMConfigError as exc:
        status = 404 if exc.code == "llm_provider_not_found" else 409 if exc.code == "llm_model_in_use" else 422
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc


@app.delete("/api/llm/providers/{provider_id}")
async def delete_llm_provider(provider_id: str, session: WorkspaceStorage = Depends(get_db)):
    if any(chat.model_selection and chat.model_selection.provider_id == provider_id for chat in session.get_sessions()):
        raise HTTPException(
            status_code=409,
            detail={"code": "llm_provider_in_use", "message": "Provider is referenced by a session"},
        )
    try:
        llm_config_store.delete_provider(provider_id)
    except LLMConfigError as exc:
        status = 409 if exc.code == "llm_provider_in_use" else 404
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc
    return {"status": "ok"}


@app.post("/api/llm/providers/{provider_id}/discover-models")
async def discover_llm_provider_models(provider_id: str):
    try:
        return {"models": await discover_models(llm_config_store, provider_id)}
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=_llm_error_status(exc.code), detail={"code": exc.code, "message": str(exc)}
        ) from exc


@app.post("/api/llm/providers/{provider_id}/test")
async def test_llm_provider(provider_id: str, data: ProviderTestRequest):
    try:
        return await test_provider(llm_config_store, provider_id, data.model_id, test_tools=data.mode == "tools")
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=_llm_error_status(exc.code), detail={"code": exc.code, "message": str(exc)}
        ) from exc


@app.get("/api/llm/settings")
async def get_llm_settings():
    return llm_config_store.get_settings()


@app.patch("/api/llm/settings")
async def update_llm_settings(data: LLMSettingsUpdateRequest):
    try:
        return llm_config_store.update_settings(data.model_dump(exclude_unset=True))
    except LLMConfigError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, session: WorkspaceStorage = Depends(get_db)):
    success = session.delete_session(session_id)
    if success:
        return {"status": "ok"}
    return {"status": "error", "message": "Session not found"}


# --- Canvas Config REST endpoints ---


class CanvasConfig(BaseModel):
    version: int | None = None
    open_app_ids: list[str] | None = None
    active_app_id: str | None = None
    windows: dict[str, Any] | None = None
    pinned_ids: list[str] | None = None
    widget_spans: dict[str, Any] | None = None


@app.get("/api/canvas")
async def get_canvas(session: WorkspaceStorage = Depends(get_db)):
    return session.get_canvas_config()


@app.post("/api/canvas")
async def save_canvas(data: CanvasConfig, session: WorkspaceStorage = Depends(get_db)):
    session.save_canvas_config(data.model_dump(exclude_none=True))
    return {"status": "ok"}


# --- AppStore REST endpoints ---


class AppStoreLayoutUpdate(BaseModel):
    revision: int
    root: list[str]
    folders: list[dict[str, Any]]


class RunCreate(BaseModel):
    catalog_id: str
    action_id: str | None = None
    input: Any = None
    source: dict[str, Any] | None = None
    idempotency_key: str | None = None
    parent_run_id: str | None = None


class RunInteractionResolve(BaseModel):
    response: Any


class RunEffectReconcile(BaseModel):
    resolution: str
    note: str | None = None


@app.post("/api/runs", status_code=202)
async def create_run(data: RunCreate):
    source = data.source or {}
    try:
        action_id = data.action_id
        if source.get("type") == "widget" and action_id is None:
            raise ValueError("Widget capability invocation requires an explicit action ID")
        if action_id is None:
            capability = app_store.get_capability(data.catalog_id)
            if capability is None:
                raise KeyError("Capability not found")
            action_id = capability.normalized_actions()[0].id
        if source.get("type") == "widget":
            app_id = str(source.get("id") or "")
            if not source.get("manifest_revision") or not source.get("grants_digest"):
                raise ValueError("Widget capability invocation requires a manifest revision and grants digest")
            capability_authorizer.authorize_invocation(
                app_id,
                data.catalog_id,
                action_id,
                str(source["manifest_revision"]) if source.get("manifest_revision") is not None else None,
                str(source["grants_digest"]),
            )
        return run_coordinator.submit(
            data.catalog_id,
            action_id,
            {} if data.input is None else data.input,
            source_type=str(source.get("type", "user")),
            source_id=str(source["id"]) if source.get("id") is not None else None,
            idempotency_key=data.idempotency_key,
            parent_run_id=data.parent_run_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CapabilityDenied as exc:
        raise HTTPException(status_code=403, detail=exc.to_dict()) from exc


@app.get("/api/runs")
async def list_runs(status: str | None = None, owner_id: str | None = None, limit: int = 100, offset: int = 0):
    return run_store.list_runs(status=status, owner_id=owner_id, limit=limit, offset=offset)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = run_store.get_run(run_id, include_events=True)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    try:
        return run_coordinator.cancel(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/reconcile")
async def reconcile_run_effect(run_id: str, data: RunEffectReconcile):
    try:
        return run_store.reconcile_effect(run_id, data.resolution, note=data.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: str):
    try:
        return run_coordinator.retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/run-interactions/{interaction_id}/resolve")
async def resolve_run_interaction(interaction_id: str, data: RunInteractionResolve):
    try:
        interaction = run_store.get_interaction(interaction_id)
        if interaction is None:
            raise KeyError(interaction_id)
        response = data.response if isinstance(data.response, dict) else {"approved": bool(data.response)}
        return run_coordinator.resolve_interaction(interaction_id, response)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Interaction not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/runtimes")
async def list_runtimes():
    return backend_manager.list_runtimes()


@app.post("/api/runtimes/{runtime_id}/stop")
async def stop_runtime(runtime_id: str):
    if run_store.has_active_runtime(runtime_id):
        raise HTTPException(status_code=409, detail="Runtime has active runs")
    if not await backend_manager.stop_runtime(runtime_id):
        raise HTTPException(status_code=404, detail="Managed runtime not found")
    return {"status": "stopped", "runtime_id": runtime_id}


@app.websocket("/ws/runs")
async def websocket_runs(websocket: WebSocket, after_sequence: int = 0, stream_epoch: str | None = None):
    await websocket.accept()
    sequence = max(0, after_sequence)
    stream = run_store.stream_info()
    if stream_epoch is not None and stream_epoch != stream["stream_epoch"]:
        await websocket.send_json(
            {
                "type": "run_stream_reset",
                "stream_epoch": stream["stream_epoch"],
                "latest_sequence": stream["latest_sequence"],
                "reason": "epoch_mismatch",
            }
        )
        await websocket.close()
        return
    if sequence > stream["latest_sequence"]:
        await websocket.send_json(
            {
                "type": "run_stream_reset",
                "stream_epoch": stream["stream_epoch"],
                "latest_sequence": stream["latest_sequence"],
                "reason": "sequence_rewound",
            }
        )
        await websocket.close()
        return
    await websocket.send_json(
        {
            "type": "run_stream_ready",
            "stream_epoch": stream["stream_epoch"],
            "latest_sequence": stream["latest_sequence"],
        }
    )
    idle_ticks = 0
    try:
        while True:
            events = run_store.events_after(sequence)
            for event in events:
                sequence = max(sequence, int(event["sequence"]))
                await websocket.send_json({"type": "run_event", "event": event})
            idle_ticks += 1
            if idle_ticks >= 40:
                await websocket.send_json(
                    {"type": "run_heartbeat", "sequence": sequence, "stream_epoch": stream["stream_epoch"]}
                )
                idle_ticks = 0
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.get("/api/app-store")
async def get_app_store():
    return app_store.get_state()


@app.put("/api/app-store/layout")
async def update_app_store_layout(data: AppStoreLayoutUpdate):
    try:
        return app_store.save_layout(data.revision, data.root, data.folders)
    except LayoutConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "App Store layout changed in another client", "state": exc.current},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/capabilities/{catalog_id}")
async def register_capability(catalog_id: str, data: CapabilityManifest):
    expected = app_store.catalog_id(data)
    if catalog_id != expected:
        raise HTTPException(status_code=400, detail=f"catalog id must be {expected}")
    return app_store.register_capability(data)


@app.delete("/api/capabilities/{catalog_id}/ui")
async def delete_capability_ui(catalog_id: str):
    if app_store.get_capability(catalog_id) is None:
        raise HTTPException(status_code=404, detail="Capability not found")
    app_id = app_store.unbind_ui(catalog_id)
    if app_id:
        if run_store.has_active_owner(f"app:{app_id}") or run_store.has_active_runtime(app_id):
            app_store.bind_ui(catalog_id, app_id)
            raise HTTPException(status_code=409, detail="Generated UI has active runs")
        app_manager.delete_app(app_id)
        app_store.on_app_deleted(app_id)
    return {"status": "ok", "catalog_id": catalog_id}


@app.delete("/api/capabilities/{catalog_id}")
async def unregister_capability(catalog_id: str):
    if run_store.has_active_owner(catalog_id):
        raise HTTPException(status_code=409, detail="Capability has active runs")
    if app_store.delete_capability(catalog_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Capability not found")


@app.get("/api/apps")
async def list_apps():
    return app_manager.list_apps()


@app.get("/api/apps/{app_id}")
async def get_app_files(app_id: str):
    try:
        files = app_manager.get_app_files(app_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if files:
        return files
    raise HTTPException(status_code=404, detail="App not found")


class AppDataSourceRequest(BaseModel):
    path: str
    method: str = "GET"
    query: dict[str, Any] | None = None
    body: Any = None
    manifest_revision: str
    grants_digest: str


@app.post("/api/apps/{app_id}/data-sources/{source_id}/request")
async def request_app_data_source(app_id: str, source_id: str, data: AppDataSourceRequest):
    try:
        result = await app_data_source_gateway.request(app_id, source_id, data.model_dump())
        return {"data": result}
    except AppDataSourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@app.get("/api/apps/{app_id}/diagnostics")
async def get_app_runtime_diagnostics(app_id: str):
    if app_manager.get_manifest(app_id) is None:
        raise HTTPException(status_code=404, detail="App not found")
    return {"diagnostics": app_data_source_gateway.recent_diagnostics(app_id)}


class AppFilePathRequest(BaseModel):
    path: str
    manifest_revision: str
    grants_digest: str


class AppFileWriteRequest(AppFilePathRequest):
    text: str


def _app_file_error(exc: AppFileError) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": "file_capability_denied", "message": str(exc)},
    )


@app.post("/api/apps/{app_id}/files/read")
async def read_app_file(app_id: str, data: AppFilePathRequest):
    try:
        return {
            "text": app_file_gateway.read_text(
                app_id,
                data.path,
                manifest_revision=data.manifest_revision,
                grants_digest=data.grants_digest,
            )
        }
    except AppFileError as exc:
        raise _app_file_error(exc) from exc


@app.post("/api/apps/{app_id}/files/list")
async def list_app_files(app_id: str, data: AppFilePathRequest):
    try:
        return {
            "files": app_file_gateway.list_files(
                app_id,
                data.path,
                manifest_revision=data.manifest_revision,
                grants_digest=data.grants_digest,
            )
        }
    except AppFileError as exc:
        raise _app_file_error(exc) from exc


@app.post("/api/apps/{app_id}/files/write")
async def write_app_file(app_id: str, data: AppFileWriteRequest):
    try:
        app_file_gateway.write_text(
            app_id,
            data.path,
            data.text,
            manifest_revision=data.manifest_revision,
            grants_digest=data.grants_digest,
        )
        return {"status": "ok"}
    except AppFileError as exc:
        raise _app_file_error(exc) from exc


@app.post("/api/apps/{app_id}/files/delete")
async def delete_app_file(app_id: str, data: AppFilePathRequest):
    try:
        app_file_gateway.delete(
            app_id,
            data.path,
            manifest_revision=data.manifest_revision,
            grants_digest=data.grants_digest,
        )
        return {"status": "ok"}
    except AppFileError as exc:
        raise _app_file_error(exc) from exc


@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str):
    if run_store.has_active_owner(f"app:{app_id}") or run_store.has_active_runtime(app_id):
        raise HTTPException(status_code=409, detail="App has active runs")
    try:
        success = app_manager.delete_app(app_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if success:
        app_store.on_app_deleted(app_id)
        return {"status": "ok"}
    return {"status": "error", "message": "App not found"}


# --- Graph Mutations endpoint ---


class GraphMutateRequest(BaseModel):
    actions: list[dict[str, Any]]
    session_id: str = "graph-api"
    idempotency_key: str | None = None
    manifest_revision: str | None = None
    grants_digest: str | None = None


async def _run_approved_graph_mutation(
    actions: list[dict[str, Any]],
    *,
    session_id: str,
    idempotency_key: str | None,
    title: str,
) -> dict[str, Any]:
    """Execute an explicit graph command through the durable reducer."""

    if not session_id or len(session_id) > 200:
        raise ValueError("session_id must be between 1 and 200 characters")
    # Tests and embedded hosts can replace the workspace GraphDatabase after
    # module import; the reducer must always use the current control-plane DB.
    durable_agent_workflow.graph_db = graph_db
    intent = IntentPlan(
        kind=IntentKind.GRAPH_MUTATION,
        confidence=1.0,
        rationale="explicit graph mutation command",
        actions=actions,
    )
    state = AgentRunState(
        workflow_type=IntentKind.GRAPH_MUTATION.value,
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id=session_id,
        phase="graph_preflight",
        intent=intent.to_dict(),
        data={"language": "zh", "explicit_command_approval": True},
    )
    run = run_coordinator.submit_internal_agent(
        owner_id=f"graph:{session_id}",
        action_id="mutate",
        title=title,
        session_id=session_id,
        input_data={"actions": actions},
        source_type="api",
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
        state=state,
        idempotency_key=idempotency_key,
    )

    deadline = asyncio.get_running_loop().time() + 30.0
    approved_interaction_id: str | None = None
    while True:
        current = run_store.get_run(run["id"])
        if current is None:
            raise RuntimeError("Durable graph Run disappeared")
        if current["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            return current
        if current["status"] == "waiting_user":
            pending = next(
                (
                    interaction
                    for interaction in current.get("interactions", [])
                    if interaction.get("status") == "pending" and interaction.get("type") == "graph_mutation_approval"
                ),
                None,
            )
            if pending and pending["id"] != approved_interaction_id:
                approved_interaction_id = pending["id"]
                run_coordinator.resolve_interaction(
                    pending["id"],
                    {"approved": True, "run_version": current["version"]},
                    expected_run_version=current["version"],
                )
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Durable graph Run {run['id']} did not finish in 30s")
        await asyncio.sleep(0.01)


@app.post("/api/graph/mutate")
async def mutate_graph(data: GraphMutateRequest):
    try:
        completed = await _run_approved_graph_mutation(
            data.actions,
            session_id=data.session_id,
            idempotency_key=data.idempotency_key,
            title="Graph mutation",
        )
        if completed["status"] != "succeeded":
            error = completed.get("error") or {}
            return {
                "status": "error",
                "run_id": completed["id"],
                "message": error.get("message", completed["status"]),
            }
        mutation = completed.get("result") or {}
        # Broadcast changes to all websocket subscribers
        from backend.graph_subscription import subscription_manager

        async def send_ws(ws, payload):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

        await subscription_manager.broadcast_updates(graph_db, send_ws, authorizer=capability_authorizer)
        return {
            "status": "success",
            "run_id": completed["id"],
            "ticket_id": mutation["ticket_id"],
            "actions": mutation["actions"],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/apps/{app_id}/graph/mutate")
async def mutate_app_graph(app_id: str, data: GraphMutateRequest):
    if not data.manifest_revision or not data.grants_digest:
        raise HTTPException(
            status_code=422,
            detail={"code": "capability_snapshot_required", "message": "Manifest revision and grants digest are required"},
        )
    try:
        capability_authorizer.authorize_graph_mutation(
            app_id,
            data.actions,
            manifest_revision=data.manifest_revision,
            grants_digest=data.grants_digest,
        )
    except CapabilityDenied as exc:
        raise HTTPException(status_code=403, detail=exc.to_dict()) from exc
    return await mutate_graph(data)


# --- WebSocket Chat Handler ---


@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str | None = None,
    projection: str = "legacy",
    session: WorkspaceStorage = Depends(get_db),
):
    await websocket.accept()

    if not session_id:
        session_id = "default-session"

    # Register websocket session mapping
    if session_id not in active_websockets:
        active_websockets[session_id] = set()
    active_websockets[session_id].add(websocket)
    if projection != "commands_only":
        legacy_run_projection_websockets.add(websocket)

    # Ensure session exists in DB
    db_session_obj = session.get(ChatSession, session_id)
    if not db_session_obj:
        db_session_obj = ChatSession(id=session_id, title="Active Chat")
        session.add(db_session_obj)
        session.commit()

    async def update_session_title(content: str) -> None:
        """Name a new session deterministically without an out-of-band model task."""

        current = session.get(ChatSession, session_id) or db_session_obj
        if not is_placeholder_title(current.title):
            return
        title = sanitize_title(" ".join(content.split()), current.language or "zh")
        if not title:
            return
        current.title = title
        session.add(current)
        session.commit()
        await broadcast_global({"type": "session_title_updated", "session_id": session_id, "title": title})

    async def submit_user_message(content_str: str, sender_str: str) -> dict[str, Any] | None:
        """Persist the command and enqueue the scheduler-owned workflow."""

        if not content_str.strip():
            await send_to_session(session_id, {"type": "error", "message": "Message content must not be empty"})
            return None

        user_msg = ChatMessage(session_id=session_id, role="user", sender=sender_str, content=content_str)
        session.add(user_msg)
        current_session = session.get(ChatSession, session_id) or db_session_obj
        current_session.updated_at = datetime.now(UTC)
        session.add(current_session)
        session.commit()
        session.refresh(user_msg)

        # Preserve the existing chat acknowledgement ordering. Execution is not
        # started by this socket; the durable scheduler claims the command.
        await send_to_session(
            session_id,
            {
                "type": "ack",
                "message": {
                    "id": user_msg.id,
                    "sender": user_msg.sender,
                    "role": user_msg.role,
                    "content": user_msg.content,
                    "timestamp": user_msg.timestamp.isoformat() if user_msg.timestamp else None,
                },
            },
        )

        model_snapshot = _snapshot_model_config(current_session)

        state = AgentRunState(
            workflow_type="agent_chat",
            workflow_version=DurableAgentWorkflow.VERSION,
            session_id=session_id,
            phase="route",
            model_snapshot=model_snapshot,
            data={
                "workspace_dir": session.workspace_dir,
                "user_message_id": user_msg.id,
            },
        )
        run = run_coordinator.submit_internal_agent(
            owner_id=f"ambient-agent:{session_id}",
            action_id="chat",
            title="Agent task",
            session_id=session_id,
            input_data={"content": content_str, "sender": sender_str, "user_message_id": user_msg.id},
            workflow_type=state.workflow_type,
            workflow_version=state.workflow_version,
            state=state,
            idempotency_key=f"chat:{session_id}:{user_msg.id}",
        )
        user_msg.run_id = run["id"]
        session.add(user_msg)
        session.commit()

        if session_id not in active_running_sessions:
            active_running_sessions.add(session_id)
            await broadcast_global(
                {
                    "type": "session_status_update",
                    "session_id": session_id,
                    "status": "running",
                    "run_id": run["id"],
                }
            )
        await update_session_title(content_str)
        return run

    # Restore connection state for the client
    try:
        # Rebuild projection from SQLite so reconnect behavior does not depend
        # on process-local task or Future registries.
        active_running_sessions.clear()
        active_running_sessions.update(_active_chat_session_ids())
        await websocket.send_json(
            {"type": "active_sessions_list", "active_session_ids": sorted(active_running_sessions)}
        )

        waiting_runs = run_store.list_runs(status="waiting_user", limit=500)
        for waiting_run in reversed(waiting_runs):
            if waiting_run.get("source_type") != "chat" or waiting_run.get("source_id") != session_id:
                continue
            full_run = run_store.get_run(waiting_run["id"]) or {}
            for interaction in full_run.get("interactions", []):
                if interaction.get("status") == "pending" and isinstance(interaction.get("payload"), dict):
                    await websocket.send_json(interaction["payload"])
    except Exception:
        pass

    async def submit_agent_msg(app_id, agent_msg_content):
        try:

            async def mirror_event(payload: dict[str, Any]):
                await websocket.send_json(payload)

            run = run_coordinator.submit_direct_agent_message(
                app_id,
                agent_msg_content,
                source_type="chat",
                source_id=session_id,
                event_callback=mirror_event,
            )

            async def project_completion(completed: dict[str, Any]) -> None:
                if completed["status"] != "succeeded":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "app_id": app_id,
                            "run_id": run["id"],
                            "message": (completed.get("error") or {}).get("message", completed["status"]),
                        }
                    )

            run_coordinator.register_completion_callback(run["id"], project_completion)
        except Exception as e:
            await websocket.send_json({"type": "error", "app_id": app_id, "message": str(e)})

    async def run_capability_ui_generation(catalog_id: str):
        capability = app_store.get_capability(catalog_id)
        if capability is None:
            await websocket.send_json(
                {"type": "capability_ui_generation_failed", "catalog_id": catalog_id, "error": "Not found"}
            )
            return
        if catalog_id in app_store.generating_ids:
            return
        app_id = app_store.generated_ui_app_id(catalog_id)
        app_store.generating_ids.add(catalog_id)
        try:
            descriptor = capability.model_dump(exclude_none=True)
            descriptor_hash = hashlib.sha256(
                json.dumps(
                    descriptor,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:16]
            instruction = (
                f"Create a polished, responsive UI for the installed capability '{capability.title}'. "
                f"The capability catalog id is '{catalog_id}'. Use "
                f"ambient.capabilities.invoke('{catalog_id}', input, '<approved-action-id>') for every capability action; "
                "the action ID must be an approved string literal. Do not call its provider directly. "
                "Build useful controls from the input schema. "
                f"Capability descriptor: {descriptor}"
            )
            current_session = session.get(ChatSession, session_id) or db_session_obj
            intent = IntentPlan(
                kind=IntentKind.WIDGET_CREATE,
                confidence=1.0,
                rationale="explicit capability UI generation",
                app_id=app_id,
                instruction=instruction,
            )
            state = AgentRunState(
                workflow_type=IntentKind.WIDGET_CREATE.value,
                workflow_version=DurableAgentWorkflow.VERSION,
                session_id=session_id,
                phase="plan",
                intent=intent.to_dict(),
                model_snapshot=_snapshot_model_config(current_session),
                data={
                    "workspace_dir": session.workspace_dir,
                    "language": current_session.language or "zh",
                    "capability_catalog_id": catalog_id,
                    "capability_app_id": app_id,
                },
            )
            run = run_coordinator.submit_internal_agent(
                owner_id=f"ambient-agent:{session_id}",
                action_id="generate_capability_ui",
                title=f"Generate UI for {capability.title}",
                session_id=session_id,
                input_data={"content": instruction, "catalog_id": catalog_id, "app_id": app_id},
                workflow_type=state.workflow_type,
                workflow_version=state.workflow_version,
                state=state,
                idempotency_key=f"capability-ui:{catalog_id}:{app_id}:{descriptor_hash}",
            )
            if run["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
                await _project_agent_run_status(run)
                return
            active_running_sessions.add(session_id)
            await broadcast_global(
                {
                    "type": "session_status_update",
                    "session_id": session_id,
                    "status": "running",
                    "run_id": run["id"],
                }
            )
            await send_to_session(
                session_id,
                {
                    "type": "capability_ui_generation_started",
                    "catalog_id": catalog_id,
                    "app_id": app_id,
                    "run_id": run["id"],
                },
            )
        except Exception as exc:
            app_store.generating_ids.discard(catalog_id)
            await send_to_session(
                session_id,
                {"type": "capability_ui_generation_failed", "catalog_id": catalog_id, "error": str(exc)},
            )

    async def resolve_durable_interaction(request_id: str | None, response: dict[str, Any]) -> bool:
        if not request_id:
            return False
        interaction = run_store.get_interaction(request_id)
        if interaction is None:
            return False
        interaction_run = run_store.get_run(interaction["run_id"])
        if not interaction_run:
            return False
        try:
            run_coordinator.resolve_interaction(request_id, response)
        except (KeyError, ValueError) as exc:
            await websocket.send_json(
                {
                    "type": "interaction_error",
                    "request_id": request_id,
                    "message": str(exc),
                }
            )
        return True

    async def reject_unknown_interaction(request_id: str | None) -> None:
        await websocket.send_json(
            {
                "type": "interaction_error",
                "request_id": request_id,
                "message": "Interaction is not pending in the durable Run store",
            }
        )

    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "backend_permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)

                if not await resolve_durable_interaction(request_id, {"approved": approved}):
                    await reject_unknown_interaction(request_id)

            elif msg_type == "ag_ui_message":
                app_id = data.get("app_id")
                agent_msg_content = data.get("message", {})
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    await submit_agent_msg(app_id, agent_msg_content)
            elif msg_type == "generate_capability_ui":
                await run_capability_ui_generation(data.get("catalog_id", ""))
            elif msg_type == "permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)

                if not await resolve_durable_interaction(request_id, {"approved": approved}):
                    await reject_unknown_interaction(request_id)
            elif msg_type == "schema_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")
                proposal = data.get("proposal", {})
                feedback = data.get("feedback", "")

                if not await resolve_durable_interaction(
                    request_id,
                    {"approved": approved_status, "proposal": proposal, "feedback": feedback},
                ):
                    await reject_unknown_interaction(request_id)
            elif msg_type == "plan_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")
                plan = data.get("plan", "")
                feedback = data.get("feedback", "")

                if not await resolve_durable_interaction(
                    request_id,
                    {"approved": approved_status, "plan": plan, "feedback": feedback},
                ):
                    await reject_unknown_interaction(request_id)
            elif msg_type == "verification_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")  # "approve", "rework_code", "rework_schema", "rework_plan"
                feedback = data.get("feedback", "")

                if not await resolve_durable_interaction(
                    request_id,
                    {"approved": approved_status, "feedback": feedback},
                ):
                    await reject_unknown_interaction(request_id)
            elif msg_type == "graph_subscribe":
                sub_id = data.get("subscription_id")
                query = data.get("query", {})
                from backend.graph_subscription import subscription_manager
                try:
                    if not data.get("manifest_revision") or not data.get("grants_digest"):
                        raise CapabilityDenied(
                            "capability_snapshot_required",
                            "Manifest revision and grants digest are required",
                            capability="manifest",
                            operation="load",
                        )
                    capability_authorizer.authorize_graph_query(
                        str(data.get("app_id") or ""),
                        query,
                        manifest_revision=data.get("manifest_revision"),
                        grants_digest=data.get("grants_digest"),
                    )
                    initial_res = subscription_manager.register(
                        websocket,
                        sub_id,
                        query,
                        graph_db,
                        app_id=str(data.get("app_id") or ""),
                        manifest_revision=data.get("manifest_revision"),
                        grants_digest=data.get("grants_digest"),
                    )
                    await websocket.send_json(
                        {"type": "graph_query_update", "subscription_id": sub_id, "data": initial_res}
                    )
                except CapabilityDenied as exc:
                    await websocket.send_json(
                        {"type": "graph_subscription_error", "subscription_id": sub_id, "error": exc.to_dict()}
                    )
            elif msg_type == "graph_unsubscribe":
                sub_id = data.get("subscription_id")
                from backend.graph_subscription import subscription_manager

                subscription_manager.unregister(websocket, sub_id)
            elif msg_type == "rollback_mutation":
                ticket_id = data.get("ticket_id")
                if ticket_id:
                    from backend.mutation_tickets import MutationTicketManager

                    mgr = MutationTicketManager(graph_db)
                    reverses = await mgr.rollback(session_id, ticket_id)
                    applied: list[str] = []
                    errors: list[str] = []
                    if not reverses:
                        errors.append("Rollback ticket has no complete inverse actions")
                    else:
                        try:
                            completed = await _run_approved_graph_mutation(
                                reverses,
                                session_id=session_id,
                                idempotency_key=f"rollback:{session_id}:{ticket_id}",
                                title="Graph mutation rollback",
                            )
                            if completed["status"] != "succeeded":
                                error = completed.get("error") or {}
                                raise RuntimeError(error.get("message", completed["status"]))
                            rollback_result = completed.get("result") or {}
                            applied.extend(
                                str(action.get("id") or f"{action.get('from_id')}->{action.get('to_id')}")
                                for action in rollback_result["actions"]
                            )
                        except Exception as e:
                            errors.append(str(e))

                    async def _send_ws(ws, payload):
                        try:
                            await ws.send_json(payload)
                        except Exception:
                            pass

                    from backend.graph_subscription import subscription_manager as _sub_mgr

                    await _sub_mgr.broadcast_updates(graph_db, _send_ws, authorizer=capability_authorizer)
                    await send_to_session(
                        session_id,
                        {
                            "type": "rollback_mutation_response",
                            "ticket_id": ticket_id,
                            "applied": applied,
                            "errors": errors,
                        },
                    )
            elif msg_type == "pin_mutation_history":
                ticket_id = data.get("ticket_id")
                if ticket_id:
                    from backend.mutation_tickets import MutationTicketManager

                    mgr = MutationTicketManager(graph_db)
                    await mgr.pin(session_id, ticket_id)
                    await send_to_session(
                        session_id,
                        {
                            "type": "pin_mutation_response",
                            "ticket_id": ticket_id,
                            "pinned": True,
                        },
                    )
            else:
                sender = data.get("sender", "user")
                content = data.get("content", "")
                await submit_user_message(content, sender)

    except WebSocketDisconnect:
        pass
    finally:
        legacy_run_projection_websockets.discard(websocket)
        if session_id in active_websockets:
            active_websockets[session_id].discard(websocket)
            if not active_websockets[session_id]:
                active_websockets.pop(session_id, None)
        from backend.graph_subscription import subscription_manager

        subscription_manager.unregister_all(websocket)
