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
        self._stream_tasks[key] = asyncio.create_task(self._run_stream(key))

    async def _stop_stream(self, key: InboxKey) -> None:
        task = self._stream_tasks.pop(key, None)
        self._streams.pop(key, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _run_stream(self, key: InboxKey) -> None:
        """Poll loop for one inbox URI. Lives until cancelled or broker stops."""
        state = self._streams[key]
        # Bootstrap: first poll fetches the most recent page and treats it as
        # the baseline (don't fire events for pre-existing messages on start).
        await self._bootstrap(key, state)
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
