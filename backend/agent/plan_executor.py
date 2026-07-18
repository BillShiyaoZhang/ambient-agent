"""Plan executor abstraction.

A ``PlanExecutor`` performs a possibly multi-stage plan-and-execute sequence
that ultimately yields a final output (the agent reply) plus, optionally, a
widget to deploy. Two concrete implementations are provided:

* ``CodingPlanExecutor`` — drives the existing widget generation pipeline
  (Plan → Schema → Code → Verify).
* ``MutationPlanExecutor`` — executes a multi-step graph mutation after user
  approval.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agent.intent_plan import IntentPlan
from backend.opencode_service import run_opencode_agent_acp


@dataclass
class PlanPhaseResult:
    """Outcome of a single PlanExecutor.run_plan invocation."""

    success: bool
    output: str = ""
    error: str | None = None
    extra: dict[str, Any] | None = None


async def _await_plan_approval(
    plan_str: str,
    app_id: str,
    session_id: str,
    on_update: Callable[[Any], Any],
) -> tuple[str, str | None]:
    """Helper used by MutationPlanExecutor to wait for the user's plan approval.

    This wraps the active_plan_requests future mechanism used by the
    WebSocket layer so the executor can pause until the user clicks
    approve/deny/refine.
    """
    import asyncio
    import uuid

    from backend.agent.harness import active_plan_requests  # local import to avoid cycles

    request_id = f"plan-act-{uuid.uuid4()}"
    future = asyncio.Future()
    active_plan_requests[request_id] = future

    try:
        await on_update(
            {
                "type": "plan_approval_request",
                "request_id": request_id,
                "app_id": app_id,
                "plan": plan_str,
            }
        )
        action, response_data = await future
        approved_plan = response_data if action == "approve" else plan_str
        return action, approved_plan
    finally:
        active_plan_requests.pop(request_id, None)


class PlanExecutor(ABC):
    """Abstract base class for plan executors."""

    @abstractmethod
    async def run_plan(
        self,
        plan: IntentPlan,
        instruction: str,
        on_update: Callable[[Any], Any],
        language: str = "zh",
    ) -> PlanPhaseResult:  # pragma: no cover - abstract
        """Execute the given plan; return a result describing its outcome."""


class CodingPlanExecutor(PlanExecutor):
    """Drives the existing widget generation pipeline."""

    def __init__(self, graph_db_factory=None, app_manager=None, run_opencode_agent_acp_fn=None):
        self._graph_db_factory = graph_db_factory
        self._app_manager = app_manager
        self._run_opencode_agent_acp_fn = run_opencode_agent_acp_fn

    async def run_plan(
        self,
        plan: IntentPlan,
        instruction: str,
        on_update: Callable[[Any], Any],
        language: str = "zh",
    ) -> PlanPhaseResult:
        if not plan.app_id:
            return PlanPhaseResult(
                success=False,
                error="widget_create/widget_modify requires app_id",
            )

        runner = self._run_opencode_agent_acp_fn or run_opencode_agent_acp
        try:
            output = await runner(plan.app_id, plan.instruction or instruction, language=language, on_update=on_update)
        except Exception as e:
            return PlanPhaseResult(success=False, error=f"opencode failed: {e!s}")

        return PlanPhaseResult(
            success=True,
            output=str(output),
            extra={"app_id": plan.app_id},
        )


class MutationPlanExecutor(PlanExecutor):
    """Multi-step graph-mutation executor with plan approval.

    Captures a snapshot of the relevant graph state, presents it as a plan
    for user approval, and on approval applies the actions with rollback
    tickets (so each step is reversible).
    """

    def __init__(
        self,
        graph_db_factory=None,
        soft_window_seconds: float = 60.0,
    ):
        self._graph_db_factory = graph_db_factory
        self._soft_window_seconds = soft_window_seconds

    async def run_plan(
        self,
        plan: IntentPlan,
        instruction: str,
        on_update: Callable[[Any], Any],
        language: str = "zh",
    ) -> PlanPhaseResult:
        actions = [a for a in (plan.actions or []) if isinstance(a, dict)]
        if not actions:
            return PlanPhaseResult(success=False, error="plan had no actions")

        from backend.graph_db import GraphDatabase  # local import

        graph_db: GraphDatabase | None = self._graph_db_factory() if self._graph_db_factory else None
        if graph_db is None:
            import os

            workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
            graph_db = GraphDatabase(workspace_dir)

        summary = self._render_plan(actions, language)
        action_label, approved_plan = await _await_plan_approval(
            plan_str=summary,
            app_id=plan.app_id or "",
            session_id=plan.rationale or "",
            on_update=on_update,
        )

        is_zh = language == "zh"
        if action_label != "approve":
            return PlanPhaseResult(
                success=False,
                error="user denied the plan",
                output="plan_and_act: 用户拒绝计划，未执行任何 mutation。" if is_zh else "plan_and_act: User denied the plan, no mutation executed.",
            )

        # Execute the actions
        from backend.mutation_tickets import MutationTicketManager

        snapshot_before: dict[str, dict[str, Any]] = {}
        for action in actions:
            if action.get("action") == "update_node_property" and action.get("id"):
                node = graph_db.get_node(action["id"])
                if node:
                    snapshot_before[action["id"]] = dict(node["properties"])

        try:
            self._apply_actions(graph_db, actions)
        except Exception as e:
            return PlanPhaseResult(
                success=False,
                error=f"mutation failed: {e!s}",
                output="plan_and_act: 执行失败。" if is_zh else "plan_and_act: Execution failed.",
            )

        ticket = MutationTicketManager(graph_db, soft_window_seconds=self._soft_window_seconds).record(
            session_id=plan.rationale or "plan-act",
            forward_actions=actions,
            snapshot_before=snapshot_before,
        )

        try:
            from backend.graph_subscription import subscription_manager

            async def _send_ws(ws, payload):
                try:
                    await ws.send_json(payload)
                except Exception:
                    pass

            await subscription_manager.broadcast_updates(graph_db, _send_ws)
        except Exception:
            pass

        try:
            await on_update(
                {
                    "type": "mutation_preview",
                    "ticket_id": ticket.ticket_id,
                    "session_id": ticket.session_id,
                    "actions": actions,
                    "summary": self._render_plan(actions, language),
                    "soft_window_seconds": int(self._soft_window_seconds),
                }
            )
        except Exception:
            pass

        output_msg = f"plan_and_act: 完成 {len(actions)} 步 mutation。{self._render_plan(actions, language)}" if is_zh else f"plan_and_act: Completed {len(actions)} step(s) of mutation. {self._render_plan(actions, language)}"
        return PlanPhaseResult(
            success=True,
            output=output_msg,
            extra={"ticket_id": ticket.ticket_id, "approved_plan": approved_plan},
        )

    @staticmethod
    def _apply_actions(graph_db, actions: list[dict[str, Any]]) -> None:
        for action in actions:
            act = action.get("action")
            if act == "create_node":
                graph_db.create_node(
                    node_id=action.get("id"),
                    node_type=action.get("type", "Generic"),
                    properties=action.get("properties"),
                )
            elif act == "update_node_property":
                graph_db.update_node_property(
                    node_id=action.get("id"),
                    properties=action.get("properties", {}),
                )
            elif act == "delete_node":
                graph_db.delete_node(node_id=action.get("id"))
            elif act == "create_edge":
                graph_db.create_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type"),
                    properties=action.get("properties"),
                )
            elif act == "delete_edge":
                graph_db.delete_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type"),
                )

    @staticmethod
    def _render_plan(actions: list[dict[str, Any]], language: str = "zh") -> str:
        lines = []
        is_zh = language == "zh"
        for a in actions:
            act = a.get("action")
            if act == "create_node":
                title = (a.get("properties") or {}).get("title") or a.get("id")
                node_type = a.get("type", "节点") if is_zh else a.get("type", "node")
                lines.append(f"+ 创建 {node_type}: {title}" if is_zh else f"+ Create {node_type}: {title}")
            elif act == "update_node_property":
                lines.append(f"~ 更新节点 `{a.get('id')}`" if is_zh else f"~ Update node `{a.get('id')}`")
            elif act == "delete_node":
                lines.append(f"- 删除节点 `{a.get('id')}`" if is_zh else f"- Delete node `{a.get('id')}`")
            elif act == "create_edge":
                lines.append(f"+ 关联 {a.get('from_id')} -> {a.get('to_id')}" if is_zh else f"+ Link {a.get('from_id')} -> {a.get('to_id')}")
            elif act == "delete_edge":
                lines.append(f"- 删除关联 {a.get('from_id')} -> {a.get('to_id')}" if is_zh else f"- Delete link {a.get('from_id')} -> {a.get('to_id')}")
        if not lines:
            return "（无动作）" if is_zh else "(No actions)"
        return ("计划：\n  - " if is_zh else "Plan:\n  - ") + "\n  - ".join(lines)
