from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.agent.errors import BudgetExhaustedError, WorkflowError
from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind
from backend.agent.providers import ToolLoopBudget
from backend.agent.run_context import RunContext
from backend.agent.slash_commands import SlashCommandParseError, explicit_skill_ids
from backend.app_manager import AppManager
from backend.app_manifest import AppManifest, ManifestValidationError, validate_app_id
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.capabilities.models import RuntimeContract, normalize_grants
from backend.coding_agent_repair import decide_widget_repair
from backend.coding_agent_runtime import spec_for
from backend.context_manager import ContextManager
from backend.graph_db import GraphDatabase
from backend.graph_query_engine import execute_graph_query
from backend.llm_config import LLMConfigError, LLMConfigStore, ModelSelection
from backend.llm_runtime import use_model_selections
from backend.models import ChatMessage, ChatSession
from backend.coding_agent_acp import (
    CodingAgentDraftError,
    CodingAgentStagedResult,
    discard_coding_agent_staging,
    promote_coding_agent_staging,
    validate_coding_agent_promotion,
    validate_coding_agent_staging,
)
from backend.plan_generation import PlanGenerationService
from backend.run_service import (
    AgentRunState,
    Continue,
    CURRENT_AGENT_WORKFLOW_VERSION,
    Failed,
    PendingRunEvent,
    RunStore,
    StepOutcomeValue,
    Succeeded,
    Wait,
)
from backend.schema_alignment import SchemaAlignmentService, validate_schema_capability_proposal
from backend.schema_verification import SchemaVerificationService
from backend.skill_manager import (
    SkillContextBudgetError,
    SkillExplicitSelectionError,
    SkillManager,
)
from backend.skill_sandbox import (
    SkillPromptChannels,
    SkillSandboxError,
    build_skill_prompt_channels,
)
from backend.skill_store import (
    SkillAuthorizationRequiredError,
    SkillPackageIntegrityError,
    SkillStoreCorruptionError,
)
from backend.workspace_storage import WorkspaceStorage

logger = logging.getLogger("agent.durable_workflow")

EventSink = Callable[[str, dict[str, Any]], Awaitable[None] | None]
LiveEventSink = Callable[[str, dict[str, Any]], Awaitable[None] | None]
CodingAgentRunner = Callable[..., Awaitable[Any]]


