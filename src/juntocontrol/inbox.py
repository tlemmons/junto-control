from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from .mcp_client import MCPClient, _unwrap_tool_result

log = structlog.get_logger(__name__)

POLL_INTERVAL_SEC = 2.0
POLL_BACKOFF_MAX_SEC = 30.0
DEFAULT_FETCH_LIMIT = 50


@dataclass(frozen=True)
class InboxKey:
    """Identifies a single inbox stream we're watching."""
    project: str
    agent: str

    def uri(self) -> str:
        return f"inbox://{self.project}/{self.agent}"


@dataclass
class InboxStreamState:
    last_cursor: str | None = None
    last_message_id: str | None = None
    last_polled_at: float | None = None
    consecutive_errors: int = 0
    mode: str = "poll"  # "poll" | "subscribe"
    notify: asyncio.Queue[None] | None = None


@dataclass
class InboxEvent:
    """A new-message event broadcast to subscribers."""
    key: InboxKey
    message: dict[str, Any]
    received_at: float = field(default_factory=time.time)


@dataclass(eq=False)
class Subscriber:
    """A connected browser tab. queue is async-aware; closed flag terminates pumping."""
    queue: asyncio.Queue[InboxEvent] = field(default_factory=asyncio.Queue)
    project_filter: str | None = None
    closed: bool = False


