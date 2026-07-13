"""Runtime DAG for widget builds.

Replaces the explicit ``current_state while`` state machine in
``AgentOrchestrator.handle_message``. The DAG itself is *linear* by default
(plan → align_schemas → code → verify); it does not branch. User interrupts
(rework_schema / rework_code / rework_plan / per-field extension) feed into
two extra nodes — ``decode_user_intent`` and ``apply_user_actions`` — that
interpret the user's feedback via an LLM and re-mark downstream nodes dirty.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.dag")


@dataclass
class TaskResult:
    success: bool = True
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    ask_user: dict[str, Any] | None = None  # {payload: dict, future: asyncio.Future}
    # Optional: after running this task, mark these nodes dirty too. Used when
    # a phase changes an artifact that downstream phases depend on.
    invalidates_if_redo: set[str] = field(default_factory=set)


@dataclass
class TaskNode:
    name: str
    run: Callable[[TaskContext], Awaitable[TaskResult]]
    needs_outputs_from: set[str] = field(default_factory=set)
    # If this node re-runs, downstream nodes listed here will be marked dirty
    # automatically (so they re-execute on the next loop).
    invalidates: set[str] = field(default_factory=set)


@dataclass
class TaskContext:
    session_id: str
    app_id: str
    plan_input: Any  # the original IntentPlan
    extra: dict[str, Any] = field(default_factory=dict)


class WidgetDAG:
    """Linear DAG runner.

    Tasks are registered once. Calling ``dirty(*names)`` marks them for
    execution on the next ``step()``. Each ``step()`` runs the next dirty task
    and returns its result; ``idle()`` is True when no task is dirty.

    A ``TaskResult`` can carry an ``ask_user`` payload — the harness sees this
    and yields to the user; when the user responds, the harness invokes
    ``handle_user_response`` to dirty further nodes.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}
        self._order: list[str] = []  # topological execution order
        self._dirty: set[str] = set()

    # ----- registration ---------------------------------------------------

    def register(self, node: TaskNode) -> None:
        if node.name in self._nodes:
            raise ValueError(f"node already registered: {node.name}")
        self._nodes[node.name] = node
        self._order.append(node.name)
        self._dirty.add(node.name)

    def dirty(self, *names: str) -> None:
        for n in names:
            if n not in self._nodes:
                raise KeyError(f"unknown node: {n}")
            self._dirty.add(n)

    # ----- execution ------------------------------------------------------

    def idle(self) -> bool:
        return not self._dirty

    def pending(self) -> list[str]:
        """Return dirty node names in execution order."""
        return [n for n in self._order if n in self._dirty]

    async def step(self, ctx: TaskContext) -> TaskResult | None:
        """Run the next dirty node in topological order. Returns its result
        or None if idle. After running, the node is removed from the dirty set
        (unless its task marked successors dirty via ``invalidates``)."""
        for name in self._order:
            if name not in self._dirty:
                continue
            node = self._nodes[name]
            logger.debug(f"running dag node: {name}")
            self._dirty.discard(name)
            try:
                result = await node.run(ctx)
            except Exception as e:
                logger.exception(f"dag node {name} raised: {e}")
                return TaskResult(
                    success=False,
                    error=str(e),
                    outputs={"node": name},
                )
            if result.invalidates_if_redo:
                self.dirty(*result.invalidates_if_redo)
            return result
        return None

    # ----- helpers --------------------------------------------------------

    def reset(self) -> None:
        self._dirty = set(self._nodes.keys())
