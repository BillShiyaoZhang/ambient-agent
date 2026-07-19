import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agent.harness import AgentOrchestrator
from backend.app_manager import AppManager
from backend.app_store import AppStoreService, CapabilityManifest, LayoutConflictError
from backend.models import ChatMessage, ChatSession
from backend.llm_config import LLMConfigError, LLMConfigStore, ModelSelection
from backend.llm_discovery import discover_models, test_provider
from backend.llm_runtime import use_model_selections
from backend.llm_service import set_default_llm_store
from backend.opencode_service import run_opencode_agent_acp
from backend.run_service import RunCoordinator, RunStore
from backend.session_title import SessionTitleService, is_placeholder_title
from backend.workspace_storage import WorkspaceStorage, migrate_old_data

# Global registry of active WebSockets mapping session_id -> Set[WebSocket]
active_websockets: dict[str, set[WebSocket]] = {}

# Registry of pending interactive requests: session_id -> dict of request_id -> request_payload
pending_requests: dict[str, dict[str, Any]] = {}

# Registry of latest status updates (Thinking/logs): session_id -> payload dict
latest_session_status: dict[str, Any] = {}

# Set of active session IDs currently running generation tasks
active_running_sessions: set[str] = set()
session_title_tasks: dict[str, asyncio.Task] = {}


async def send_to_session(session_id: str, data: Any):
    """Sends JSON data to all active websockets connected to a specific session."""
    sockets = active_websockets.get(session_id, set())
    for ws in list(sockets):
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
db_storage = WorkspaceStorage(WORKSPACE_DIR)
llm_config_store = LLMConfigStore(WORKSPACE_DIR)
set_default_llm_store(llm_config_store)
app_store = AppStoreService(WORKSPACE_DIR, app_manager)

from backend.graph_db import GraphDatabase

graph_db = GraphDatabase(WORKSPACE_DIR)


def get_db():
    yield db_storage


from backend.backend_manager import BackendManager

