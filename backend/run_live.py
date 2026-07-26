"""Bounded in-process fan-out for ephemeral Run progress.

Live events intentionally never enter RunStore. Durable step and business
events remain the replay/recovery source of truth.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class RunLiveBroker:
    """Fan out session-scoped live events without blocking the producer."""

    def __init__(self, *, queue_size: int = 128) -> None:
        if queue_size < 1:
            raise ValueError("Live Run subscriber queue must be positive")
        self.queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        if not session_id:
            raise ValueError("Live Run subscription requires a session ID")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(session_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(session_id, None)

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        """Best-effort publish; slow clients lose old ephemeral deltas."""

        for queue in tuple(self._subscribers.get(session_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - guarded by full()
                    pass
            try:
                queue.put_nowait(dict(event))
            except asyncio.QueueFull:  # pragma: no cover - one event loop owns each queue
                pass

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subscribers.get(session_id, ()))
