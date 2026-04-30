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
    fake_client.capabilities = SimpleNamespace(inbox_resource_supported=False)
    broker = InboxBroker(fake_client)
    keys = await broker.watch_project("nimbus")
    agent_names = {k.agent for k in keys}
    assert agent_names == {"alpha", "beta"}
    # Tasks were created — cancel them so the test doesn't leak coroutines.
    for k in list(broker._stream_tasks.keys()):
        await broker._stop_stream(k)
    # Drain any pending task cancellations.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_subscribe_path_emits_on_notification(fake_client: Any) -> None:
    """When inbox_resource_supported, broker subscribes and reads on notifications."""
    fake_client.capabilities = SimpleNamespace(inbox_resource_supported=True)
    fake_client.subscribe_inbox = AsyncMock()
    fake_client.unsubscribe_inbox = AsyncMock()
    fake_client.read_resource = AsyncMock(
        return_value={"messages": [_msg("m2"), _msg("m1")]}
    )
    # Bootstrap call goes through .call("memory_get_messages", ...) and returns m1 only,
    # so when the resource read later sees m2+m1, only m2 is new.
    fake_client.call.return_value = _wrap({"messages": [_msg("m1")]})

    broker = InboxBroker(fake_client)
    sub = broker.subscribe()
    key = InboxKey("nimbus", "main")
    async with asyncio.timeout(2):
        await broker._start_stream(key)
        # Let the bootstrap + subscribe happen.
        for _ in range(10):
            state = broker._streams[key]
            if state.mode == "subscribe" and state.notify is not None:
                break
            await asyncio.sleep(0.01)
        assert state.mode == "subscribe"
        fake_client.subscribe_inbox.assert_awaited_with("inbox://nimbus/main")
        # Server pushes a notification → broker should fetch and emit m2 only.
        await broker.on_inbox_notification("inbox://nimbus/main")
        for _ in range(20):
            if not sub.queue.empty():
                break
            await asyncio.sleep(0.01)
        assert sub.queue.qsize() == 1
        event = sub.queue.get_nowait()
        assert event.message["id"] == "m2"
        await broker._stop_stream(key)
    fake_client.unsubscribe_inbox.assert_awaited_with("inbox://nimbus/main")


@pytest.mark.asyncio
async def test_subscribe_failure_falls_back_to_poll(fake_client: Any) -> None:
    """If subscribe raises, broker degrades to poll mode for that stream."""
    fake_client.capabilities = SimpleNamespace(inbox_resource_supported=True)
    fake_client.subscribe_inbox = AsyncMock(side_effect=RuntimeError("nope"))
    fake_client.unsubscribe_inbox = AsyncMock()
    fake_client.call.return_value = _wrap({"messages": []})

    broker = InboxBroker(fake_client)
    key = InboxKey("nimbus", "main")
    async with asyncio.timeout(2):
        await broker._start_stream(key)
        for _ in range(10):
            state = broker._streams[key]
            if state.mode == "poll":
                break
            await asyncio.sleep(0.01)
        assert state.mode == "poll"
        await broker._stop_stream(key)


@pytest.mark.asyncio
async def test_on_reconnect_resubscribes_subscribe_streams(fake_client: Any) -> None:
    fake_client.capabilities = SimpleNamespace(inbox_resource_supported=True)
    fake_client.subscribe_inbox = AsyncMock()

    broker = InboxBroker(fake_client)
    key_a = InboxKey("nimbus", "alpha")
    key_b = InboxKey("ha", "sage")
    broker._streams[key_a] = type(broker._streams[key_a] if broker._streams else None) or None  # noqa: E501
    # Simulate two streams already running: one subscribe, one poll.
    from claudecontrol.inbox import InboxStreamState

    broker._streams = {
        key_a: InboxStreamState(mode="subscribe", notify=asyncio.Queue()),
        key_b: InboxStreamState(mode="poll"),
    }
    broker._uri_to_key = {key_a.uri(): key_a, key_b.uri(): key_b}

    await broker.on_reconnect()
    # Only the subscribe-mode stream should have re-issued subscribe_inbox.
    fake_client.subscribe_inbox.assert_awaited_once_with("inbox://nimbus/alpha")
