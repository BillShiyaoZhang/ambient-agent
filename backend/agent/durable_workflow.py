from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
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
from backend.app_manager import AppManager
from backend.app_manifest import validate_app_id
from backend.context_manager import ContextManager
from backend.graph_db import GraphDatabase
from backend.graph_query_engine import execute_graph_query
from backend.llm_config import LLMConfigError, LLMConfigStore, ModelSelection
from backend.llm_runtime import use_model_selections
from backend.models import ChatMessage, ChatSession
from backend.opencode_service import (
    CodingAgentStagedResult,
    discard_coding_agent_staging as discard_opencode_staging,
    promote_coding_agent_staging as promote_opencode_staging,
    validate_coding_agent_promotion as validate_opencode_promotion,
    validate_coding_agent_staging as validate_opencode_staging,
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
from backend.schema_alignment import SchemaAlignmentService
from backend.schema_verification import SchemaVerificationService
from backend.workspace_storage import WorkspaceStorage

logger = logging.getLogger("agent.durable_workflow")

EventSink = Callable[[str, dict[str, Any]], Awaitable[None] | None]
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
        "approved_plan",
        "schema_candidate",
        "approved_schema",
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
        coding_agent_runner: CodingAgentRunner | None = None,
        opencode_runner: CodingAgentRunner | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.run_store = run_store
        self.app_manager = app_manager
        self.graph_db = graph_db
        self.llm_config_store = llm_config_store
        self.coding_agent_runner = coding_agent_runner or opencode_runner
        if self.coding_agent_runner is None:
            raise ValueError("A coding-agent runner is required")
        # Compatibility for test hosts and extensions that still reference the
        # pre-registry attribute directly.
        self.opencode_runner = self.coding_agent_runner
        self.event_sink = event_sink
        self._event_buffer: ContextVar[list[PendingRunEvent] | None] = ContextVar(
            "durable_agent_event_buffer",
            default=None,
        )

    async def __call__(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        token = self._event_buffer.set([])
        try:
            outcome = await self._reduce_once(run, state)
            events = list(self._event_buffer.get() or [])
        finally:
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
                with use_model_selections(primary, fast):
                    outcome = await handler(run, state)
            if isinstance(outcome, Failed):
                return await self._failure(
                    state,
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
                code=exc.code,
                message=str(exc),
                retryable=False,
                effect_state="none",
            )
        except WorkflowError as exc:
            return await self._failure(
                state,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                effect_state=exc.effect_state,
            )
        except Exception as exc:
            logger.exception("Durable workflow phase %s failed", state.phase)
            return await self._failure(
                state,
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
            state.data["active_seconds"] = float(state.data.get("active_seconds", 0.0)) + (
                time.monotonic() - started
            )

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

    async def _emit(self, run: dict[str, Any], payload: Any, *, project_to_chat: bool = True) -> None:
        if isinstance(payload, dict):
            wire_payload = payload
            event_type = str(payload.get("type") or "agent_update")
        else:
            wire_payload = {
                "type": "reply",
                "message": {
                    "id": -1,
                    "sender": "agent",
                    "role": "agent",
                    "content": str(payload),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            }
            event_type = "agent_update"
        buffer = self._event_buffer.get()
        if buffer is None:
            raise WorkflowError("Reducer event emitted outside a step transaction", code="event_outside_step")
        buffer.append(
            PendingRunEvent(
                type=event_type,
                payload=wire_payload,
                project_to_chat=project_to_chat,
            )
        )

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
        if (
            state.budget.max_tokens is not None
            and state.budget.tokens_used > state.budget.max_tokens
        ):
            raise BudgetExhaustedError("Agent Run exceeded its token budget")
        if (
            state.budget.max_cost_usd is not None
            and state.budget.cost_usd > state.budget.max_cost_usd
        ):
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
        await self._emit(run, wire_payload)
        language = str(state.data.get("language") or "zh")
        waiting_text = {
            "plan_approval": (
                "⏳ 等待开发计划 Plan 确认中...",
                "⏳ Waiting for development plan approval...",
            ),
            "schema_approval": (
                "⏳ 等待数据库 Schema 确认中...",
                "⏳ Waiting for database schema approval...",
            ),
            "verification_approval": (
                "⏳ 等待校验处理决定...",
                "⏳ Waiting for a verification decision...",
            ),
        }.get(kind)
        if waiting_text:
            await self._emit(run, waiting_text[0] if language == "zh" else waiting_text[1])
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
            # current phase must not silently roll earlier results back.
            retries[state.phase] = phase_retries + 1
            staged = state.data.get("staged_app")
            if staged:
                try:
                    discard_opencode_staging(self._staged_result(staged))
                    state.data.pop("staged_app", None)
                except Exception:
                    logger.warning("Unable to discard retry staging", exc_info=True)
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

        staged = state.data.get("staged_app")
        if staged:
            try:
                discard_opencode_staging(self._staged_result(staged))
                state.data.pop("staged_app", None)
            except Exception:
                logger.warning("Unable to discard failed staged App", exc_info=True)

        return Failed(
            summary="Agent task failed",
            error_code=code,
            message=message,
            retryable=False,
            effect_state=effect_state if effect_state in {"none", "committed", "unknown"} else "unknown",
        )

    def cleanup_state(self, state: AgentRunState | dict[str, Any] | None) -> None:
        """Best-effort cleanup for cancelled/abandoned retained staging artifacts."""

        if not state:
            return
        normalized = state if isinstance(state, AgentRunState) else AgentRunState.model_validate(state)
        staged = normalized.data.get("staged_app")
        if staged and not staged.get("legacy_promoted") and not normalized.data.get("non_compensable_effect"):
            try:
                discard_opencode_staging(self._staged_result(staged))
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
        context_summary = self._ensure_context_summary(state, storage)
        orchestrator = AgentOrchestrator(
            db_session=storage,
            app_manager=self.app_manager,
            run_context=self._run_context(run, state),
            context_summary=context_summary,
            artifact_ids=[
                str(ref.get("id"))
                for ref in state.artifact_refs
                if isinstance(ref, dict) and ref.get("id")
            ],
            tool_loop_budget=self._model_budget(state),
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
        context_summary = self._ensure_context_summary(state, storage)
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
        orchestrator = AgentOrchestrator(
            db_session=storage,
            app_manager=self.app_manager,
            run_context=self._run_context(run, state),
            context_summary=context_summary,
            artifact_ids=[
                str(ref.get("id"))
                for ref in state.artifact_refs
                if isinstance(ref, dict) and ref.get("id")
            ],
            tool_loop_budget=self._model_budget(
                state,
                max_iterations=remaining_model_turns,
                max_tool_calls=12,
            ),
        )

        async def on_update(payload: Any) -> None:
            await self._emit(run, payload)

        message, widget = await orchestrator._handle_converse(
            plan=intent,
            session_id=state.session_id or "default-session",
            content=str((run.get("input") or {}).get("content") or ""),
            language=language,
            on_update=on_update,
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

    async def _phase_wait_graph_approval(
        self, run: dict[str, Any], state: AgentRunState
    ) -> StepOutcomeValue:
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
                idempotency_key=(
                    f"agent-run:{run['id']}:graph_commit:{int(state.data.get('multi_index', 0))}"
                ),
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

        normalized_graph_actions = (
            self.graph_db.preflight_actions(all_graph_actions) if all_graph_actions else []
        )
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
            content = content or ("✅ 所有步骤已完成。" if state.data.get("language") == "zh" else "✅ All steps completed.")
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
            candidate = await PlanGenerationService.generate_plan(
                instruction=intent.instruction or "",
                app_id=intent.app_id,
                schemas_context="",
                db_session=self._run_storage(state),
                language=str(state.data.get("language") or "zh"),
                audit_context=self._run_context(run, state).audit_context(),
                budget=self._model_budget(state),
            )
            state.data["plan_candidate"] = candidate
        await self._emit(run, "🔍 正在为您制定开发计划 Plan..." if state.data.get("language") == "zh" else "🔍 Formulating development plan...")
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
                schemas_context="",
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
            )
            proposal = self._merge_preapproved_schema_props(
                proposal,
                state.data.get("pre_extend_schema_props") or {},
            )
            self.graph_db.effective_schemas(proposal)
            state.data["schema_candidate"] = proposal
        await self._emit(run, "🔍 正在对齐数据库 Schema..." if state.data.get("language") == "zh" else "🔍 Aligning database schemas...")
        state.phase = "wait_schema"
        return await self._wait(
            run,
            state,
            kind="schema_approval",
            prompt="Approve database schema proposal",
            payload={"type": "schema_approval_request", "app_id": intent.app_id, "proposal": proposal},
        )

    async def _phase_wait_schema(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        response = self._response(state)
        if response is None:
            raise WorkflowError("Schema approval response is missing", code="interaction_unresolved")
        action = self._approval(response)
        proposal = state.data.get("schema_candidate") or {}
        if action == "approve":
            approved = response.get("proposal") or proposal
            self.graph_db.effective_schemas(approved)
            state.data["approved_schema"] = approved
            return Continue(next_phase="stage_code", summary="Schema proposal approved")
        if action == "rework_plan":
            state.data.pop("plan_candidate", None)
            state.data.pop("approved_plan", None)
            state.data.pop("schema_candidate", None)
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
            )
            self.graph_db.effective_schemas(refined)
            state.data["schema_candidate"] = refined
            state.phase = "wait_schema"
            return await self._wait(
                run,
                state,
                kind="schema_approval",
                prompt="Approve refined database schema proposal",
                payload={"type": "schema_approval_request", "app_id": intent.app_id, "proposal": refined},
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

    async def _phase_stage_code(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        if not intent.app_id:
            return Failed(summary="Missing App ID", error_code="app_id_missing", message="Widget intent has no app_id")
        previous = state.data.get("staged_app")
        if previous:
            discard_opencode_staging(self._staged_result(previous))
            state.data.pop("staged_app", None)

        self._consume_model_turn(state, 1)
        schemas = self.graph_db.effective_schemas(state.data.get("approved_schema") or {})
        schema_text = "\n".join(f"- Type '{item['id']}': {json.dumps(item.get('properties', {}))}" for item in schemas)
        instruction = (
            f"{intent.instruction or ''}\n\n[APPROVED DEVELOPMENT PLAN]\n"
            f"{state.data.get('approved_plan', '')}\n\n[GRAPH DATABASE SCHEMAS]\n{schema_text}"
        )
        if state.data.get("code_feedback"):
            instruction += f"\n\n[VERIFICATION FEEDBACK]\n{state.data['code_feedback']}"
        language = str(state.data.get("language") or "zh")
        coding_agent = str(state.model_snapshot.get("coding_agent") or "opencode")
        coding_agent_name = "Codex" if coding_agent == "codex" else "OpenCode"
        await self._emit(
            run,
            f"🛠️ 正在启动 {coding_agent_name} 开发者智能体并生成隔离 staging App..."
            if language == "zh"
            else f"🛠️ Starting the {coding_agent_name} agent in an isolated staging App...",
        )

        kwargs: dict[str, Any] = {"language": language, "on_update": lambda payload: self._emit(run, payload)}
        try:
            runner_parameters = inspect.signature(self.coding_agent_runner).parameters
            supports_promote = "promote" in runner_parameters
            supports_coding_agent = "coding_agent" in runner_parameters
        except (TypeError, ValueError):
            supports_promote = True
            supports_coding_agent = True
        if supports_promote:
            kwargs["promote"] = False
        if supports_coding_agent:
            kwargs["coding_agent"] = coding_agent
        generated = await self.coding_agent_runner(intent.app_id, instruction, **kwargs)
        if isinstance(generated, CodingAgentStagedResult):
            validate_opencode_staging(generated)
            state.data["staged_app"] = {
                "output": generated.output[-64_000:],
                "app_id": generated.app_id,
                "staging_dir": str(generated.staging_dir),
                "live_dir": str(generated.live_dir),
                "coding_agent": coding_agent,
            }
        elif isinstance(generated, str) and not supports_promote:
            # Compatibility for injected legacy test runners. Production always
            # uses the retained staging API.
            state.data["staged_app"] = {
                "output": generated[-64_000:],
                "app_id": intent.app_id,
                "legacy_promoted": True,
            }
        else:
            raise WorkflowError("Coding agent did not return a staged artifact", code="staged_artifact_missing")
        return Continue(next_phase="verify", summary="Staged App generated")

    @staticmethod
    def _staged_result(data: dict[str, Any]) -> CodingAgentStagedResult:
        if not isinstance(data, dict) or data.get("legacy_promoted"):
            raise WorkflowError("No retained staging artifact is available", code="staged_artifact_missing")
        return CodingAgentStagedResult(
            output=str(data.get("output") or ""),
            app_id=str(data["app_id"]),
            staging_dir=Path(str(data["staging_dir"])),
            live_dir=Path(str(data["live_dir"])),
        )

    def _staged_widget_code(self, state: AgentRunState) -> dict[str, str]:
        staged = state.data.get("staged_app") or {}
        if staged.get("legacy_promoted"):
            files = self.app_manager.get_app_files(str(staged["app_id"])) or {}
            return {"js": str(files.get("js") or "")}
        result = self._staged_result(staged)
        controller = validate_opencode_staging(result)
        return {"js": controller.read_text(encoding="utf-8")}

    async def _phase_verify(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        intent = self._current_intent(state)
        staged = state.data.get("staged_app")
        if not staged:
            raise WorkflowError("Verification has no staged artifact", code="staged_artifact_missing")

        await self._emit(
            run,
            "🔍 正在校验代码与 Database Schema..."
            if state.data.get("language") == "zh"
            else "🔍 Verifying staged code and Database Schema...",
        )
        schemas = self.graph_db.effective_schemas(state.data.get("approved_schema") or {})
        diff = await SchemaVerificationService.diff(
            app_id=intent.app_id or "",
            widget_code=self._staged_widget_code(state),
            registered_schemas=schemas,
            db_session=self._run_storage(state),
            audit_context=self._run_context(run, state).audit_context(),
            budget=self._model_budget(state),
        )
        report = diff.to_markdown()
        state.data["verification_report"] = report
        state.data["verification_options"] = diff.to_per_field_payload()
        await self._emit(run, f"### 🔍 Database Schema Verification Report\n\n{report}")
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
        del run
        staged = state.data.get("staged_app")
        if not staged:
            raise WorkflowError("Verification override has no staged artifact", code="staged_artifact_missing")
        response = self._response(state)
        if response is None:
            raise WorkflowError("Verification response is missing", code="interaction_unresolved")
        action = self._approval(response)
        if action == "approve":
            state.data["verification_override"] = True
            return Continue(next_phase="promote", summary="Verification override approved")
        if action == "rework_code":
            state.data["code_feedback"] = str(
                response.get("feedback") or state.data.get("verification_report") or ""
            )
            if not staged.get("legacy_promoted"):
                discard_opencode_staging(self._staged_result(staged))
            state.data.pop("staged_app", None)
            return Continue(next_phase="stage_code", summary="Reworking staged code")
        if action == "rework_schema":
            if not staged.get("legacy_promoted"):
                discard_opencode_staging(self._staged_result(staged))
            state.data.pop("staged_app", None)
            state.data.pop("schema_candidate", None)
            state.data.pop("approved_schema", None)
            return Continue(next_phase="align_schema", summary="Reworking schema proposal")
        if action == "rework_plan":
            if not staged.get("legacy_promoted"):
                discard_opencode_staging(self._staged_result(staged))
            for key in self._WIDGET_KEYS:
                state.data.pop(key, None)
            return Continue(next_phase="plan", summary="Reworking development plan")
        return Failed(
            summary="Verification override denied",
            error_code="approval_denied",
            message=f"Verification action was denied or unknown: {action}",
        )

    async def _phase_promote(self, run: dict[str, Any], state: AgentRunState) -> StepOutcomeValue:
        if not state.data.get("verification_passed") and not state.data.get("verification_override"):
            raise WorkflowError(
                "An unverified artifact cannot be promoted",
                code="artifact_not_verified",
            )
        return await self._publish_widget(run, state, self._current_intent(state))

    async def _publish_widget(
        self, run: dict[str, Any], state: AgentRunState, intent: IntentPlan
    ) -> StepOutcomeValue:
        staged = state.data.get("staged_app") or {}
        schema_effect_key = f"agent-run:{run['id']}:schema_promote"
        schema_change: dict[str, Any] | None = None
        staged_result: CodingAgentStagedResult | None = None
        recovered_controller: Path | None = None
        if not staged.get("legacy_promoted"):
            staged_result = self._staged_result(staged)
            recovered_controller = validate_opencode_promotion(staged_result, run["id"])
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
            if not staged.get("legacy_promoted"):
                if staged_result is None:
                    raise WorkflowError("Promotion has no staging handle", code="staged_artifact_missing")
                controller = recovered_controller or validate_opencode_staging(staged_result)
                artifact_hash = hashlib.sha256(controller.read_bytes()).hexdigest()
                if recovered_controller is None:
                    marker = staged_result.staging_dir / ".ambient-promotion.json"
                    marker.write_text(
                        json.dumps({"run_id": run["id"], "artifact_hash": artifact_hash}),
                        encoding="utf-8",
                    )
                    state.data["effect_in_flight"] = "app_atomic_promote"
                    await asyncio.to_thread(promote_opencode_staging, staged_result)
                    state.data.pop("effect_in_flight", None)
                state.artifact_refs.append(
                    {"type": "app", "id": staged_result.app_id, "sha256": artifact_hash}
                )
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
        report = str(state.data.get("verification_report") or "Explicit verification override approved")
        output = str(staged.get("output") or "")
        coding_agent = str(staged.get("coding_agent") or state.model_snapshot.get("coding_agent") or "opencode")
        coding_agent_name = "Codex" if coding_agent == "codex" else "OpenCode"
        content = f"{coding_agent_name} Execution Log:\n\n```\n{output}\n```\n\n### 🔍 Database Schema Verification Report\n\n{report}"

        from backend.agent_parser import serialize_widget_to_text

        storage = self._run_storage(state)
        code_message = ChatMessage(
            session_id=state.session_id,
            role="code",
            sender="agent",
            content=serialize_widget_to_text(widget),
            run_id=f"{run['id']}:artifact",
        )
        if self._message_for_run(storage, state.session_id or "", code_message.run_id) is None:
            storage.add(code_message)
            storage.commit()
        artifacts = [{"type": "app", "id": widget.get("id")}]
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
