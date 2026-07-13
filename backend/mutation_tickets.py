"""Mutation ticket manager — tracks graph mutations so users can preview and undo.

Design points
-------------
* Every graph_mutation (or plan_and_act mutation step) is wrapped in a
  ``MutationTicket``.
* A ticket stays in memory for ``soft_window_seconds`` (default 60s). After that
  the in-memory copy is dropped; if the user clicked "⭐" before expiry, the
  ticket is also persisted to ``graph_mutation_history`` and continues to be
  rollback-able for the lifetime of the workspace.
* Rolling back a ticket returns a list of inverse actions the caller can apply
  to revert the change.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.graph_db import GraphDatabase

logger = logging.getLogger("mutation_tickets")


@dataclass
class MutationTicket:
    ticket_id: str
    session_id: str
    forward_actions: list[dict[str, Any]]
    reverse_actions: list[dict[str, Any]] = field(default_factory=list)
    snapshot_before: dict[str, dict[str, Any]] = field(default_factory=dict)
    pinned: bool = False
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "session_id": self.session_id,
            "forward_actions": list(self.forward_actions),
            "reverse_actions": list(self.reverse_actions),
            "snapshot_before": dict(self.snapshot_before),
            "pinned": self.pinned,
            "created_at": self.created_at,
        }


def compute_reverse_actions(
    forward: list[dict[str, Any]],
    snapshot_before: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Translate forward actions into reverse actions where possible."""
    snapshot_before = snapshot_before or {}
    reverses: list[dict[str, Any]] = []
    for action in forward:
        act = action.get("action")
        if act == "create_node":
            reverses.append({"action": "delete_node", "id": action.get("id")})
        elif act == "create_edge":
            reverses.append(
                {
                    "action": "delete_edge",
                    "from_id": action.get("from_id"),
                    "to_id": action.get("to_id"),
                    "type": action.get("type"),
                }
            )
        elif act == "update_node_property":
            node_id = action.get("id")
            old_props = snapshot_before.get(node_id, {})
            revert_props: dict[str, Any] = {}
            for k in (action.get("properties") or {}).keys():
                if k in old_props:
                    revert_props[k] = old_props[k]
            reverses.append(
                {
                    "action": "update_node_property",
                    "id": node_id,
                    "properties": revert_props,
                }
            )
        elif act == "delete_node":
            # Reverse requires snapshot; skip if no snapshot was captured
            pass
        elif act == "delete_edge":
            pass
        else:
            logger.warning(f"Unknown action in compute_reverse_actions: {act!r}")
    return reverses


class MutationTicketManager:
    """In-memory + DB-backed ticket manager.

    Lifecycle:
    - ``record(session_id, forward_actions, snapshot_before)`` produces a ticket
      kept in memory for ``soft_window_seconds`` (default 60) and persisted to
      the DB initially as ``pinned=0``.
    - ``pin`` flips the DB row to ``pinned=1`` so it remains rollback-able past
      the soft window.
    - ``rollback`` produces inverse actions (looked up from memory if available,
      otherwise from the persistent history).
    """

    def __init__(
        self,
        graph_db: GraphDatabase,
        soft_window_seconds: float = 60.0,
    ):
        self._graph_db = graph_db
        self._soft_window = soft_window_seconds
        # session_id -> ticket_id -> ticket (in memory)
        self._in_memory: dict[str, dict[str, MutationTicket]] = defaultdict(dict)
        self._soft_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def record(
        self,
        session_id: str,
        forward_actions: list[dict[str, Any]],
        snapshot_before: dict[str, dict[str, Any]] | None = None,
        ticket_id: str | None = None,
        reverse_actions: list[dict[str, Any]] | None = None,
    ) -> MutationTicket:
        ticket_id = ticket_id or f"tkt-{uuid.uuid4().hex[:12]}"
        reverse = (
            reverse_actions
            if reverse_actions is not None
            else compute_reverse_actions(forward_actions, snapshot_before)
        )
        ticket = MutationTicket(
            ticket_id=ticket_id,
            session_id=session_id,
            forward_actions=list(forward_actions),
            reverse_actions=list(reverse),
            snapshot_before=dict(snapshot_before or {}),
            pinned=False,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._in_memory[session_id][ticket_id] = ticket

        # Persist the ticket (unpinned) immediately so we have a record
        self._graph_db.record_mutation_history(
            ticket_id=ticket.ticket_id,
            session_id=session_id,
            forward_actions=ticket.forward_actions,
            reverse_actions=ticket.reverse_actions,
            snapshot_before=ticket.snapshot_before,
            pinned=False,
        )

        # Schedule soft expiry
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._soft_expire(session_id, ticket.ticket_id))
        self._soft_tasks[ticket.ticket_id] = task

        return ticket

    def get(self, session_id: str, ticket_id: str) -> MutationTicket | None:
        return self._in_memory.get(session_id, {}).get(ticket_id)

    async def _soft_expire(self, session_id: str, ticket_id: str) -> None:
        try:
            await asyncio.sleep(self._soft_window)
        except asyncio.CancelledError:
            return
        # Drop from in-memory; persistent record remains (unpinned) for visibility
        self._in_memory.get(session_id, {}).pop(ticket_id, None)
        if not self._in_memory.get(session_id):
            self._in_memory.pop(session_id, None)
        self._soft_tasks.pop(ticket_id, None)

    async def pin(self, session_id: str, ticket_id: str) -> bool:
        async with self._lock:
            ticket = self.get(session_id, ticket_id)
            if ticket is not None:
                ticket.pinned = True
            # Always mark in DB
            self._graph_db.pin_mutation_history(ticket_id)
            # Cancel the soft expiry since the ticket is now permanent
            task = self._soft_tasks.pop(ticket_id, None)
            if task is not None and not task.done():
                task.cancel()
        return True

    async def rollback(self, session_id: str, ticket_id: str) -> list[dict[str, Any]]:
        """Return the inverse actions for a ticket, consulting memory then DB."""
        # Try in-memory first
        in_mem = self.get(session_id, ticket_id)
        if in_mem is not None:
            return list(in_mem.reverse_actions)
        # Fall back to persistent record
        row = self._graph_db.load_mutation_history(ticket_id)
        if row is None:
            return []
        return list(row.get("reverse_actions") or [])