class DurableAgentWorkflow:
    """Versioned, one-step-at-a-time reducer for scheduler-owned chat Runs."""

    VERSION = CURRENT_AGENT_WORKFLOW_VERSION
    _MODEL_FREE_PHASES = {
        "graph_query",
        "graph_preflight",
        "wait_graph_approval",
        "graph_commit",
    }
    _WIDGET_KEYS = {
        "plan_candidate",
        "plan_rework_feedback",
        "plan_schema_context",
        "approved_plan",
        "schema_candidate",
        "schema_validation_errors",
        "approved_schema",
        "runtime_contract",
        "code_feedback",
        "staged_app",
        "verification_report",
        "verification_options",
        "verification_passed",
        "verification_override",
        "schema_snapshot",
    }

    def __init__(
        self,
        *,
        workspace_dir: str,
        run_store: RunStore,
        app_manager: AppManager,
        graph_db: GraphDatabase,
        llm_config_store: LLMConfigStore | Callable[[], LLMConfigStore],
        coding_agent_runner: CodingAgentRunner,
        event_sink: EventSink | None = None,
        live_event_sink: LiveEventSink | None = None,
        app_diagnostic_loader: Callable[[str], list[dict[str, Any]]] | None = None,
        capability_catalog_factory: Callable[[], SystemCapabilityCatalog] | None = None,
        skill_manager: SkillManager | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.run_store = run_store
        self.app_manager = app_manager
        self.graph_db = graph_db
        self.llm_config_store = llm_config_store
        self.coding_agent_runner = coding_agent_runner
        self.event_sink = event_sink
        self.live_event_sink = live_event_sink
        self.app_diagnostic_loader = app_diagnostic_loader
        self.capability_catalog_factory = capability_catalog_factory or SystemCapabilityCatalog.build
        self.skill_manager = skill_manager
        self._event_buffer: ContextVar[list[PendingRunEvent] | None] = ContextVar(
            "durable_agent_event_buffer",
            default=None,
        )
        self._live_stream_state: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
            "durable_agent_live_stream_state",
            default=None,
        )

    async def __call__(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        token = self._event_buffer.set([])
        live_token = self._live_stream_state.set({})
        try:
            outcome = await self._reduce_once(run, state)
            events = list(self._event_buffer.get() or [])
        finally:
            self._live_stream_state.reset(live_token)
            self._event_buffer.reset(token)
        outcome.events.extend(events)
        return outcome

    async def _reduce_once(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        started = time.monotonic()
        try:
            if state.workflow_version != self.VERSION:
                return Failed(
                    summary="Unsupported workflow version",
                    error_code="unsupported_workflow_version",
                    message=f"Expected workflow version {self.VERSION}, got {state.workflow_version}",
                )
            if state.session_id != run.get("source_id"):
                return Failed(
                    summary="Invalid workflow checkpoint",
                    error_code="session_mismatch",
                    message="Checkpoint session does not match Run source",
                )
            active_seconds = float(state.data.get("active_seconds", 0.0))
            if active_seconds >= state.budget.max_wall_seconds:
                raise BudgetExhaustedError("Agent Run exceeded its active wall-clock budget")

            handler = getattr(self, f"_phase_{state.phase}", None)
            if handler is None:
                return Failed(
                    summary="Unknown workflow phase",
                    error_code="unknown_workflow_phase",
                    message=f"Unknown durable workflow phase: {state.phase}",
                )
            if state.phase in self._MODEL_FREE_PHASES:
                outcome = await handler(run, state)
            else:
                primary, fast = self._model_selections(state)
                coding_raw = state.model_snapshot.get("coding_model")
                coding = ModelSelection.model_validate(coding_raw) if coding_raw else primary
                with use_model_selections(primary, fast, coding):
                    outcome = await handler(run, state)
            if isinstance(outcome, Failed):
                return await self._failure(
                    state,
                    run=run,
                    code=outcome.error_code,
                    message=outcome.message,
                    retryable=outcome.retryable,
                    effect_state=outcome.effect_state,
                )
            return outcome
        except asyncio.CancelledError:
            # Task cancellation is also how RunCoordinator stops workers during
            # graceful shutdown.  Only an explicit, durably recorded cancel
            # command may compensate effects or discard retained artifacts.
            # Otherwise the worker lease is released and this exact checkpoint
            # is requeued, so changing live state here would make the persisted
            # saga diverge from reality.
            run_id = run.get("id")
            current = self.run_store.get_run(str(run_id)) if run_id else None
            if current is not None and current.get("status") == "cancel_requested":
                self.cleanup_state(state)
            raise
        except LLMConfigError as exc:
            return await self._failure(
                state,
                run=run,
                code=exc.code,
                message=str(exc),
                retryable=False,
                effect_state="none",
            )
        except WorkflowError as exc:
            return await self._failure(
                state,
                run=run,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                effect_state=exc.effect_state,
            )
        except Exception as exc:
            logger.exception("Durable workflow phase %s failed", state.phase)
            return await self._failure(
                state,
                run=run,
                code=type(exc).__name__,
                message=str(exc),
                retryable=False,
                effect_state=(
                    "unknown"
                    if state.data.get("non_compensable_effect") or state.data.get("effect_in_flight")
                    else "none"
                ),
            )
        finally:
            state.data["active_seconds"] = float(state.data.get("active_seconds", 0.0)) + (time.monotonic() - started)

    def _model_selections(self, state: AgentRunState) -> tuple[ModelSelection, ModelSelection]:
        config_store = self.llm_config_store() if callable(self.llm_config_store) else self.llm_config_store
        primary_raw = state.model_snapshot.get("primary")
        fast_raw = state.model_snapshot.get("fast")
        if primary_raw is None:
            settings = config_store.get_settings()
            primary_raw = settings.get("default_model")
            fast_raw = settings.get("fast_model") or primary_raw
        if primary_raw is None:
            raise LLMConfigError("Configure a default model before starting a task", code="llm_configuration_required")
        primary = ModelSelection.model_validate(primary_raw)
        fast = ModelSelection.model_validate(fast_raw or primary_raw)
        config_store.resolve(primary)
        config_store.resolve(fast)
        return primary, fast

    @staticmethod
    def _storage(workspace_dir: str) -> WorkspaceStorage:
        # WorkspaceStorage buffers pending writes; a per-step instance avoids
        # sharing that mutable buffer across concurrently executing sessions.
        return WorkspaceStorage(workspace_dir)

    def _run_storage(self, state: AgentRunState) -> WorkspaceStorage:
        workspace_dir = state.data.get("workspace_dir")
        return self._storage(str(workspace_dir) if workspace_dir else self.workspace_dir)

    def _ensure_context_summary(self, state: AgentRunState, storage: WorkspaceStorage) -> str | None:
        summary = state.data.get("context_summary")
        if summary is not None and not isinstance(summary, str):
            raise WorkflowError("Durable context summary is malformed", code="invalid_context_summary")
        if summary:
            expected_ref = f"sha256:{hashlib.sha256(summary.encode('utf-8')).hexdigest()}"
            if state.context_summary_ref and state.context_summary_ref != expected_ref:
                raise WorkflowError("Durable context summary hash mismatch", code="context_summary_hash_mismatch")
            state.context_summary_ref = expected_ref
            return summary

        summary = ContextManager(
            db_session=storage,
            app_manager=self.app_manager,
        ).build_persistent_summary(state.session_id or "default-session")
        if summary:
            state.data["context_summary"] = summary
            state.context_summary_ref = f"sha256:{hashlib.sha256(summary.encode('utf-8')).hexdigest()}"
        return summary

    @staticmethod
    def _run_context(run: dict[str, Any], state: AgentRunState) -> RunContext:
        artifact_hashes = {
            str(ref["id"]): str(ref["sha256"])
            for ref in state.artifact_refs
            if isinstance(ref, dict) and ref.get("id") and ref.get("sha256")
        }
        active_skills = state.data.get("active_skills", [])
        if isinstance(active_skills, list):
            for snapshot in active_skills:
                if not isinstance(snapshot, dict):
                    continue
                catalog_id = snapshot.get("catalog_id")
                digest = snapshot.get("digest")
                if isinstance(catalog_id, str) and catalog_id and isinstance(digest, str) and digest:
                    # Audit contexts use bare SHA-256 values. The durable
                    # checkpoint retains the algorithm-qualified digest.
                    artifact_hashes[catalog_id] = (
                        digest.removeprefix("sha256:")
                        if digest.startswith("sha256:")
                        else digest
                    )
        return RunContext(
            run_id=str(run["id"]),
            session_id=str(state.session_id or run.get("source_id") or ""),
            step_id=state.phase,
            attempt=max(1, int(state.attempt)),
            trace_id=str(state.data.get("trace_id") or run["id"]),
            primary_model=dict(state.model_snapshot.get("primary") or {}),
            fast_model=dict(state.model_snapshot.get("fast") or {}),
            artifact_hashes=artifact_hashes,
        )

    def _active_skill_prompt_channels(self, state: AgentRunState) -> SkillPromptChannels:
        snapshots = state.data.get("active_skills")
        if snapshots is None:
            return SkillPromptChannels()
        if not isinstance(snapshots, list) or any(not isinstance(item, dict) for item in snapshots):
            raise WorkflowError("Durable Skill snapshot is malformed", code="invalid_skill_snapshot")
        if not snapshots:
            return SkillPromptChannels()
        if self.skill_manager is None:
            # Old deployments can resume checkpoints without silently loading
            # mutable package state, but cannot interpret an unknown snapshot.
            raise WorkflowError("Skill runtime is unavailable", code="skill_runtime_unavailable")
        try:
            return build_skill_prompt_channels(
                snapshots,
                render_context=self.skill_manager.render_context,
            )
        except (SkillContextBudgetError, SkillSandboxError, ValueError, TypeError) as exc:
            raise WorkflowError("Durable Skill snapshot is invalid", code="invalid_skill_snapshot") from exc

    def _select_active_skills(self, content: str, state: AgentRunState) -> None:
        """Select once at route time and persist exact instructions for replay."""

        marker = state.data.get("skill_selection_state")
        has_snapshots = "active_skills" in state.data
        snapshots = state.data.get("active_skills")

        if marker is None:
            if has_snapshots:
                raise WorkflowError(
                    "Legacy Skill checkpoint has an unexpected snapshot",
                    code="invalid_skill_snapshot",
                )
            # Checkpoints created before Skill support have neither a marker
            # nor snapshots. Pin an empty selection so an upgrade cannot inject
            # a Skill installed after the Run began.
            state.data["active_skills"] = []
            state.data["skill_selection_state"] = "pinned_none"
            return

        if marker == "pinned":
            if not has_snapshots or not isinstance(snapshots, list) or not snapshots:
                raise WorkflowError(
                    "Pinned Skill checkpoint is missing its snapshot",
                    code="invalid_skill_snapshot",
                )
            # Never re-resolve a recovered Run against a newer installation.
            self._active_skill_prompt_channels(state)
            return

        if marker == "pinned_none":
            if snapshots is not None and snapshots != []:
                raise WorkflowError(
                    "Empty Skill checkpoint contains an active snapshot",
                    code="invalid_skill_snapshot",
                )
            state.data["active_skills"] = []
            return

        if marker != "pending":
            raise WorkflowError(
                "Skill checkpoint has an unknown selection state",
                code="invalid_skill_snapshot",
            )
        if has_snapshots:
            raise WorkflowError(
                "Pending Skill checkpoint already contains a snapshot",
                code="invalid_skill_snapshot",
            )

        if self.skill_manager is None:
            state.data["active_skills"] = []
            state.data["skill_selection_state"] = "pinned_none"
            return
        try:
            try:
                selected_skill_ids = explicit_skill_ids(content)
            except SlashCommandParseError:
                # The router owns the user-facing clarification for an
                # oversized sequence; selection must not replace it with a
                # Skill workflow error.
                selected_skill_ids = []
            state.data["active_skills"] = (
                self.skill_manager.select_for_context(
                    content,
                    explicit_names=selected_skill_ids,
                )
                if selected_skill_ids
                else self.skill_manager.select_for_context(content)
            )
        except SkillExplicitSelectionError as exc:
            raise WorkflowError(
                f"Explicit Skill '{exc.target}' cannot be activated ({exc.reason})",
                code=f"skill_{exc.reason}",
            ) from exc
        except SkillPackageIntegrityError as exc:
            raise WorkflowError(
                "The selected Skill package failed integrity verification",
                code="skill_package_integrity_error",
            ) from exc
        except (SkillContextBudgetError, ValueError, TypeError) as exc:
            raise WorkflowError("Skill context selection failed validation", code="invalid_skill_context") from exc
        state.data["skill_selection_state"] = (
            "pinned" if state.data["active_skills"] else "pinned_none"
        )
        self._active_skill_prompt_channels(state)

    async def _emit_live(
        self,
        run: dict[str, Any],
        payload: dict[str, Any],
        *,
        mode: str,
    ) -> None:
        session_id = str(run.get("source_id") or "")
        streams = self._live_stream_state.get()
        if self.live_event_sink is None or streams is None or not session_id:
            return

        phase = str(run.get("step_key") or payload.get("step_id") or "route")
        attempt = max(1, int(run.get("step_attempt") or payload.get("attempt") or 1))
        payload_type = str(payload.get("type") or "")
        kind = ""
        delta = ""
        snapshot = ""
        stream_suffix = "activity"
        tool: str | None = None
        tool_status: str | None = None

        if payload_type.startswith("tool_"):
            kind = "tool_progress"
            tool = str(payload.get("tool") or "tool")
            tool_status = payload_type.removeprefix("tool_")
            stream_suffix = f"tool:{tool}"
            delta = tool
            mode = "replace"
        elif payload_type == "activity_updated":
            activity_id = str(payload.get("activity_id") or "activity")
            kind = "activity_delta"
            stream_suffix = f"activity:{activity_id}"
            delta = str(payload.get("detail") or payload.get("summary") or "")
            snapshot = delta
        else:
            return

        if not delta:
            return
        stream_id = f"{run['id']}:{phase}:{attempt}:{stream_suffix}"
        stream = streams.setdefault(stream_id, {"sequence": 0, "snapshot": ""})
        replace = mode == "replace"
        if mode == "snapshot":
            previous = str(stream.get("snapshot") or "")
            if previous and delta.startswith(previous):
                delta = delta[len(previous) :]
                replace = False
            else:
                replace = True
            stream["snapshot"] = snapshot
        if not delta:
            return
        stream["sequence"] = int(stream.get("sequence") or 0) + 1
        event = {
            "schema_version": 1,
            "run_id": str(run["id"]),
            "session_id": session_id,
            "step_id": phase,
            "attempt": attempt,
            "stream_id": stream_id,
            "chunk_sequence": stream["sequence"],
            "kind": kind,
            "delta": delta[-12_000:],
            "replace": replace,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if tool is not None:
            event["tool"] = tool
            event["tool_status"] = tool_status
        try:
            result = self.live_event_sink(session_id, event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Live Run event projection failed", exc_info=True)

    async def _emit(
        self,
        run: dict[str, Any],
        payload: dict[str, Any],
        *,
        project_to_chat: bool = True,
    ) -> None:
        if not isinstance(payload, dict):
            raise WorkflowError(
                "Durable Run events must use a structured payload",
                code="unstructured_run_event",
            )
        if not str(payload.get("type") or ""):
            raise WorkflowError(
                "Durable Run event payload is missing its type",
                code="invalid_run_event",
            )
        wire_payload = payload
        event_type = str(payload["type"])
        buffer = self._event_buffer.get()
        if buffer is None:
            raise WorkflowError("Reducer event emitted outside a step transaction", code="event_outside_step")
        if project_to_chat:
            await self._emit_live(run, wire_payload, mode="delta")
        buffer.append(
            PendingRunEvent(
                type=event_type,
                payload=wire_payload,
                project_to_chat=project_to_chat,
            )
        )

    async def _emit_activity_progress(
        self,
        run: dict[str, Any],
        *,
        activity_id: str,
        summary: str,
        detail: str,
        mode: str = "replace",
    ) -> None:
        """Project ephemeral progress without adding it to Run or chat history."""

        normalized_detail = str(detail or "")[-12_000:]
        if not normalized_detail:
            return
        await self._emit_live(
            run,
            {
                "type": "activity_updated",
                "activity_id": activity_id[:200],
                "status": "running",
                "summary": " ".join(summary.strip().split())[:2_000] or "Working",
                "detail": normalized_detail,
            },
            mode=mode,
        )

    async def _emit_callback_update(
        self,
        run: dict[str, Any],
        payload: Any,
        *,
        activity_id: str,
        summary: str,
        mode: str = "replace",
    ) -> None:
        """Keep typed tool events durable and route free-form updates to live progress."""

        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or "")
            if payload_type in {
                "tool_started",
                "tool_succeeded",
                "tool_failed",
                "tool_cancelled",
                "permission_request",
                "backend_permission_request",
            }:
                await self._emit(run, payload)
                return
            detail = str(
                payload.get("message") or payload.get("detail") or payload.get("summary") or payload_type or ""
            )
        else:
            detail = str(payload)
        await self._emit_activity_progress(
            run,
            activity_id=activity_id,
            summary=summary,
            detail=detail,
            mode=mode,
        )

    async def _emit_activity(
        self,
        run: dict[str, Any],
        *,
        activity_id: str,
        activity_type: str,
        status: str,
        summary: str,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "activity_updated",
            "activity_id": activity_id[:200],
            "activity_type": activity_type,
            "status": status,
            "summary": " ".join(summary.strip().split())[:2_000] or activity_type,
            "metadata": metadata or {},
        }
        if detail:
            payload["detail"] = detail[:12_000]
        # Structured activity belongs to the canonical Run stream. Keep the
        # legacy /ws/chat projection stable while still sending its live hint.
        await self._emit_live(run, payload, mode="replace")
        await self._emit(run, payload, project_to_chat=False)

    async def dispatch_committed_events(self, run: dict[str, Any], outcome: StepOutcomeValue) -> None:
        """Best-effort compatibility projection after the SQLite commit."""

        session_id = str(run.get("source_id") or "")
        if self.event_sink is None or not session_id:
            return
        for event in outcome.events:
            if not event.project_to_chat:
                continue
            try:
                result = self.event_sink(session_id, event.payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning("Committed Run event projection failed", exc_info=True)

    @staticmethod
    def _reply_payload(message: ChatMessage) -> dict[str, Any]:
        return {
            "type": "reply",
            "message": {
                "id": message.id,
                "sender": message.sender,
                "role": message.role,
                "content": message.content,
                "context_policy": message.context_policy,
                "provenance": message.provenance,
                "timestamp": message.timestamp.isoformat() if message.timestamp else None,
            },
        }

    @staticmethod
    def _message_for_run(storage: WorkspaceStorage, session_id: str, run_id: str) -> ChatMessage | None:
        return next((message for message in storage.get_messages(session_id) if message.run_id == run_id), None)

    def _save_agent_message(
        self,
        run: dict[str, Any],
        state: AgentRunState,
        content: str,
        *,
        role: str = "agent",
    ) -> tuple[ChatMessage, bool]:
        storage = self._run_storage(state)
        session_id = str(run["source_id"])
        existing = self._message_for_run(storage, session_id, run["id"])
        if existing is not None and existing.role == role:
            return existing, False
        message = ChatMessage(
            session_id=session_id,
            role=role,
            sender="agent",
            content=content,
            run_id=run["id"],
        )
        storage.add(message)
        storage.commit()
        storage.refresh(message)
        return message, True

    def _consume_model_turn(self, state: AgentRunState, count: int = 1) -> None:
        if state.budget.model_turns + count > state.budget.max_model_turns:
            raise BudgetExhaustedError("Agent Run exceeded its model-turn budget")
        state.budget.model_turns += count

    def _consume_usage(self, state: AgentRunState, usage: dict[str, Any]) -> None:
        def number(*keys: str) -> float | None:
            for key in keys:
                value = usage.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and float(value) >= 0
                ):
                    return float(value)
            return None

        total_tokens = number("total_tokens")
        if total_tokens is None:
            input_tokens = number("input_tokens", "prompt_tokens") or 0.0
            output_tokens = number("output_tokens", "completion_tokens") or 0.0
            total_tokens = input_tokens + output_tokens
        call_cost = number("cost_usd", "response_cost", "cost") or 0.0
        state.budget.tokens_used += int(total_tokens)
        state.budget.cost_usd += call_cost
        if state.budget.max_tokens is not None and state.budget.tokens_used > state.budget.max_tokens:
            raise BudgetExhaustedError("Agent Run exceeded its token budget")
        if state.budget.max_cost_usd is not None and state.budget.cost_usd > state.budget.max_cost_usd:
            raise BudgetExhaustedError("Agent Run exceeded its cost budget")

    def _model_budget(
        self,
        state: AgentRunState,
        *,
        max_iterations: int = 1,
        max_tool_calls: int = 0,
    ) -> ToolLoopBudget:
        remaining_turns = state.budget.max_model_turns - state.budget.model_turns
        if remaining_turns < 1:
            raise BudgetExhaustedError("Agent Run exceeded its model-turn budget")
        remaining_wall_seconds = max(
            0.001,
            state.budget.max_wall_seconds - float(state.data.get("active_seconds", 0.0)),
        )
        return ToolLoopBudget(
            max_iterations=min(max_iterations, remaining_turns),
            max_tool_calls=max_tool_calls,
            wall_clock_s=remaining_wall_seconds,
            llm_call_timeout_s=min(60.0, remaining_wall_seconds),
            on_model_call=lambda: self._consume_model_turn(state, 1),
            on_usage=lambda usage: self._consume_usage(state, usage),
        )

    @staticmethod
    def _interaction_id(
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        phase: str,
        attempt: int,
    ) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {"phase": phase, "attempt": attempt, "payload": payload},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"{kind}-{run_id[:8]}-a{attempt}-{digest}"

    async def _wait(
        self,
        run: dict[str, Any],
        state: AgentRunState,
        *,
        kind: str,
        prompt: str,
        payload: dict[str, Any],
    ) -> Wait:
        interaction_id = self._interaction_id(
            run["id"],
            kind,
            payload,
            phase=state.phase,
            attempt=max(1, int(state.attempt)),
        )
        wire_payload = {**payload, "request_id": interaction_id, "run_id": run["id"]}
        state.pending_interaction_id = interaction_id
        await self._emit_activity(
            run,
            activity_id=f"approval:{interaction_id}",
            activity_type="approval",
            status="waiting",
            summary=prompt,
            metadata={"interaction_id": interaction_id, "interaction_type": kind},
        )
        await self._emit(run, wire_payload)
        return Wait(
            interaction_id=interaction_id,
            interaction_type=kind,
            interaction_prompt=prompt,
            interaction_payload=wire_payload,
            summary=prompt,
        )

    def _response(self, state: AgentRunState) -> dict[str, Any] | None:
        interaction_id = state.pending_interaction_id
        if not interaction_id:
            return None
        interaction = self.run_store.get_interaction(interaction_id)
        if interaction is None:
            raise WorkflowError("Pending interaction is missing", code="interaction_missing")
        if interaction["status"] == "pending":
            return None
        if interaction["status"] == "cancelled":
            raise WorkflowError("Interaction was cancelled", code="interaction_cancelled")
        response = interaction.get("response")
        state.pending_interaction_id = None
        return response if isinstance(response, dict) else {"approved": bool(response)}

    @staticmethod
    def _approval(response: dict[str, Any]) -> str:
        value = response.get("approved", False)
        if value is True:
            return "approve"
        if value is False or value is None:
            return "deny"
        return str(value)

    def _current_intent(self, state: AgentRunState) -> IntentPlan:
        raw = state.data.get("current_intent") or state.intent
        if not isinstance(raw, dict):
            raise WorkflowError("Workflow has no structured intent", code="intent_missing")
        return IntentPlan.from_dict(raw)

    @staticmethod
    def _phase_for_intent(intent: IntentPlan) -> str:
        if intent.kind == IntentKind.CONVERSE:
            return "converse"
        if intent.kind == IntentKind.GRAPH_QUERY:
            return "graph_query"
        if intent.kind == IntentKind.GRAPH_MUTATION:
            return "graph_preflight"
        if intent.kind in {IntentKind.WIDGET_CREATE, IntentKind.WIDGET_MODIFY}:
            return "plan"
        if intent.kind in {IntentKind.MULTI_INTENT, IntentKind.PLAN_AND_ACT}:
            return "multi_preflight"
        if intent.kind == IntentKind.CLARIFY:
            return "clarify"
        raise WorkflowError(f"Unsupported intent: {intent.kind}", code="unsupported_intent")

    async def _failure(
        self,
        state: AgentRunState,
        *,
        run: dict[str, Any] | None = None,
        code: str,
        message: str,
        retryable: bool,
        effect_state: str,
    ) -> Failed:
        retries = state.data.setdefault("phase_retries", {})
        phase_retries = int(retries.get(state.phase, 0))
        may_retry = retryable and phase_retries < 2 and effect_state == "none"
        if may_retry:
            # Keep already committed saga steps intact.  They have durable
            # effect ledgers and compensation data, so retrying only the
            # current phase must not silently roll earlier results back.  A
            # staged App is also part of the checkpoint: a transient verifier
            # failure must retry that artifact instead of deleting and
            # regenerating it.
            retries[state.phase] = phase_retries + 1
            return Failed(
                summary="Retrying agent step",
                error_code=code,
                message=message,
                retryable=True,
                effect_state="none",
            )

        compensated = False
        if state.data.get("graph_compensations") and not state.data.get("non_compensable_effect"):
            try:
                for item in reversed(state.data["graph_compensations"]):
                    actions = item.get("actions", []) if isinstance(item, dict) else item
                    ticket_id = item.get("ticket_id") if isinstance(item, dict) else None
                    self.graph_db.apply_actions_atomic(
                        actions,
                        idempotency_key=f"compensate:{ticket_id}" if ticket_id else None,
                    )
                state.data["graph_compensations"] = []
                state.data["effects_committed"] = False
                effect_state = "none"
                compensated = True
            except Exception:
                logger.exception("Unable to compensate durable graph saga")
                effect_state = "unknown"
        elif state.data.get("effects_committed"):
            effect_state = "unknown"

        if compensated:
            # A later explicit retry must rebuild the saga from preflight;
            # keeping its old index/results would report effects that were
            # just compensated away.
            state.phase = "route"
            state.intent = None
            for key in {
                "current_intent",
                "return_to_multi",
                "multi_index",
                "multi_results",
                "multi_preflight_complete",
                "multi_preflight_intents",
                "graph_actions",
                "pre_extend_schema_props",
            }:
                state.data.pop(key, None)

        retained_staged_app = False
        staged = state.data.get("staged_app")
        if isinstance(staged, dict) and not state.data.get("non_compensable_effect"):
            raw_staging_dir = staged.get("staging_dir")
            if isinstance(raw_staging_dir, str) and raw_staging_dir:
                staging_dir = Path(raw_staging_dir)
                try:
                    retained_staged_app = staging_dir.is_dir() and not staging_dir.is_symlink()
                except OSError:
                    retained_staged_app = False
            if retained_staged_app:
                # A failed draft remains hidden from App discovery and cannot
                # execute or promote without passing the regular verifier.
                # Keeping the durable handle lets an explicit retry resume at
                # verification with a fresh active-time window.
                state.data["staged_app_status"] = {
                    "state": "failed_draft",
                    "phase": state.phase,
                    "error_code": code,
                    "retryable": False,
                }

        failure = Failed(
            summary=(
                "任务失败；生成草稿已保留，可重试继续验证"
                if retained_staged_app and state.data.get("language") == "zh"
                else "Agent task failed; staged App retained"
                if retained_staged_app
                else "Agent task failed"
            ),
            error_code=code,
            message=message,
            retryable=False,
            effect_state=effect_state if effect_state in {"none", "committed", "unknown"} else "unknown",
        )
        if retained_staged_app and run is not None:
            app_id = str(staged.get("app_id") or "")
            if not app_id and isinstance(state.intent, dict):
                app_id = str(state.intent.get("app_id") or "")
            app_id = app_id or "unknown-app"
            reason = " ".join(str(message).strip().split())[:2_000] or "Unknown generation failure"
            repair_decision = (
                state.data.get("repair_decision") if isinstance(state.data.get("repair_decision"), dict) else {}
            )
            automatic_repair_stalled = repair_decision.get("action") == "human"
            if state.data.get("language") == "zh":
                repair_guidance = (
                    "自动修复已停止：同一校验错误连续出现，或修复没有改变受校验文件。"
                    f"失败草稿已安全保留；如有新的修复思路，可回复 `/repair {app_id} <具体说明>`。"
                    if automatic_repair_stalled
                    else f"失败草稿已安全保留。请直接回复 `/repair {app_id}` 继续修复；也可以在命令后补充具体要求。"
                )
                content = (
                    f"Widget “{app_id}” 生成失败，尚未发布到应用中心。\n"
                    f"失败阶段：{state.phase}\n"
                    f"错误码：{code}\n"
                    f"原因：{reason}\n"
                    f"{repair_guidance}"
                )
            else:
                repair_guidance = (
                    "Automatic repair stopped because the same verifier finding repeated or the validated "
                    f"files did not change. The failed draft was retained; reply with `/repair {app_id} "
                    "<specific guidance>` only if you have a new repair direction."
                    if automatic_repair_stalled
                    else f"The failed draft was retained safely. Reply with `/repair {app_id}` to continue, "
                    "optionally followed by additional instructions."
                )
                content = (
                    f'Widget "{app_id}" failed and was not published to App Center.\n'
                    f"Failed phase: {state.phase}\n"
                    f"Error code: {code}\n"
                    f"Cause: {reason}\n"
                    f"{repair_guidance}"
                )
            try:
                diagnostic, created = self._save_agent_message(run, state, content)
                if created:
                    failure.events.append(
                        PendingRunEvent(
                            type="reply",
                            payload=self._reply_payload(diagnostic),
                            project_to_chat=True,
                        )
                    )
            except Exception:
                logger.exception("Unable to persist the failed Widget diagnostic in chat")
        return failure

    def cleanup_state(self, state: AgentRunState | dict[str, Any] | None) -> None:
        """Best-effort cleanup for cancelled/abandoned retained staging artifacts."""

        if not state:
            return
        normalized = state if isinstance(state, AgentRunState) else AgentRunState.model_validate(state)
        staged = normalized.data.get("staged_app")
        if staged and not normalized.data.get("non_compensable_effect"):
            try:
                discard_coding_agent_staging(self._staged_result(staged))
                normalized.data.pop("staged_app", None)
            except Exception:
                logger.warning("Unable to discard abandoned staged App", exc_info=True)

        if normalized.data.get("graph_compensations") and not normalized.data.get("non_compensable_effect"):
            try:
                for item in reversed(normalized.data["graph_compensations"]):
                    actions = item.get("actions", []) if isinstance(item, dict) else item
                    ticket_id = item.get("ticket_id") if isinstance(item, dict) else None
                    self.graph_db.apply_actions_atomic(
                        actions,
                        idempotency_key=f"compensate:{ticket_id}" if ticket_id else None,
                    )
                normalized.data["graph_compensations"] = []
                normalized.data["effects_committed"] = False
            except Exception:
                logger.exception("Unable to compensate cancelled durable graph saga")

    async def _phase_route(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        storage = self._run_storage(state)
        session_id = state.session_id or "default-session"
        session = storage.get(ChatSession, session_id)
        if session is None:
            session = ChatSession(id=session_id, title="Active Chat")
            storage.add(session)
            storage.commit()
        content = str((run.get("input") or {}).get("content") or "")
        if not content.strip():
            return Failed(
                summary="Empty agent command",
                error_code="empty_command",
                message="Chat command content must not be empty",
            )
        self._select_active_skills(content, state)
        skill_prompt_channels = self._active_skill_prompt_channels(state)
        if skill_prompt_channels.untrusted_user_guidance:
            # External natural-language guidance never gets a chance to steer
            # the LLM Router into Widget, Graph, or other effect workflows.
            # Its only execution surface is the bounded read-only Converse
            # phase, where it remains a lower-priority data message.
            intent = IntentPlan(
                kind=IntentKind.CONVERSE,
                confidence=1.0,
                rationale="external Skill semantic sandbox requires read-only Converse",
                instruction=content,
            )
        else:
            context_summary = self._ensure_context_summary(state, storage)
            orchestrator = AgentOrchestrator(
                db_session=storage,
                app_manager=self.app_manager,
                graph_db=self.graph_db,
                run_context=self._run_context(run, state),
                context_summary=context_summary,
                artifact_ids=[
                    str(ref.get("id"))
                    for ref in state.artifact_refs
                    if isinstance(ref, dict) and ref.get("id")
                ],
                tool_loop_budget=self._model_budget(state),
                capability_catalog=self.capability_catalog_factory(),
            )
            intent = await orchestrator._classify_intent(
                content,
                session_id=session_id,
                language=session.language or "zh",
            )
        if intent.deprecated:
            return Failed(
                summary="Deprecated intent",
                error_code="deprecated_intent",
                message="Router returned a deprecated execution plan",
            )
        state.intent = intent.to_dict()
        state.workflow_type = intent.kind.value
        state.data["language"] = session.language or "zh"
        await self._emit(
            run,
            {
                "type": "agent_routed",
                "run_id": run["id"],
                "intent": intent.to_dict(),
            },
            project_to_chat=False,
        )
        return Continue(next_phase=self._phase_for_intent(intent), summary=f"Routed to {intent.kind.value}")

    async def _phase_clarify(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        content = intent.clarification_message or "Please provide more details."
        message, created = self._save_agent_message(run, state, content)
        if created:
            await self._emit(run, self._reply_payload(message))
        return Succeeded(summary="Clarification requested", result={"message": content})

    async def _phase_converse(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        storage = self._run_storage(state)
        existing = self._message_for_run(storage, state.session_id or "default-session", run["id"])
        if existing is not None and existing.role == "agent":
            cached_result = state.data.get("converse_result")
            app_id = cached_result.get("app_id") if isinstance(cached_result, dict) else None
            artifacts = [{"type": "app", "id": app_id}] if app_id else []
            return Succeeded(
                summary="Agent task completed",
                result={"message": existing.content, "app_id": app_id},
                artifacts=artifacts,
            )

        intent = self._current_intent(state)
        language = str(state.data.get("language") or "zh")
        remaining_model_turns = state.budget.max_model_turns - state.budget.model_turns
        skill_prompt_channels = self._active_skill_prompt_channels(state)
        external_skill_sandbox = bool(
            skill_prompt_channels.untrusted_user_guidance
        )

        def require_live_external_skill_grant() -> None:
            if not external_skill_sandbox:
                return
            if self.skill_manager is None:
                raise WorkflowError(
                    "Skill runtime is unavailable",
                    code="skill_runtime_unavailable",
                )
            try:
                self.skill_manager.require_current_external_authorizations(
                    state.data.get("active_skills", [])
                )
            except SkillAuthorizationRequiredError as exc:
                raise WorkflowError(
                    "External Skill authorization changed before context injection",
                    code="skill_authorization_required",
                ) from exc
            except SkillPackageIntegrityError as exc:
                raise WorkflowError(
                    "The selected Skill package failed integrity verification",
                    code="skill_package_integrity_error",
                ) from exc
            except (
                SkillStoreCorruptionError,
                OSError,
                ValueError,
                sqlite3.DatabaseError,
            ) as exc:
                raise WorkflowError(
                    "The Skill authorization registry is unavailable",
                    code="skill_registry_unavailable",
                ) from exc
        if external_skill_sandbox:
            # Reject stale checkpoints early, then repeat this same check at
            # the actual provider-admission boundary below.
            require_live_external_skill_grant()
        context_summary = (
            None
            if external_skill_sandbox
            else self._ensure_context_summary(state, storage)
        )
        orchestrator = AgentOrchestrator(
            db_session=storage,
            app_manager=self.app_manager,
            graph_db=self.graph_db,
            run_context=self._run_context(run, state),
            context_summary=context_summary,
            artifact_ids=(
                []
                if external_skill_sandbox
                else [
                    str(ref.get("id"))
                    for ref in state.artifact_refs
                    if isinstance(ref, dict) and ref.get("id")
                ]
            ),
            tool_loop_budget=self._model_budget(
                state,
                max_iterations=remaining_model_turns,
                max_tool_calls=12,
            ),
            capability_catalog=self.capability_catalog_factory(),
            skill_prompt_channels=skill_prompt_channels,
            pre_model_call_guard=(
                require_live_external_skill_grant
                if external_skill_sandbox
                else None
            ),
        )

        async def on_update(payload: Any) -> None:
            await self._emit_callback_update(
                run,
                payload,
                activity_id="converse:response",
                summary="Preparing response",
            )

        return_to_multi = bool(state.data.get("return_to_multi"))
        converse_kwargs = {"persist": False} if return_to_multi else {}
        raw_content = str((run.get("input") or {}).get("content") or "")
        converse_content = (
            intent.instruction
            if intent.instruction and (
                return_to_multi
                or intent.rationale == "explicit slash command"
            )
            else raw_content
        )
        message, widget = await orchestrator._handle_converse(
            plan=intent,
            session_id=state.session_id or "default-session",
            content=converse_content,
            language=language,
            on_update=on_update,
            **converse_kwargs,
        )
        if return_to_multi:
            result = {"message": message.content, "app_id": widget.get("id") if widget else None}
            return await self._finish_subflow(
                run,
                state,
                content=message.content,
                result=result,
                artifacts=(
                    [{"type": "app", "id": widget.get("id")}]
                    if widget
                    else []
                ),
            )
        # Mark the persisted projection with its originating Run so a recovered
        # step can detect it. Older storage implementations are tolerated.
        if getattr(message, "run_id", None) is None:
            message.run_id = run["id"]
            storage.add(message)
            storage.commit()
        await self._emit(run, self._reply_payload(message))
        artifacts: list[dict[str, Any]] = []
        if widget:
            await self._emit(run, {"type": "widget", "widget": widget})
            artifacts.append({"type": "app", "id": widget.get("id")})
        result = {"message": message.content, "app_id": widget.get("id") if widget else None}
        state.data["converse_result"] = result
        return Succeeded(
            summary="Agent task completed",
            result=result,
            artifacts=artifacts,
        )

    async def _phase_graph_query(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        results = execute_graph_query(intent.query or {}, self.graph_db)
        language = str(state.data.get("language") or "zh")
        if not results:
            content = "（图中没有匹配的节点。）" if language == "zh" else "(No matching nodes found.)"
        else:
            lines = []
            for item in results[:20]:
                props = item.get("properties", {})
                label = props.get("title") or props.get("summary") or props.get("name") or item["id"]
                lines.append(f"- {item['type']} `{item['id']}` — {label}")
            content = ("📊 Graph 结果：\n" if language == "zh" else "📊 Graph Results:\n") + "\n".join(lines)
        return await self._finish_subflow(run, state, content=content, result={"results": results})

    async def _phase_graph_preflight(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        normalized = self.graph_db.preflight_actions(intent.actions)
        state.data["graph_actions"] = normalized
        summary = AgentOrchestrator._summarize_actions(normalized, str(state.data.get("language") or "zh"))
        await self._emit(
            run,
            {
                "type": "mutation_preview",
                "run_id": run["id"],
                "actions": normalized,
                "summary": summary,
                "committed": False,
            },
        )
        state.phase = "wait_graph_approval"
        return await self._wait(
            run,
            state,
            kind="graph_mutation_approval",
            prompt="Approve graph mutation",
            payload={
                "type": "permission_request",
                "permission_type": "graph_mutation",
                "value": {"actions": normalized, "summary": summary},
            },
        )

    async def _phase_wait_graph_approval(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        del run
        response = self._response(state)
        if response is None:
            raise WorkflowError("Graph approval response is missing", code="interaction_unresolved")
        if self._approval(response) != "approve":
            return Failed(
                summary="Graph mutation denied",
                error_code="approval_denied",
                message="User denied the graph mutation",
            )
        return Continue(next_phase="graph_commit", summary="Graph mutation approved")

    async def _phase_graph_commit(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        actions = state.data.get("graph_actions")
        state.data["effect_in_flight"] = "graph_atomic_commit"
        try:
            mutation = await asyncio.to_thread(
                self.graph_db.apply_actions_atomic,
                actions,
                session_id=state.session_id,
                idempotency_key=(f"agent-run:{run['id']}:graph_commit:{int(state.data.get('multi_index', 0))}"),
            )
        except asyncio.CancelledError:
            # The SQLite transaction may still finish in its worker thread.
            # Retain the marker so cancellation resolves to needs_attention.
            raise
        except Exception:
            state.data.pop("effect_in_flight", None)
            raise
        state.data.pop("effect_in_flight", None)
        state.data.setdefault("graph_compensations", []).append(
            {"ticket_id": mutation["ticket_id"], "actions": mutation["reverse_actions"]}
        )
        state.data["effects_committed"] = True
        language = str(state.data.get("language") or "zh")
        summary = AgentOrchestrator._summarize_actions(mutation["actions"], language)
        content = f"✅ {summary}"
        await self._emit(
            run,
            {
                "type": "mutation_committed",
                "ticket_id": mutation["ticket_id"],
                "actions": mutation["actions"],
                "summary": summary,
            },
        )
        return await self._finish_subflow(
            run,
            state,
            content=content,
            result={"ticket_id": mutation["ticket_id"], "actions": mutation["actions"]},
        )

    async def _phase_multi_preflight(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        del run
        root = IntentPlan.from_dict(state.intent or {})
        if not root.sub_intents:
            raise WorkflowError("Multi-intent workflow has no executable steps", code="multi_intent_empty")

        intents: list[IntentPlan] = []
        graph_slices: list[tuple[int, int]] = []
        all_graph_actions: list[dict[str, Any]] = []
        for sub in root.sub_intents:
            intent = self._intent_from_sub(sub)
            if intent.kind == IntentKind.GRAPH_MUTATION:
                start = len(all_graph_actions)
                all_graph_actions.extend(intent.actions)
                graph_slices.append((start, len(intent.actions)))
            else:
                graph_slices.append((-1, 0))
            if intent.kind == IntentKind.GRAPH_QUERY and not isinstance(intent.query, dict):
                raise WorkflowError("Graph query must be an object", code="invalid_graph_query")
            if intent.kind == IntentKind.CONVERSE and not (intent.instruction or "").strip():
                raise WorkflowError("Converse step has no instruction", code="converse_instruction_missing")
            if intent.kind in {IntentKind.WIDGET_CREATE, IntentKind.WIDGET_MODIFY}:
                validate_app_id(intent.app_id)
                if not (intent.instruction or "").strip():
                    raise WorkflowError("Widget step has no instruction", code="widget_instruction_missing")
                if sub.extend_schema_props:
                    self.graph_db.effective_schemas(
                        {
                            "reused_schemas": [
                                {
                                    "id": schema_id,
                                    "reason": "Preflighted multi-intent extension",
                                    "extended_properties": properties,
                                }
                                for schema_id, properties in sub.extend_schema_props.items()
                            ],
                            "new_schemas": [],
                        }
                    )
            intents.append(intent)

        normalized_graph_actions = self.graph_db.preflight_actions(all_graph_actions) if all_graph_actions else []
        for intent, (start, count) in zip(intents, graph_slices, strict=True):
            if start >= 0:
                intent.actions = normalized_graph_actions[start : start + count]
        state.data["multi_preflight_intents"] = [intent.to_dict() for intent in intents]
        state.data["multi_preflight_complete"] = True
        return Continue(next_phase="multi_dispatch", summary="Multi-intent preflight passed")

    async def _phase_multi_dispatch(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        root = IntentPlan.from_dict(state.intent or {})
        index = int(state.data.get("multi_index", 0))
        if index >= len(root.sub_intents):
            results = list(state.data.get("multi_results") or [])
            content = "\n\n".join(str(item.get("message") or "") for item in results if item.get("message"))
            content = content or (
                "✅ 所有步骤已完成。" if state.data.get("language") == "zh" else "✅ All steps completed."
            )
            message, created = self._save_agent_message(run, state, content)
            if created:
                await self._emit(run, self._reply_payload(message))
            artifacts = [artifact for item in results for artifact in item.get("artifacts", [])]
            return Succeeded(
                summary="Multi-intent saga completed",
                result={"message": content, "steps": results},
                artifacts=artifacts,
            )

        if not state.data.get("multi_preflight_complete"):
            raise WorkflowError("Multi-intent dispatch requires preflight", code="multi_preflight_missing")
        sub = root.sub_intents[index]
        preflighted = state.data.get("multi_preflight_intents") or []
        if index >= len(preflighted) or not isinstance(preflighted[index], dict):
            raise WorkflowError("Multi-intent preflight checkpoint is incomplete", code="multi_preflight_missing")
        intent = IntentPlan.from_dict(preflighted[index])
        state.data["current_intent"] = intent.to_dict()
        state.data["return_to_multi"] = True
        state.data["multi_index"] = index
        for key in self._WIDGET_KEYS | {"graph_actions"}:
            state.data.pop(key, None)
        if sub.extend_schema_props:
            state.data["pre_extend_schema_props"] = sub.extend_schema_props
        if sub.feedback:
            state.data["code_feedback"] = sub.feedback
        return Continue(next_phase=self._phase_for_intent(intent), summary=f"Starting saga step {index + 1}")

    @staticmethod
    def _intent_from_sub(sub: SubIntent) -> IntentPlan:
        if sub.kind == SubIntentKind.CONVERSE:
            return IntentPlan(
                kind=IntentKind.CONVERSE,
                instruction=sub.instruction or "",
                rationale="multi_intent",
            )
        if sub.kind == SubIntentKind.GRAPH_MUTATION:
            return IntentPlan(kind=IntentKind.GRAPH_MUTATION, actions=sub.actions, rationale="multi_intent")
        if sub.kind == SubIntentKind.GRAPH_QUERY:
            return IntentPlan(kind=IntentKind.GRAPH_QUERY, query=sub.query or {}, rationale="multi_intent")
        if sub.kind in {
            SubIntentKind.WIDGET_CREATE,
            SubIntentKind.WIDGET_MODIFY,
            SubIntentKind.WIDGET_EXTEND_SCHEMA,
            SubIntentKind.WIDGET_FIX_CODE,
            SubIntentKind.WIDGET_REWRITE,
        }:
            return IntentPlan(
                kind=IntentKind.WIDGET_CREATE if sub.kind == SubIntentKind.WIDGET_CREATE else IntentKind.WIDGET_MODIFY,
                app_id=sub.app_id,
                instruction=sub.instruction or sub.feedback or "",
                rationale="multi_intent",
            )
        raise WorkflowError(f"Unsupported sub-intent: {sub.kind}", code="unsupported_sub_intent")

    async def _phase_plan(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        if not intent.app_id:
            return Failed(summary="Missing App ID", error_code="app_id_missing", message="Widget intent has no app_id")
        candidate = state.data.get("plan_candidate")
        if not candidate:
            rework_feedback = str(state.data.pop("plan_rework_feedback", "") or "").strip()
            plan_instruction = intent.instruction or ""
            if rework_feedback:
                plan_instruction = f"{plan_instruction}\n\n[PLAN REWORK FEEDBACK]\n{rework_feedback[:12_000]}"
            candidate = await PlanGenerationService.generate_plan(
                instruction=plan_instruction,
                app_id=intent.app_id,
                schemas_context=str(state.data.get("plan_schema_context") or "")[:16_000],
                db_session=self._run_storage(state),
                language=str(state.data.get("language") or "zh"),
                audit_context=self._run_context(run, state).audit_context(),
                budget=self._model_budget(state),
            )
            state.data["plan_candidate"] = candidate
        await self._emit_activity(
            run,
            activity_id="plan:proposal",
            activity_type="plan",
            status="completed",
            summary="Development plan prepared",
            metadata={"app_id": intent.app_id},
        )
        state.phase = "wait_plan"
        return await self._wait(
            run,
            state,
            kind="plan_approval",
            prompt="Approve development plan",
            payload={"type": "plan_approval_request", "app_id": intent.app_id, "plan": candidate},
        )

    async def _phase_wait_plan(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        response = self._response(state)
        if response is None:
            raise WorkflowError("Plan approval response is missing", code="interaction_unresolved")
        action = self._approval(response)
        candidate = str(state.data.get("plan_candidate") or "")
        if action == "approve":
            approved_plan = str(response.get("plan") or candidate)
            if not approved_plan.strip():
                raise WorkflowError("Approved development plan is empty", code="approved_plan_empty")
            state.data["approved_plan"] = approved_plan
            return Continue(next_phase="align_schema", summary="Development plan approved")
        if action == "refine":
            refined = await PlanGenerationService.refine_plan(
                instruction=intent.instruction or "",
                app_id=intent.app_id or "",
                schemas_context=str(state.data.get("plan_schema_context") or "")[:16_000],
                current_plan=str(response.get("plan") or candidate),
                feedback=str(response.get("feedback") or ""),
                db_session=self._run_storage(state),
                language=str(state.data.get("language") or "zh"),
                audit_context=self._run_context(run, state).audit_context(),
                budget=self._model_budget(state),
            )
            state.data["plan_candidate"] = refined
            state.phase = "wait_plan"
            return await self._wait(
                run,
                state,
                kind="plan_approval",
                prompt="Approve refined development plan",
                payload={"type": "plan_approval_request", "app_id": intent.app_id, "plan": refined},
            )
        return Failed(
            summary="Development plan denied",
            error_code="approval_denied",
            message="User denied the development plan",
        )

    async def _phase_align_schema(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        proposal = state.data.get("schema_candidate")
        if not proposal:
            proposal = await SchemaAlignmentService.align_schemas(
                instruction=intent.instruction or "",
                app_id=intent.app_id or "",
                db=self.graph_db,
                db_session=self._run_storage(state),
                approved_plan=str(state.data.get("approved_plan") or ""),
                language=str(state.data.get("language") or "zh"),
                audit_context=self._run_context(run, state).audit_context(),
                budget=self._model_budget(state),
                capability_catalog=self.capability_catalog_factory(),
            )
            proposal = self._merge_preapproved_schema_props(
                proposal,
                state.data.get("pre_extend_schema_props") or {},
            )
            state.data.pop("pre_extend_schema_props", None)
            self.graph_db.effective_schemas(proposal)
            state.data["schema_candidate"] = proposal
        await self._emit_activity(
            run,
            activity_id="schema:proposal",
            activity_type="schema",
            status="completed",
            summary="Schema and capability proposal prepared",
            metadata={
                "app_id": intent.app_id,
                "reused_schema_count": len(proposal.get("reused_schemas") or []),
                "new_schema_count": len(proposal.get("new_schemas") or []),
                "capability_count": len(proposal.get("capabilities") or []),
            },
        )
        state.phase = "wait_schema"
        return await self._wait(
            run,
            state,
            kind="schema_approval",
            prompt="Approve database schema proposal",
            payload={
                "type": "schema_approval_request",
                "app_id": intent.app_id,
                "plan": str(state.data.get("approved_plan") or ""),
                "proposal": proposal,
            },
        )

    async def _phase_wait_schema(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        response = self._response(state)
        if response is None:
            raise WorkflowError("Schema approval response is missing", code="interaction_unresolved")
        action = self._approval(response)
        proposal = state.data.get("schema_candidate") or {}
        if action == "approve":
            edited_proposal = json.loads(json.dumps(response.get("proposal") or proposal))
            try:
                approved = validate_schema_capability_proposal(
                    edited_proposal,
                    self.capability_catalog_factory(),
                )
                effective_schemas = self.graph_db.effective_schemas(approved)
            except ValueError as exc:
                # Approval payloads are editable in the UI.  A dependency
                # error (for example deleting a schema while leaving it in a
                # Graph grant) is a correctable design issue, not a terminal
                # workflow failure.  Keep the edited draft and ask again with
                # an actionable deterministic diagnostic.
                diagnostic = " ".join(str(exc).strip().split())[:2_000]
                state.data["schema_candidate"] = edited_proposal
                state.data["schema_validation_errors"] = [diagnostic]
                state.phase = "wait_schema"
                return await self._wait(
                    run,
                    state,
                    kind="schema_approval",
                    prompt="Fix schema and capability proposal dependencies",
                    payload={
                        "type": "schema_approval_request",
                        "app_id": intent.app_id,
                        "plan": str(state.data.get("approved_plan") or ""),
                        "proposal": edited_proposal,
                        "validation_errors": [diagnostic],
                    },
                )
            approved_schema_ids = {
                str(item.get("id"))
                for item in [*approved.get("reused_schemas", []), *approved.get("new_schemas", [])]
                if item.get("id")
            }
            state.data.pop("schema_validation_errors", None)
            state.data["approved_schema"] = approved
            effective_by_id = {item["id"]: item for item in effective_schemas}
            schemas = [effective_by_id[schema_id] for schema_id in sorted(approved_schema_ids)]
            state.data["runtime_contract"] = RuntimeContract.create(
                app_id=intent.app_id or "",
                schemas=schemas,
                capabilities=approved["capabilities"],
            ).to_dict()
            state.data.pop("plan_schema_context", None)
            state.data.pop("plan_rework_feedback", None)
            return Continue(next_phase="stage_code", summary="Schema proposal approved")
        if action == "rework_plan":
            edited_proposal = response.get("proposal") or proposal
            rework_feedback = str(response.get("feedback") or "").strip() or (
                "Revise the plan so every feature is feasible with the user-edited schema and capability proposal."
            )
            state.data.pop("plan_candidate", None)
            state.data.pop("approved_plan", None)
            state.data.pop("schema_candidate", None)
            state.data.pop("runtime_contract", None)
            state.data["plan_rework_feedback"] = rework_feedback[:12_000]
            state.data["plan_schema_context"] = json.dumps(
                edited_proposal,
                ensure_ascii=False,
                sort_keys=True,
            )[:16_000]
            return Continue(next_phase="plan", summary="Returning to development plan")
        if action == "refine":
            refined = await SchemaAlignmentService.refine_proposal(
                instruction=intent.instruction or "",
                app_id=intent.app_id or "",
                current_proposal=response.get("proposal") or proposal,
                feedback=str(response.get("feedback") or ""),
                db=self.graph_db,
                db_session=self._run_storage(state),
                approved_plan=str(state.data.get("approved_plan") or ""),
                language=str(state.data.get("language") or "zh"),
                audit_context=self._run_context(run, state).audit_context(),
                budget=self._model_budget(state),
                capability_catalog=self.capability_catalog_factory(),
            )
            self.graph_db.effective_schemas(refined)
            state.data["schema_candidate"] = refined
            state.phase = "wait_schema"
            return await self._wait(
                run,
                state,
                kind="schema_approval",
                prompt="Approve refined database schema proposal",
                payload={
                    "type": "schema_approval_request",
                    "app_id": intent.app_id,
                    "plan": str(state.data.get("approved_plan") or ""),
                    "proposal": refined,
                },
            )
        return Failed(
            summary="Schema proposal denied",
            error_code="approval_denied",
            message="User denied the schema proposal",
        )

    @staticmethod
    def _merge_preapproved_schema_props(
        proposal: dict[str, Any], extensions: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        merged = json.loads(json.dumps(proposal))
        for schema_id, properties in extensions.items():
            for reuse in merged.setdefault("reused_schemas", []):
                if reuse.get("id") == schema_id:
                    reuse.setdefault("extended_properties", {}).update(properties)
                    break
            else:
                merged["reused_schemas"].append(
                    {"id": schema_id, "reason": "Approved multi-intent extension", "extended_properties": properties}
                )
        return merged

    def _manifest_v2_template(self, contract: dict[str, Any]) -> dict[str, Any]:
        app_id = str(contract.get("app_id") or "")
        existing: AppManifest | None = None
        get_manifest = getattr(self.app_manager, "get_manifest", None)
        if callable(get_manifest):
            try:
                existing = get_manifest(app_id)
            except Exception:
                logger.warning("Unable to load the existing Manifest while preparing the coding prompt", exc_info=True)
        if existing is not None:
            template = existing.to_dict()
        else:
            template = {
                "manifest_version": 2,
                "id": app_id,
                "title": " ".join(part.capitalize() for part in app_id.split("-")) or "Ambient App",
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": [],
                "capabilities": [],
            }
        template["manifest_version"] = 2
        template["id"] = app_id
        template["schema_refs"] = sorted(
            str(item["id"]) for item in contract.get("schemas", []) if isinstance(item, dict) and item.get("id")
        )
        template["capabilities"] = [grant.to_dict() for grant in normalize_grants(contract.get("capabilities", []))]
        for field in ("backend_type", "mcp_server", "agent_url"):
            template.pop(field, None)
        return template

    async def _phase_stage_code(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        if not intent.app_id:
            return Failed(summary="Missing App ID", error_code="app_id_missing", message="Widget intent has no app_id")
        previous = state.data.get("staged_app")
        retained_draft = self._staged_result(previous) if previous else None

        self._consume_model_turn(state, 1)
        contract = state.data.get("runtime_contract")
        if not isinstance(contract, dict):
            raise WorkflowError("Approved Runtime Contract is missing", code="runtime_contract_missing")
        schemas = list(contract.get("schemas") or [])
        schema_text = "\n".join(f"- Type '{item['id']}': {json.dumps(item.get('properties', {}))}" for item in schemas)
        manifest_template = self._manifest_v2_template(contract)
        instruction = (
            f"{intent.instruction or ''}\n\n[APPROVED DEVELOPMENT PLAN]\n"
            f"{state.data.get('approved_plan', '')}\n\n[GRAPH DATABASE SCHEMAS]\n{schema_text}"
            "\n\n[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]\n"
            f"{json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)}"
            "\n\n[REQUIRED MANIFEST V2 TEMPLATE]\n"
            f"{json.dumps(manifest_template, ensure_ascii=False, sort_keys=True, indent=2)}"
            "\n\n[MANIFEST V2 FIELD RULES]\n"
            "`intents` must be an array of unique, non-empty strings; never objects. "
            "`schema_refs` must also be an array of unique, non-empty strings. "
            "Keep every capability entry in the exact approved object shape."
            "\n\n[SYSTEM CAPABILITIES]\n"
            f"{self.capability_catalog_factory().render(AgentRole.CODING_AGENT)}"
        )
        if state.data.get("code_feedback"):
            instruction += f"\n\n[VERIFICATION FEEDBACK]\n{state.data['code_feedback']}"
        if self.app_diagnostic_loader is not None:
            diagnostics = self.app_diagnostic_loader(intent.app_id)
            if diagnostics:
                instruction += (
                    "\n\n[RECENT APP RUNTIME DIAGNOSTICS]\n"
                    "Use these structured failures to repair the App. Do not ignore them or silently downgrade "
                    "requested live behavior.\n"
                    f"{json.dumps(diagnostics, ensure_ascii=False, indent=2)[:16_000]}"
                )
        language = str(state.data.get("language") or "zh")
        coding_agent = str(state.model_snapshot.get("coding_agent") or "opencode")
        coding_agent_name = spec_for(coding_agent).name
        await self._emit_activity(
            run,
            activity_id="code:generation",
            activity_type="code",
            status="running",
            summary=f"{coding_agent_name} is generating the staged App",
            metadata={"agent": coding_agent, "app_id": intent.app_id},
        )

        async def on_coding_agent_update(payload: Any) -> None:
            await self._emit_callback_update(
                run,
                payload,
                activity_id="code:generation",
                summary=f"{coding_agent_name} is generating the staged App",
                mode="snapshot",
            )

        kwargs: dict[str, Any] = {"language": language, "on_update": on_coding_agent_update}
        try:
            runner_parameters = inspect.signature(self.coding_agent_runner).parameters
            supports_promote = "promote" in runner_parameters
            supports_coding_agent = "coding_agent" in runner_parameters
            supports_coding_agent_model = "coding_agent_model" in runner_parameters
            supports_staged_result = "staged_result" in runner_parameters
            supports_artifact_validator = "artifact_validator" in runner_parameters
            supports_repair_decider = "repair_decider" in runner_parameters
        except (TypeError, ValueError):
            supports_promote = True
            supports_coding_agent = True
            supports_coding_agent_model = True
            supports_staged_result = True
            supports_artifact_validator = True
            supports_repair_decider = True
        if supports_promote:
            kwargs["promote"] = False
        if supports_coding_agent:
            kwargs["coding_agent"] = coding_agent
        if supports_coding_agent_model:
            kwargs["coding_agent_model"] = dict(state.model_snapshot.get("coding_agent_config") or {})
        if supports_artifact_validator:
            kwargs["artifact_validator"] = lambda result: self._assert_staged_runtime_contract(
                result.staging_dir,
                contract,
            )
        if supports_repair_decider:
            kwargs["repair_decider"] = decide_widget_repair
        if retained_draft is not None:
            if not supports_staged_result:
                raise WorkflowError(
                    "Configured coding agent cannot resume the retained failed draft",
                    code="staged_artifact_resume_unsupported",
                )
            kwargs["staged_result"] = retained_draft
        try:
            generated = await self.coding_agent_runner(intent.app_id, instruction, **kwargs)
        except CodingAgentDraftError as exc:
            self._record_staged_app(state, exc.staged_result, coding_agent, validation_error=str(exc))
            state.data["repair_decision"] = {
                "action": exc.repair_action,
                "reason": exc.repair_reason,
                "finding": exc.finding,
            }
            finding = exc.finding if isinstance(exc.finding, dict) else {}
            await self._emit_activity(
                run,
                activity_id="repair:auto",
                activity_type="repair",
                status="failed",
                summary="Automatic repair stopped",
                detail=str(finding.get("message") or exc)[:12_000],
                metadata={
                    "action": exc.repair_action,
                    "reason": exc.repair_reason,
                    "code": finding.get("code"),
                    "stage": finding.get("stage"),
                    "attempt": finding.get("attempt"),
                    "artifact_hash": finding.get("artifact_hash"),
                },
            )
            raise WorkflowError(str(exc), code=exc.error_code) from exc
        if isinstance(generated, CodingAgentStagedResult):
            self._record_staged_app(state, generated, coding_agent)
            validate_coding_agent_staging(generated)
            self._assert_staged_runtime_contract(generated.staging_dir, contract)
            state.data.pop("repair_decision", None)
        else:
            raise WorkflowError("Coding agent did not return a staged artifact", code="staged_artifact_missing")
        if generated.repair_attempts > 0:
            latest_finding = generated.repair_findings[-1] if generated.repair_findings else {}
            await self._emit_activity(
                run,
                activity_id="repair:auto",
                activity_type="repair",
                status="completed",
                summary=f"Automatic repair completed after {generated.repair_attempts} attempt(s)",
                detail=str(latest_finding.get("message") or "")[:12_000] or None,
                metadata={
                    "repair_count": generated.repair_attempts,
                    "code": latest_finding.get("code"),
                    "stage": latest_finding.get("stage"),
                    "attempt": latest_finding.get("attempt"),
                    "artifact_hash": latest_finding.get("artifact_hash"),
                },
            )
        await self._emit_activity(
            run,
            activity_id="code:generation",
            activity_type="code",
            status="completed",
            summary="Staged App generated",
            metadata={"agent": coding_agent, "app_id": intent.app_id},
        )
        return Continue(next_phase="verify", summary="Staged App generated")

    @staticmethod
    def _record_staged_app(
        state: AgentRunState,
        result: CodingAgentStagedResult,
        coding_agent: str,
        *,
        validation_error: str | None = None,
    ) -> None:
        state.data["staged_app"] = {
            "output": result.output[-64_000:],
            "app_id": result.app_id,
            "staging_dir": str(result.staging_dir),
            "live_dir": str(result.live_dir),
            "coding_agent": coding_agent,
            "repair_attempts": result.repair_attempts,
            "repair_findings": list(result.repair_findings),
            "artifact_hash": result.artifact_hash,
        }
        if result.repair_findings:
            state.data["verification_findings"] = list(result.repair_findings)
            state.data["repair_count"] = result.repair_attempts
        if validation_error:
            state.data["code_feedback"] = validation_error[:12_000]

    @staticmethod
    def _staged_result(data: dict[str, Any]) -> CodingAgentStagedResult:
        if not isinstance(data, dict):
            raise WorkflowError("No retained staging artifact is available", code="staged_artifact_missing")
        return CodingAgentStagedResult(
            output=str(data.get("output") or ""),
            app_id=str(data["app_id"]),
            staging_dir=Path(str(data["staging_dir"])),
            live_dir=Path(str(data["live_dir"])),
            repair_attempts=int(data.get("repair_attempts") or 0),
            repair_findings=tuple(data.get("repair_findings") or ()),
            artifact_hash=str(data.get("artifact_hash") or ""),
        )

    def _staged_widget_code(self, state: AgentRunState) -> dict[str, str]:
        staged = state.data.get("staged_app") or {}
        result = self._staged_result(staged)
        controller = validate_coding_agent_staging(result)
        return {"js": controller.read_text(encoding="utf-8")}

    @staticmethod
    def _assert_staged_runtime_contract(staging_dir: Path, contract: dict[str, Any]) -> AppManifest:
        app_id = str(contract.get("app_id") or "")
        try:
            manifest = AppManifest.read(staging_dir / "manifest.json", expected_app_id=app_id)
        except (OSError, ManifestValidationError) as exc:
            raise WorkflowError(
                "Staged App is missing a valid Manifest V2",
                code="runtime_contract_mismatch",
            ) from exc
        expected_capabilities = normalize_grants(contract.get("capabilities", []))
        if manifest.capabilities != expected_capabilities or manifest.grants_digest != contract.get("grants_digest"):
            raise WorkflowError(
                "Staged App capabilities differ from the user-approved Runtime Contract",
                code="runtime_contract_mismatch",
            )
        if manifest.backend_type != "code" or manifest.mcp_server is not None or manifest.agent_url is not None:
            raise WorkflowError(
                "Generated Widgets cannot declare executable backend adapters",
                code="runtime_contract_mismatch",
            )
        expected_schema_refs = tuple(sorted(str(item["id"]) for item in contract.get("schemas", []) if item.get("id")))
        if tuple(sorted(manifest.schema_refs)) != expected_schema_refs:
            raise WorkflowError(
                "Staged App schema_refs differ from the user-approved Runtime Contract",
                code="runtime_contract_mismatch",
            )
        allowed_names = {".ambient-promotion.json", "README.md", "controller.js", "data", "manifest.json"}
        unexpected = sorted(path.name for path in staging_dir.iterdir() if path.name not in allowed_names)
        if unexpected:
            raise WorkflowError(
                f"Staged App contains files outside the Runtime Contract: {', '.join(unexpected)}",
                code="runtime_contract_mismatch",
            )
        return manifest

    async def _phase_verify(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        staged = state.data.get("staged_app")
        if not staged:
            raise WorkflowError("Verification has no staged artifact", code="staged_artifact_missing")

        await self._emit_activity(
            run,
            activity_id="verification:contract",
            activity_type="verification",
            status="running",
            summary="Verifying staged code and Database Schema",
        )
        contract = state.data.get("runtime_contract")
        if not isinstance(contract, dict):
            raise WorkflowError("Approved Runtime Contract is missing", code="runtime_contract_missing")
        schemas = list(contract.get("schemas") or [])
        diff = await SchemaVerificationService.diff(
            app_id=intent.app_id or "",
            widget_code=self._staged_widget_code(state),
            registered_schemas=schemas,
            db_session=self._run_storage(state),
            audit_context=self._run_context(run, state).audit_context(),
            budget=self._model_budget(state),
            capability_catalog=self.capability_catalog_factory(),
        )
        report = diff.to_markdown()
        state.data["verification_report"] = report
        state.data["verification_options"] = diff.to_per_field_payload()
        await self._emit_activity(
            run,
            activity_id="verification:contract",
            activity_type="verification",
            status="completed" if diff.is_clean else "failed",
            summary="Staged App verification passed" if diff.is_clean else "Verification found required changes",
            detail=report,
            metadata={
                "finding_count": len(state.data["verification_options"]),
                "clean": diff.is_clean,
            },
        )
        if diff.is_clean:
            state.data["verification_passed"] = True
            return Continue(next_phase="promote", summary="Staged App verified")
        state.phase = "wait_override"
        return await self._wait(
            run,
            state,
            kind="verification_approval",
            prompt="Resolve schema verification findings",
            payload={
                "type": "verification_approval_request",
                "app_id": intent.app_id,
                "report": report,
                "options": state.data["verification_options"],
            },
        )

    async def _phase_wait_override(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        staged = state.data.get("staged_app")
        if not staged:
            raise WorkflowError("Verification override has no staged artifact", code="staged_artifact_missing")
        response = self._response(state)
        if response is None:
            raise WorkflowError("Verification response is missing", code="interaction_unresolved")
        action = self._approval(response)
        if action == "approve":
            # Schema/capability findings predict runtime authorization or
            # ontology failures. Publishing them as an "override" only turns
            # a deterministic build error into a user-facing runtime error.
            # Keep the draft and require an explicit repair path.
            diagnostic = (
                "Schema and capability verification findings cannot be bypassed; "
                "rework the code, schema, or development plan."
            )
            state.data.pop("verification_override", None)
            state.phase = "wait_override"
            return await self._wait(
                run,
                state,
                kind="verification_approval",
                prompt="Choose a repair path for mandatory verification findings",
                payload={
                    "type": "verification_approval_request",
                    "app_id": self._current_intent(state).app_id,
                    "report": str(state.data.get("verification_report") or ""),
                    "options": state.data.get("verification_options") or [],
                    "validation_errors": [diagnostic],
                    "allowed_actions": ["rework_code", "rework_schema", "rework_plan"],
                },
            )
        if action == "rework_code":
            state.data["code_feedback"] = str(response.get("feedback") or state.data.get("verification_report") or "")
            # Keep the validated draft as the Coding Agent's editing base.
            # Discarding it turns a one-line verifier fix into a full
            # regeneration, loses useful context, and can exhaust the active
            # workflow budget before verification runs again.
            for key in (
                "verification_report",
                "verification_options",
                "verification_passed",
                "verification_override",
            ):
                state.data.pop(key, None)
            return Continue(next_phase="stage_code", summary="Reworking staged code")
        if action == "rework_schema":
            extensions = self._selected_schema_extensions(
                response.get("approved_options"),
                state.data.get("verification_options"),
            )
            if extensions:
                state.data["pre_extend_schema_props"] = extensions
            discard_coding_agent_staging(self._staged_result(staged))
            state.data.pop("staged_app", None)
            for key in (
                "schema_candidate",
                "schema_validation_errors",
                "approved_schema",
                "runtime_contract",
                "verification_report",
                "verification_options",
                "verification_passed",
                "verification_override",
            ):
                state.data.pop(key, None)
            return Continue(next_phase="align_schema", summary="Reworking schema proposal")
        if action == "rework_plan":
            plan_rework_feedback = str(response.get("feedback") or state.data.get("verification_report") or "").strip()
            plan_schema_context = json.dumps(
                state.data.get("approved_schema") or {},
                ensure_ascii=False,
                sort_keys=True,
            )[:16_000]
            discard_coding_agent_staging(self._staged_result(staged))
            for key in self._WIDGET_KEYS | {"pre_extend_schema_props"}:
                state.data.pop(key, None)
            state.data["plan_rework_feedback"] = plan_rework_feedback[:12_000]
            state.data["plan_schema_context"] = plan_schema_context
            return Continue(next_phase="plan", summary="Reworking development plan")
        return Failed(
            summary="Verification override denied",
            error_code="approval_denied",
            message=f"Verification action was denied or unknown: {action}",
        )

    @staticmethod
    def _selected_schema_extensions(
        raw_selected: Any,
        raw_available: Any,
    ) -> dict[str, dict[str, str]]:
        """Compile UI selections from server-issued verification findings.

        The client selects only `(node_type, property_name)` pairs. Types and
        actions always come from the checkpointed server findings so a forged
        response cannot introduce arbitrary ontology fields.
        """

        if not isinstance(raw_selected, list) or not isinstance(raw_available, list):
            return {}
        selected = {
            (str(item.get("node_type") or ""), str(item.get("property_name") or ""))
            for item in raw_selected
            if isinstance(item, dict)
        }
        allowed_types = {"string", "integer", "number", "boolean"}
        extensions: dict[str, dict[str, str]] = {}
        for item in raw_available:
            if not isinstance(item, dict):
                continue
            node_type = str(item.get("node_type") or "")
            property_name = str(item.get("property_name") or "")
            detected_type = str(item.get("detected_type") or "")
            if (
                (node_type, property_name) not in selected
                or not node_type
                or not property_name
                or property_name == "*"
                or item.get("action") != "extend_schema"
                or detected_type not in allowed_types
            ):
                continue
            extensions.setdefault(node_type, {})[property_name] = detected_type
        return extensions

    async def _phase_promote(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        if not state.data.get("verification_passed"):
            raise WorkflowError(
                "An unverified artifact cannot be promoted",
                code="artifact_not_verified",
            )
        return await self._publish_widget(run, state, self._current_intent(state))

    async def _publish_widget(self, run: dict[str, Any], state: AgentRunState, intent: IntentPlan) -> StepOutcomeValue:
        staged = state.data.get("staged_app") or {}
        await self._emit_activity(
            run,
            activity_id="artifact:publish",
            activity_type="artifact",
            status="running",
            summary="Publishing verified App",
            metadata={"app_id": intent.app_id},
        )
        schema_effect_key = f"agent-run:{run['id']}:schema_promote"
        schema_change: dict[str, Any] | None = None
        staged_result = self._staged_result(staged)
        recovered_controller: Path | None = None
        contract = state.data.get("runtime_contract")
        if not isinstance(contract, dict):
            raise WorkflowError("Approved Runtime Contract is missing", code="runtime_contract_missing")
        recovered_controller = validate_coding_agent_promotion(staged_result, run["id"])
        contract_dir = staged_result.live_dir if recovered_controller is not None else staged_result.staging_dir
        self._assert_staged_runtime_contract(contract_dir, contract)
        if state.data.get("approved_schema"):
            state.data["effect_in_flight"] = "schema_atomic_commit"
            try:
                schema_change = await asyncio.to_thread(
                    self.graph_db.apply_schema_proposal_atomic,
                    state.data["approved_schema"],
                    idempotency_key=schema_effect_key,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                state.data.pop("effect_in_flight", None)
                raise
            state.data.pop("effect_in_flight", None)
            state.data["schema_snapshot"] = schema_change["snapshot"]
        try:
            controller = recovered_controller or validate_coding_agent_staging(staged_result)
            artifact_hash = hashlib.sha256(controller.read_bytes()).hexdigest()
            if recovered_controller is None:
                marker = staged_result.staging_dir / ".ambient-promotion.json"
                marker.write_text(
                    json.dumps({"run_id": run["id"], "artifact_hash": artifact_hash}),
                    encoding="utf-8",
                )
                state.data["effect_in_flight"] = "app_atomic_promote"
                await asyncio.to_thread(promote_coding_agent_staging, staged_result)
                state.data.pop("effect_in_flight", None)
            state.artifact_refs.append({"type": "app", "id": staged_result.app_id, "sha256": artifact_hash})
        except (Exception, asyncio.CancelledError):
            if schema_change is not None:
                self.graph_db.restore_schema_snapshot(
                    schema_change["snapshot"],
                    idempotency_key=schema_effect_key,
                )
            raise

        state.data["effects_committed"] = True
        state.data["non_compensable_effect"] = True
        widget = self.app_manager.get_app_files(intent.app_id or "")
        if not widget or not str(widget.get("js") or "").strip():
            raise WorkflowError(
                "Published App is missing its controller artifact",
                code="published_artifact_missing",
                effect_state="unknown",
            )
        storage = self._run_storage(state)
        canvas = storage.get_canvas_config()
        published_app_id = str(widget.get("id") or intent.app_id or "")
        canvas["open_app_ids"] = [
            *[app_id for app_id in canvas["open_app_ids"] if app_id != published_app_id],
            published_app_id,
        ]
        canvas["active_app_id"] = published_app_id
        storage.save_canvas_config(canvas)
        app_title = str(widget.get("title") or widget.get("id") or intent.app_id or "App")
        content = (
            f"✅ App“{app_title}”已生成、验证并发布。"
            if state.data.get("language") == "zh"
            else f'✅ App "{app_title}" was generated, verified, and published.'
        )
        artifacts = [{"type": "app", "id": widget.get("id")}]
        await self._emit_activity(
            run,
            activity_id="artifact:publish",
            activity_type="artifact",
            status="completed",
            summary="Verified App published",
            metadata={
                "artifact_type": "app",
                "artifact_id": widget.get("id"),
                "manifest_revision": widget.get("manifest_revision"),
            },
        )
        await self._emit(
            run,
            {
                "type": "artifact_ready",
                "artifact_type": "app",
                "artifact_id": str(widget.get("id") or ""),
                "title": str(widget.get("title") or widget.get("id") or ""),
                "summary": "Verified App is ready",
            },
            project_to_chat=False,
        )
        result = {"message": content, "app_id": widget.get("id")}
        if state.data.get("return_to_multi"):
            await self._emit(run, {"type": "widget", "widget": widget})
            return await self._finish_subflow(
                run,
                state,
                content=content,
                result=result,
                artifacts=artifacts,
            )

        message, created = self._save_agent_message(run, state, content)
        if created:
            await self._emit(run, self._reply_payload(message))
        await self._emit(run, {"type": "widget", "widget": widget})
        return Succeeded(summary="Agent task completed", result=result, artifacts=artifacts)

    async def _finish_subflow(
        self,
        run: dict[str, Any],
        state: AgentRunState,
        *,
        content: str,
        result: dict[str, Any],
        artifacts: list[dict[str, Any]] | None = None,
    ) -> StepOutcomeValue:
        artifacts = artifacts or []
        if state.data.get("return_to_multi"):
            state.data.setdefault("multi_results", []).append(
                {"message": content, "result": result, "artifacts": artifacts}
            )
            state.data["multi_index"] = int(state.data.get("multi_index", 0)) + 1
            state.data.pop("current_intent", None)
            state.data.pop("return_to_multi", None)
            for key in self._WIDGET_KEYS | {"graph_actions", "pre_extend_schema_props"}:
                state.data.pop(key, None)
            return Continue(next_phase="multi_dispatch", summary="Saga step completed", output=result)

        message, created = self._save_agent_message(run, state, content)
        if created:
            await self._emit(run, self._reply_payload(message))
        return Succeeded(summary="Agent task completed", result=result, artifacts=artifacts)
