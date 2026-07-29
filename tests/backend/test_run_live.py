from __future__ import annotations

import asyncio

import pytest

from backend.run_live import RunLiveBroker


@pytest.mark.asyncio
async def test_live_broker_is_session_scoped_and_drops_oldest_for_slow_subscribers() -> None:
    broker = RunLiveBroker(queue_size=2)
    first = broker.subscribe("session-one")
    second = broker.subscribe("session-two")

    broker.publish("session-one", {"chunk_sequence": 1})
    broker.publish("session-one", {"chunk_sequence": 2})
    broker.publish("session-one", {"chunk_sequence": 3})

    assert (await first.get())["chunk_sequence"] == 2
    assert (await first.get())["chunk_sequence"] == 3
    assert second.empty()
    assert broker.subscriber_count("session-one") == 1

    broker.unsubscribe("session-one", first)
    broker.unsubscribe("session-two", second)
    assert broker.subscriber_count("session-one") == 0
    assert broker.subscriber_count("session-two") == 0


@pytest.mark.asyncio
async def test_live_broker_fans_out_independent_event_copies() -> None:
    broker = RunLiveBroker()
    first = broker.subscribe("session-one")
    second = broker.subscribe("session-one")
    event = {"delta": "hello"}

    broker.publish("session-one", event)
    first_event = await asyncio.wait_for(first.get(), timeout=0.1)
    second_event = await asyncio.wait_for(second.get(), timeout=0.1)
    first_event["delta"] = "changed"

    assert second_event["delta"] == "hello"