backend_manager = BackendManager()
run_store = RunStore(WORKSPACE_DIR)
run_coordinator = RunCoordinator(run_store, app_store, app_manager, backend_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Perform automated migration from db.sqlite3 and backend/apps to workspace
    migrate_old_data(WORKSPACE_DIR)
    await run_coordinator.start()
    yield
    await run_coordinator.shutdown()
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
async def update_session_model(
    session_id: str, data: ModelSelection, session: WorkspaceStorage = Depends(get_db)
):
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
    if any(
        chat.model_selection and chat.model_selection.provider_id == provider_id for chat in session.get_sessions()
    ):
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
        raise HTTPException(status_code=_llm_error_status(exc.code), detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/api/llm/providers/{provider_id}/test")
async def test_llm_provider(provider_id: str, data: ProviderTestRequest):
    try:
        return await test_provider(llm_config_store, provider_id, data.model_id, test_tools=data.mode == "tools")
    except LLMConfigError as exc:
        raise HTTPException(status_code=_llm_error_status(exc.code), detail={"code": exc.code, "message": str(exc)}) from exc


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


@app.post("/api/runs", status_code=202)
async def create_run(data: RunCreate):
    source = data.source or {}
    try:
        action_id = data.action_id
        if action_id is None:
            capability = app_store.get_capability(data.catalog_id)
            if capability is None:
                raise KeyError("Capability not found")
            action_id = capability.normalized_actions()[0].id
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
        payload = interaction.get("payload") or {}
        response = data.response if isinstance(data.response, dict) else {"approved": bool(data.response)}
        approved = response.get("approved", False)
        nested_request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        request_type = payload.get("type") or nested_request.get("type")
        if request_type == "plan_approval_request":
            from backend.agent.harness import active_plan_requests

            future = active_plan_requests.get(interaction_id)
            if future and not future.done():
                action = "refine" if approved == "refine" else "approve" if approved else "deny"
                value = response.get("plan", payload.get("plan", ""))
                if action == "refine":
                    value = {"plan": value, "feedback": response.get("feedback", "")}
                future.set_result((action, value))
        elif request_type == "schema_approval_request":
            from backend.agent.harness import active_schema_requests

            future = active_schema_requests.get(interaction_id)
            if future and not future.done():
                action = str(approved) if isinstance(approved, str) else "approve" if approved else "deny"
                future.set_result((action, response.get("proposal", payload.get("proposal", {}))))
        elif request_type == "verification_approval_request":
            from backend.agent.harness import active_verification_requests

            future = active_verification_requests.get(interaction_id)
            if future and not future.done():
                action = approved if isinstance(approved, str) else "approve" if approved else "deny"
                future.set_result((action, {"feedback": response.get("feedback", "")}))
        elif request_type == "backend_permission_request":
            backend_manager.resolve_permission(interaction_id, bool(approved))
        elif request_type == "permission_request":
            from backend.opencode_service import active_acp_clients

            for client in active_acp_clients.values():
                if interaction_id in client.pending_permissions:
                    client.resolve_permission(interaction_id, bool(approved))
                    break
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
async def websocket_runs(websocket: WebSocket, after_sequence: int = 0):
    await websocket.accept()
    sequence = max(0, after_sequence)
    idle_ticks = 0
    try:
        while True:
            events = run_store.events_after(sequence)
            for event in events:
                sequence = max(sequence, int(event["sequence"]))
                await websocket.send_json({"type": "run_event", "event": event})
            idle_ticks += 1
            if idle_ticks >= 40:
                await websocket.send_json({"type": "run_heartbeat", "sequence": sequence})
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


@app.post("/api/graph/mutate")
async def mutate_graph(data: GraphMutateRequest):
    try:
        for action in data.actions:
            act_type = action.get("action")
            if act_type == "create_node":
                graph_db.create_node(
                    node_id=action.get("id"),
                    node_type=action.get("type", "Generic"),
                    properties=action.get("properties"),
                )
            elif act_type == "update_node_property":
                graph_db.update_node_property(node_id=action.get("id"), properties=action.get("properties"))
            elif act_type == "delete_node":
                graph_db.delete_node(node_id=action.get("id"))
            elif act_type == "create_edge":
                graph_db.create_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type"),
                    properties=action.get("properties"),
                )
            elif act_type == "delete_edge":
                graph_db.delete_edge(
                    from_id=action.get("from_id"), to_id=action.get("to_id"), edge_type=action.get("type")
                )
        # Broadcast changes to all websocket subscribers
        from backend.graph_subscription import subscription_manager

        async def send_ws(ws, payload):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

        await subscription_manager.broadcast_updates(graph_db, send_ws)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- WebSocket Chat Handler ---


@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket, session_id: str | None = None, session: WorkspaceStorage = Depends(get_db)
):
    await websocket.accept()

    if not session_id:
        session_id = "default-session"

    # Register websocket session mapping
    if session_id not in active_websockets:
        active_websockets[session_id] = set()
    active_websockets[session_id].add(websocket)

    # Ensure session exists in DB
    db_session_obj = session.get(ChatSession, session_id)
    if not db_session_obj:
        db_session_obj = ChatSession(id=session_id, title="Active Chat")
        session.add(db_session_obj)
        session.commit()

    orchestrator = AgentOrchestrator(
        db_session=session, app_manager=app_manager, run_opencode_agent_acp_fn=run_opencode_agent_acp
    )
    title_service = SessionTitleService(session, config_store=llm_config_store)

    def start_title_generation(content: str) -> None:
        if session_id in session_title_tasks or not is_placeholder_title(db_session_obj.title):
            return

        async def generate_and_broadcast() -> None:
            try:
                title = await title_service.generate(session_id, content, db_session_obj.language or "zh")
                if title:
                    db_session_obj.title = title
                    await broadcast_global({"type": "session_title_updated", "session_id": session_id, "title": title})
            except Exception as exc:
                print("Error generating session title:", exc)
            finally:
                session_title_tasks.pop(session_id, None)

        session_title_tasks[session_id] = asyncio.create_task(generate_and_broadcast())

    # Callback to send incremental updates to client
    async def send_ws_update(data: Any):
        try:
            if isinstance(data, dict):
                payload = data
                # Capture pending requests
                if data.get("type") in ("schema_approval_request", "permission_request", "plan_approval_request"):
                    req_id = data.get("request_id")
                    if req_id:
                        if session_id not in pending_requests:
                            pending_requests[session_id] = {}
                        pending_requests[session_id][req_id] = data
            else:
                payload = {
                    "type": "reply",
                    "message": {
                        "id": -1,
                        "sender": "agent",
                        "role": "agent",
                        "content": data,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                }
                # Store latest status update
                latest_session_status[session_id] = payload

            await send_to_session(session_id, payload)
        except Exception:
            pass

    async def process_user_message(content_str: str, sender_str: str):
        run = run_coordinator.create_external_run(
            owner_id=f"ambient-agent:{session_id}",
            action_id="chat",
            title="Agent task",
            source_type="chat",
            source_id=session_id,
            input_data={"content": content_str, "sender": sender_str},
        )
        current_task = asyncio.current_task()
        if current_task is not None:
            run_coordinator.bind_external_task(run["id"], current_task)

        async def run_update(data: Any):
            event_type = data.get("type", "agent_update") if isinstance(data, dict) else "agent_update"
            run_store.append_event(run["id"], event_type, data)
            if isinstance(data, dict) and event_type in (
                "schema_approval_request",
                "permission_request",
                "plan_approval_request",
                "verification_approval_request",
            ):
                request_id = data.get("request_id")
                if request_id:
                    checkpoint_step = {
                        "plan_approval_request": "plan",
                        "schema_approval_request": "schema_alignment",
                        "permission_request": "execution_permission",
                        "verification_approval_request": "verification",
                    }[event_type]
                    if run_store.begin_step(run["id"], checkpoint_step):
                        run_store.finish_step(run["id"], checkpoint_step, {"request_id": request_id, "payload": data})
                    run_store.create_interaction(
                        run["id"], event_type.removesuffix("_request"), "Agent needs your input", data, request_id
                    )
                    current = run_store.get_run(run["id"])
                    if current and current["status"] == "running":
                        run_store.transition(run["id"], "waiting_user", summary="Waiting for your input")
            await send_ws_update(data)

        try:
            while run_coordinator.claim_external(run["id"]) is None:
                current = run_store.get_run(run["id"])
                if current is None or current["status"] == "cancelled":
                    return
                await asyncio.sleep(0.05)
            run_store.begin_step(run["id"], "agent_orchestration")
            # Save user message to database (committed immediately so we get the ID for ack)
            user_msg = ChatMessage(session_id=session_id, role="user", sender=sender_str, content=content_str)
            session.add(user_msg)

            # Reload before every run so a model change from another client is not
            # overwritten by the session object captured when the socket opened.
            current_session = session.get(ChatSession, session_id) or db_session_obj
            current_session.updated_at = datetime.now(UTC)
            session.add(current_session)
            session.commit()
            session.refresh(user_msg)

            # Send acknowledgement back to client
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
            # Mark session as running and broadcast globally
            active_running_sessions.add(session_id)
            await broadcast_global({"type": "session_status_update", "session_id": session_id, "status": "running"})

            # Resolve once when the run starts. Context variables keep concurrent
            # sessions isolated and ensure UI changes affect only the next run.
            settings = llm_config_store.get_settings()
            primary_data = current_session.model_selection or settings.get("default_model")
            if not primary_data:
                raise LLMConfigError(
                    "Configure a default model before starting a task",
                    code="llm_configuration_required",
                )
            primary_snapshot = ModelSelection.model_validate(primary_data)
            fast_snapshot = ModelSelection.model_validate(settings.get("fast_model") or primary_snapshot)
            llm_config_store.resolve(primary_snapshot)
            llm_config_store.resolve(fast_snapshot)
            with use_model_selections(primary_snapshot, fast_snapshot):
                agent_msg, widget_to_send = await orchestrator.handle_message(
                    session_id=session_id, content=content_str, on_update=run_update
                )

            # Send the final agent explanation/execution log back to client
            await send_to_session(
                session_id,
                {
                    "type": "reply",
                    "message": {
                        "id": agent_msg.id,
                        "sender": agent_msg.sender,
                        "role": agent_msg.role,
                        "content": agent_msg.content,
                        "timestamp": agent_msg.timestamp.isoformat() if agent_msg.timestamp else None,
                    },
                },
            )

            # Send widget creation/update to frontend
            if widget_to_send:
                try:
                    await send_to_session(session_id, {"type": "widget", "widget": widget_to_send})
                except Exception:
                    pass
            current = run_store.get_run(run["id"])
            if current and current["status"] == "cancel_requested":
                run_store.finish_step(run["id"], "agent_orchestration", status="cancelled")
                run_store.transition(run["id"], "cancelled", summary="Cancelled")
            elif current and current["status"] not in {"succeeded", "failed", "cancelled"}:
                if current["status"] == "waiting_user":
                    run_store.transition(run["id"], "running", summary="Finishing")
                result = {"message": agent_msg.content, "app_id": widget_to_send.get("id") if widget_to_send else None}
                run_store.finish_step(run["id"], "agent_orchestration", result)
                run_store.transition(
                    run["id"],
                    "succeeded",
                    summary="Agent task completed",
                    result=result,
                    artifacts=[{"type": "app", "id": widget_to_send.get("id")}] if widget_to_send else [],
                )
        except asyncio.CancelledError:
            current = run_store.get_run(run["id"])
            if current and current["status"] == "cancel_requested":
                run_store.finish_step(run["id"], "agent_orchestration", status="cancelled")
                run_store.transition(run["id"], "cancelled", summary="Cancelled")
            raise
        except LLMConfigError as e:
            error_payload = {
                "type": "llm_error",
                "code": e.code,
                "message": str(e),
                "action": "open_llm_settings",
            }
            await send_to_session(session_id, error_payload)
            current = run_store.get_run(run["id"])
            if current and current["status"] not in {"succeeded", "failed", "cancelled"}:
                run_store.finish_step(run["id"], "agent_orchestration", error_payload, status="failed")
                if current["status"] == "waiting_user":
                    run_store.transition(run["id"], "running", summary="LLM configuration required")
                run_store.transition(
                    run["id"],
                    "failed",
                    summary="LLM configuration required",
                    error={"code": e.code, "message": str(e)},
                )
        except Exception as e:
            print("Error in process_user_message:", e)
            current = run_store.get_run(run["id"])
            if current and current["status"] not in {"succeeded", "failed", "cancelled"}:
                run_store.finish_step(run["id"], "agent_orchestration", {"message": str(e)}, status="failed")
                if current["status"] == "waiting_user":
                    run_store.transition(run["id"], "running", summary="Agent task failed")
                run_store.transition(
                    run["id"], "failed", summary="Agent task failed", error={"message": str(e)}
                )
        finally:
            # Clean up pending requests, latest status, and active session flag
            pending_requests.pop(session_id, None)
            latest_session_status.pop(session_id, None)
            active_running_sessions.discard(session_id)
            await broadcast_global({"type": "session_status_update", "session_id": session_id, "status": "idle"})
            start_title_generation(content_str)

    # Restore connection state for the client
    try:
        # Send all active running sessions
        await websocket.send_json({"type": "active_sessions_list", "active_session_ids": list(active_running_sessions)})

        # Send latest status/logs if session is running
        if session_id in latest_session_status:
            await websocket.send_json(latest_session_status[session_id])

        # Send any pending requests
        if session_id in pending_requests:
            for req_data in pending_requests[session_id].values():
                await websocket.send_json(req_data)
    except Exception:
        pass

    async def run_agent_msg(app_id, manifest, agent_msg_content):
        try:

            async def send_ws_payload(payload):
                await websocket.send_json(payload)

            await backend_manager.handle_agent_message(
                app_id=app_id, manifest=manifest, message=agent_msg_content, send_ws_message_func=send_ws_payload
            )
        except Exception as e:
            await websocket.send_json({"type": "error", "app_id": app_id, "message": str(e)})

    async def run_mcp_call(app_id, manifest, tool_name, arguments, call_id):
        try:
            async def mirror_event(payload: dict[str, Any]):
                await websocket.send_json(payload)

            run = run_coordinator.submit_direct_mcp(
                app_id,
                tool_name,
                arguments,
                source_type="app",
                source_id=app_id,
                event_callback=mirror_event,
            )
            completed = await run_coordinator.wait_terminal(run["id"])
            if completed["status"] == "succeeded":
                await websocket.send_json(
                    {
                        "type": "mcp_call_response",
                        "app_id": app_id,
                        "call_id": call_id,
                        "run_id": run["id"],
                        "result": completed.get("result"),
                    }
                )
            else:
                raise RuntimeError((completed.get("error") or {}).get("message", completed["status"]))
        except Exception as e:
            await websocket.send_json(
                {"type": "mcp_call_response", "app_id": app_id, "call_id": call_id, "error": str(e)}
            )

    async def run_mcp_read(app_id, manifest, uri, call_id):
        try:

            async def send_ws_payload(payload):
                if payload.get("type") == "backend_permission_request":
                    req_id = payload.get("request_id")
                    if req_id:
                        if session_id not in pending_requests:
                            pending_requests[session_id] = {}
                        pending_requests[session_id][req_id] = payload
                await websocket.send_json(payload)

            client = await backend_manager.get_or_start_mcp_client(
                app_id=app_id, manifest=manifest, send_ws_message_func=send_ws_payload
            )
            if client:
                result = await client.call("resources/read", {"uri": uri})
                await websocket.send_json(
                    {"type": "mcp_read_response", "app_id": app_id, "call_id": call_id, "result": result}
                )
        except Exception as e:
            await websocket.send_json(
                {"type": "mcp_read_response", "app_id": app_id, "call_id": call_id, "error": str(e)}
            )

    async def run_capability_invoke(catalog_id: str, input_data: Any, call_id: str, action_id: str | None = None):
        try:
            capability = app_store.get_capability(catalog_id)
            if capability is None:
                raise ValueError("Capability does not expose an invocation adapter")
            actions = capability.normalized_actions()
            selected_action = action_id or actions[0].id
            run = run_coordinator.submit(
                catalog_id,
                selected_action,
                input_data if isinstance(input_data, dict) else {"input": input_data},
                source_type="app",
                source_id=session_id,
            )
            completed = await run_coordinator.wait_terminal(run["id"])
            if completed["status"] != "succeeded":
                raise RuntimeError((completed.get("error") or {}).get("message", completed["status"]))
            await websocket.send_json(
                {
                    "type": "capability_call_response",
                    "catalog_id": catalog_id,
                    "call_id": call_id,
                    "run_id": run["id"],
                    "result": completed.get("result"),
                }
            )
        except Exception as exc:
            await websocket.send_json(
                {
                    "type": "capability_call_response",
                    "catalog_id": catalog_id,
                    "call_id": call_id,
                    "error": str(exc),
                }
            )

    async def run_capability_ui_generation(catalog_id: str):
        capability = app_store.get_capability(catalog_id)
        if capability is None:
            await websocket.send_json(
                {"type": "capability_ui_generation_failed", "catalog_id": catalog_id, "error": "Not found"}
            )
            return
        if catalog_id in app_store.generating_ids:
            return
        if session_id in active_running_sessions:
            await websocket.send_json(
                {
                    "type": "capability_ui_generation_failed",
                    "catalog_id": catalog_id,
                    "error": "The current session is already running another task",
                }
            )
            return
        app_id = app_store.generated_ui_app_id(catalog_id)
        app_store.generating_ids.add(catalog_id)
        active_running_sessions.add(session_id)
        await broadcast_global({"type": "session_status_update", "session_id": session_id, "status": "running"})
        await send_to_session(
            session_id,
            {"type": "capability_ui_generation_started", "catalog_id": catalog_id, "app_id": app_id},
        )
        try:
            descriptor = capability.model_dump(exclude_none=True)
            instruction = (
                f"Create a polished, responsive UI for the installed capability '{capability.title}'. "
                f"The capability catalog id is '{catalog_id}'. Use ambient.capabilities.invoke('{catalog_id}', input) "
                "for every capability action; do not call its provider directly. Build useful controls from the input schema. "
                f"Capability descriptor: {descriptor}"
            )
            _, widget = await orchestrator.generate_capability_ui(
                session_id=session_id,
                app_id=app_id,
                instruction=instruction,
                on_update=send_ws_update,
            )
            if not widget:
                raise RuntimeError("UI generation completed without an app")
            app_store.bind_ui(catalog_id, app_id)
            await send_to_session(session_id, {"type": "widget", "widget": widget})
            await send_to_session(
                session_id,
                {"type": "capability_ui_generation_completed", "catalog_id": catalog_id, "app_id": app_id},
            )
        except Exception as exc:
            await send_to_session(
                session_id,
                {"type": "capability_ui_generation_failed", "catalog_id": catalog_id, "error": str(exc)},
            )
        finally:
            app_store.generating_ids.discard(catalog_id)
            active_running_sessions.discard(session_id)
            await broadcast_global({"type": "session_status_update", "session_id": session_id, "status": "idle"})

    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "backend_permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                backend_manager.resolve_permission(request_id, approved)
                if run_store.get_interaction(request_id):
                    run_coordinator.resolve_interaction(request_id, {"approved": approved})
            elif msg_type == "ag_ui_message":
                app_id = data.get("app_id")
                agent_msg_content = data.get("message", {})
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    asyncio.create_task(run_agent_msg(app_id, manifest, agent_msg_content))
            elif msg_type == "mcp_call_tool":
                app_id = data.get("app_id")
                tool_name = data.get("name")
                arguments = data.get("arguments", {})
                call_id = data.get("call_id")
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    asyncio.create_task(run_mcp_call(app_id, manifest, tool_name, arguments, call_id))
            elif msg_type == "mcp_read_resource":
                app_id = data.get("app_id")
                uri = data.get("uri")
                call_id = data.get("call_id")
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    asyncio.create_task(run_mcp_read(app_id, manifest, uri, call_id))
            elif msg_type == "capability_invoke":
                asyncio.create_task(
                    run_capability_invoke(
                        data.get("catalog_id", ""),
                        data.get("input", {}),
                        data.get("call_id", ""),
                        data.get("action_id"),
                    )
                )
            elif msg_type == "generate_capability_ui":
                asyncio.create_task(run_capability_ui_generation(data.get("catalog_id", "")))
            elif msg_type == "permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                from backend.opencode_service import active_acp_clients

                resolved = False
                for client in active_acp_clients.values():
                    if request_id in client.pending_permissions:
                        client.resolve_permission(request_id, approved)
                        resolved = True
                if not resolved:
                    print(f"Warning: permission request {request_id} not found in active clients.")
                if run_store.get_interaction(request_id):
                    run_coordinator.resolve_interaction(request_id, {"approved": approved})
            elif msg_type == "schema_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")
                proposal = data.get("proposal", {})
                feedback = data.get("feedback", "")

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                action = "deny"
                response_data = None

                if approved_status is True or approved_status == "approve":
                    action = "approve"
                    response_data = proposal
                elif approved_status == "refine":
                    action = "refine"
                    response_data = {"feedback": feedback, "proposal": proposal}
                elif approved_status == "rework_plan":
                    action = "rework_plan"
                    response_data = {"feedback": feedback}

                from backend.agent.harness import active_schema_requests

                fut = active_schema_requests.get(request_id)
                if fut and not fut.done():
                    fut.set_result((action, response_data))
                if run_store.get_interaction(request_id):
                    run_coordinator.resolve_interaction(
                        request_id, {"approved": approved_status, "proposal": proposal, "feedback": feedback}
                    )
            elif msg_type == "plan_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")
                plan = data.get("plan", "")
                feedback = data.get("feedback", "")

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                action = "deny"
                response_data = None

                if approved_status is True or approved_status == "approve":
                    action = "approve"
                    response_data = plan
                elif approved_status == "refine":
                    action = "refine"
                    response_data = {"feedback": feedback, "plan": plan}

                from backend.agent.harness import active_plan_requests

                fut = active_plan_requests.get(request_id)
                if fut and not fut.done():
                    fut.set_result((action, response_data))
                if run_store.get_interaction(request_id):
                    run_coordinator.resolve_interaction(
                        request_id, {"approved": approved_status, "plan": plan, "feedback": feedback}
                    )
            elif msg_type == "verification_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")  # "approve", "rework_code", "rework_schema", "rework_plan"
                feedback = data.get("feedback", "")

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                action = approved_status or "approve"
                response_data = {"feedback": feedback}

                from backend.agent.harness import active_verification_requests

                fut = active_verification_requests.get(request_id)
                if fut and not fut.done():
                    fut.set_result((action, response_data))
                if run_store.get_interaction(request_id):
                    run_coordinator.resolve_interaction(
                        request_id, {"approved": approved_status, "feedback": feedback}
                    )
            elif msg_type == "graph_subscribe":
                sub_id = data.get("subscription_id")
                query = data.get("query", {})
                from backend.graph_subscription import subscription_manager

                initial_res = subscription_manager.register(websocket, sub_id, query, graph_db)
                await websocket.send_json(
                    {"type": "graph_query_update", "subscription_id": sub_id, "data": initial_res}
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
                    # Apply inverse actions; report success/failure back
                    applied: list[str] = []
                    errors: list[str] = []
                    for r in reverses:
                        try:
                            act = r.get("action")
                            if act == "update_node_property":
                                graph_db.update_node_property(
                                    node_id=r.get("id"),
                                    properties=r.get("properties", {}),
                                )
                                applied.append(r.get("id", "?"))
                            elif act == "delete_node":
                                graph_db.delete_node(node_id=r.get("id"))
                                applied.append(r.get("id", "?"))
                            elif act == "delete_edge":
                                graph_db.delete_edge(
                                    from_id=r.get("from_id"),
                                    to_id=r.get("to_id"),
                                    edge_type=r.get("type"),
                                )
                                applied.append(f"{r.get('from_id')}->{r.get('to_id')}")
                        except Exception as e:
                            errors.append(f"{r}: {e!s}")

                    async def _send_ws(ws, payload):
                        try:
                            await ws.send_json(payload)
                        except Exception:
                            pass

                    from backend.graph_subscription import subscription_manager as _sub_mgr

                    await _sub_mgr.broadcast_updates(graph_db, _send_ws)
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
                # Run the orchestrator logic in a concurrent background task to avoid blocking the WS read loop
                asyncio.create_task(process_user_message(content, sender))

    except WebSocketDisconnect:
        pass
    finally:
        if session_id in active_websockets:
            active_websockets[session_id].discard(websocket)
            if not active_websockets[session_id]:
                active_websockets.pop(session_id, None)
        from backend.graph_subscription import subscription_manager

        subscription_manager.unregister_all(websocket)