class InboxBroker:
    """
    Polls memory_get_messages for each watched (project, agent) tuple, tracks
    per-stream cursors, and fans new messages out to in-process subscribers
    (WebSocket-connected browser tabs).

    Designed so that swapping to resources/subscribe is a single method
    (_run_stream) change — the broker fan-out and subscriber API are
    transport-agnostic.
    """

    def __init__(self, client: MCPClient) -> None:
        self._client = client
        self._streams: dict[InboxKey, InboxStreamState] = {}
        self._stream_tasks: dict[InboxKey, asyncio.Task[None]] = {}
        self._uri_to_key: dict[str, InboxKey] = {}
        self._subscribers: set[Subscriber] = set()
        self._lock = asyncio.Lock()
        self._stopping = False

    @property
    def watched_keys(self) -> list[InboxKey]:
        return list(self._streams.keys())

    async def list_agents_in_project(self, project: str) -> list[dict[str, Any]]:
        result = await self._client.call("memory_list_agents", project=project)
        payload = _unwrap_tool_result(result)
        return list(payload.get("agents", []))

    async def watch_project(self, project: str) -> list[InboxKey]:
        """Set the watched-stream set to every agent currently in `project`."""
        agents = await self.list_agents_in_project(project)
        keys = {InboxKey(project=project, agent=a["instance"]) for a in agents}
        async with self._lock:
            current = set(self._streams.keys())
            to_add = keys - current
            to_remove = current - keys
            for k in to_remove:
                await self._stop_stream(k)
            for k in to_add:
                await self._start_stream(k)
        log.info("watch_project", project=project, watched=len(keys))
        return sorted(keys, key=lambda k: k.agent)

    async def stop_all(self) -> None:
        self._stopping = True
        async with self._lock:
            for k in list(self._streams.keys()):
                await self._stop_stream(k)
        for sub in list(self._subscribers):
            sub.closed = True

    def subscribe(self, project_filter: str | None = None) -> Subscriber:
        sub = Subscriber(project_filter=project_filter)
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        sub.closed = True
        self._subscribers.discard(sub)

    async def _start_stream(self, key: InboxKey) -> None:
        if key in self._stream_tasks:
            return
        self._streams[key] = InboxStreamState()
        self._uri_to_key[key.uri()] = key
        self._stream_tasks[key] = asyncio.create_task(self._run_stream(key))

    async def _stop_stream(self, key: InboxKey) -> None:
        state = self._streams.pop(key, None)
        task = self._stream_tasks.pop(key, None)
        self._uri_to_key.pop(key.uri(), None)
        if state is not None and state.mode == "subscribe":
            with contextlib.suppress(Exception):
                await self._client.unsubscribe_inbox(key.uri())
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def on_inbox_notification(self, uri: str) -> None:
        """MCPClient notification dispatch entry. Wakes the matching stream."""
        key = self._uri_to_key.get(uri)
        if key is None:
            return
        state = self._streams.get(key)
        if state is None or state.notify is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            state.notify.put_nowait(None)

    async def on_reconnect(self) -> None:
        """MCPClient reconnect dispatch entry. Re-subscribe every subscribe-mode stream."""
        for key, state in list(self._streams.items()):
            if state.mode != "subscribe":
                continue
            try:
                await self._client.subscribe_inbox(key.uri())
                log.info("inbox_resubscribed", key=str(key))
            except Exception as exc:
                log.warning("inbox_resubscribe_failed", key=str(key), error=str(exc))

    async def _run_stream(self, key: InboxKey) -> None:
        """Drive one inbox URI via subscribe (preferred) or polling (fallback)."""
        state = self._streams[key]
        # Bootstrap: fetch the most recent page once and treat it as baseline so
        # we don't re-emit pre-existing messages on start.
        await self._bootstrap(key, state)
        if self._subscribe_supported() and await self._try_subscribe(key, state):
            await self._run_subscribe_loop(key, state)
            return
        await self._run_poll_loop(key, state)

    def _subscribe_supported(self) -> bool:
        caps = getattr(self._client, "capabilities", None)
        return bool(getattr(caps, "inbox_resource_supported", False))

    async def _try_subscribe(self, key: InboxKey, state: InboxStreamState) -> bool:
        try:
            await self._client.subscribe_inbox(key.uri())
        except Exception as exc:
            log.warning("inbox_subscribe_failed_falling_back", key=str(key), error=str(exc))
            return False
        state.mode = "subscribe"
        state.notify = asyncio.Queue(maxsize=64)
        log.info("inbox_subscribed", key=str(key))
        return True

    async def _run_subscribe_loop(self, key: InboxKey, state: InboxStreamState) -> None:
        assert state.notify is not None
        while not self._stopping:
            try:
                await state.notify.get()
                if self._stopping:
                    return
                await self._fetch_via_resource(key, state)
                state.consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.consecutive_errors += 1
                log.warning(
                    "inbox_subscribe_fetch_error",
                    key=str(key),
                    error=str(exc),
                    consecutive=state.consecutive_errors,
                )
                # If the session has dropped, on_reconnect will re-subscribe
                # us; just back off briefly and continue waiting on the queue.
                await asyncio.sleep(min(2 ** state.consecutive_errors, POLL_BACKOFF_MAX_SEC))

    async def _run_poll_loop(self, key: InboxKey, state: InboxStreamState) -> None:
        state.mode = "poll"
        while not self._stopping:
            try:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                await self._poll_once(key, state)
                state.consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.consecutive_errors += 1
                backoff = min(
                    POLL_INTERVAL_SEC * (2**state.consecutive_errors),
                    POLL_BACKOFF_MAX_SEC,
                )
                log.warning(
                    "inbox_poll_error",
                    key=str(key),
                    error=str(exc),
                    consecutive=state.consecutive_errors,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)

    async def _bootstrap(self, key: InboxKey, state: InboxStreamState) -> None:
        try:
            messages = await self._fetch(key, since_iso=None)
            if messages:
                # Newest first per server convention; remember the newest seen.
                newest = messages[0]
                state.last_message_id = newest.get("id")
                state.last_cursor = newest.get("created")
            state.last_polled_at = time.time()
            log.info(
                "inbox_bootstrapped",
                key=str(key),
                seeded_from=state.last_message_id,
            )
        except Exception as exc:
            log.warning("inbox_bootstrap_failed", key=str(key), error=str(exc))

    async def _poll_once(self, key: InboxKey, state: InboxStreamState) -> None:
        messages = await self._fetch(key, since_iso=None)
        state.last_polled_at = time.time()
        await self._diff_and_broadcast(key, state, messages)

    async def _fetch_via_resource(self, key: InboxKey, state: InboxStreamState) -> None:
        payload = await self._client.read_resource(key.uri())
        state.last_polled_at = time.time()
        await self._diff_and_broadcast(key, state, list(payload.get("messages", [])))

    async def _diff_and_broadcast(
        self,
        key: InboxKey,
        state: InboxStreamState,
        messages: list[dict[str, Any]],
    ) -> None:
        if not messages:
            return
        # Server returns newest first. We deliver in oldest→newest order
        # so subscribers see them in send order.
        new_messages: list[dict[str, Any]] = []
        for msg in messages:
            mid = msg.get("id")
            if mid is None:
                continue
            if mid == state.last_message_id:
                break
            new_messages.append(msg)
        if not new_messages:
            return
        new_messages.reverse()
        state.last_message_id = new_messages[-1].get("id")
        state.last_cursor = new_messages[-1].get("created")
        for msg in new_messages:
            await self._broadcast(InboxEvent(key=key, message=msg))

    async def _fetch(self, key: InboxKey, since_iso: str | None) -> list[dict[str, Any]]:
        """Pull the agent's inbox via memory_get_messages."""
        kwargs: dict[str, Any] = {
            "for_instance": key.agent,
            "limit": DEFAULT_FETCH_LIMIT,
            "include_delivered": True,
        }
        # NOTE: memory_get_messages doesn't filter by project on the read side
        # (an agent has one inbox). The for_instance scoping is sufficient.
        if since_iso:
            kwargs["cursor"] = since_iso
        result = await self._client.call("memory_get_messages", **kwargs)
        payload = _unwrap_tool_result(result)
        return list(payload.get("messages", []))

    async def _broadcast(self, event: InboxEvent) -> None:
        for sub in list(self._subscribers):
            if sub.closed:
                self._subscribers.discard(sub)
                continue
            if sub.project_filter and sub.project_filter != event.key.project:
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("subscriber_queue_full_dropping", key=str(event.key))
