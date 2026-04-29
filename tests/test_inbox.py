from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from claudecontrol.inbox import InboxBroker, InboxKey


def _wrap(payload: dict[str, Any]) -> SimpleNamespace:
    """Mimic the MCP CallToolResult shape that _unwrap_tool_result expects."""
    text = json.dumps({"result": json.dumps(payload)})
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _msg(mid: str, created: str = "2026-04-29T15:00:00Z") -> dict[str, Any]:
    return {
        "id": mid,
        "from": "tester",
        "from_project": "claudecontrol",
        "category": "info",
        "message": f"hello from {mid}",
        "priority": "normal",
        "status": "pending",
        "created": created,
    }


@pytest.fixture
def fake_client() -> Any:
    client = SimpleNamespace()
    client.call = AsyncMock()
    return client


def test_inbox_key_uri() -> None:
    assert InboxKey("nimbus", "main").uri() == "inbox://nimbus/main"


@pytest.mark.asyncio
async def test_list_agents_in_project(fake_client: Any) -> None:
    fake_client.call.return_value = _wrap({"agents": [{"instance": "alpha"}, {"instance": "beta"}]})
    broker = InboxBroker(fake_client)
    agents = await broker.list_agents_in_project("nimbus")
    assert [a["instance"] for a in agents] == ["alpha", "beta"]
    fake_client.call.assert_awaited_with("memory_list_agents", project="nimbus")


@pytest.mark.asyncio
async def test_poll_emits_only_new_messages(fake_client: Any) -> None:
    """First poll seeds baseline; second poll fires only the genuinely new message."""
    # Bootstrap returns 2 messages; broker should remember the newest as baseline.
    bootstrap = _wrap({"messages": [_msg("m2"), _msg("m1")]})
    # Next poll returns 3 messages — m3 is new.
    poll = _wrap({"messages": [_msg("m3"), _msg("m2"), _msg("m1")]})
    fake_client.call.side_effect = [bootstrap, poll]

    from claudecontrol.inbox import InboxStreamState

    broker = InboxBroker(fake_client)
    sub = broker.subscribe()
    key = InboxKey("nimbus", "main")
    s = InboxStreamState()
    broker._streams[key] = s
    await broker._bootstrap(key, s)
    assert s.last_message_id == "m2"
    await broker._poll_once(key, s)
    assert s.last_message_id == "m3"
    # Subscriber should have exactly one event (m3), not m1/m2.
    assert sub.queue.qsize() == 1
    event = sub.queue.get_nowait()
    assert event.message["id"] == "m3"


@pytest.mark.asyncio
async def test_subscriber_project_filter(fake_client: Any) -> None:
    broker = InboxBroker(fake_client)
    sub_nimbus = broker.subscribe(project_filter="nimbus")
    sub_all = broker.subscribe()

    from claudecontrol.inbox import InboxEvent

    await broker._broadcast(InboxEvent(InboxKey("nimbus", "x"), _msg("a")))
    await broker._broadcast(InboxEvent(InboxKey("ha", "y"), _msg("b")))

    assert sub_nimbus.queue.qsize() == 1
    assert sub_all.queue.qsize() == 2
    assert sub_nimbus.queue.get_nowait().message["id"] == "a"


@pytest.mark.asyncio
async def test_unsubscribe_drops_subscriber(fake_client: Any) -> None:
    broker = InboxBroker(fake_client)
    sub = broker.subscribe()
    broker.unsubscribe(sub)
    assert sub.closed
    assert sub not in broker._subscribers


@pytest.mark.asyncio
async def test_watch_project_diffs_set(fake_client: Any) -> None:
    fake_client.call.return_value = _wrap({"agents": [{"instance": "alpha"}, {"instance": "beta"}]})
    broker = InboxBroker(fake_client)
    keys = await broker.watch_project("nimbus")
    agent_names = {k.agent for k in keys}
    assert agent_names == {"alpha", "beta"}
    # Tasks were created — cancel them so the test doesn't leak coroutines.
    for k in list(broker._stream_tasks.keys()):
        await broker._stop_stream(k)
    # Drain any pending task cancellations.
    await asyncio.sleep(0)
